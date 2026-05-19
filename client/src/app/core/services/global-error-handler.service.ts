import { ErrorHandler, Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

/**
 * Self-hosted crash sink. Catches anything Angular hands to ErrorHandler
 * (unhandled effect / template / async errors) and POSTs a minimal report
 * to ``/api/client-errors``. The backend logs and forgets — no third-party
 * service. Errors that fail to send are still logged to the console so we
 * never silently swallow them.
 */
@Injectable({ providedIn: 'root' })
export class GlobalErrorHandler implements ErrorHandler {
  private readonly http = inject(HttpClient);
  private inFlight = 0;
  private readonly MAX_INFLIGHT = 5;

  handleError(error: unknown): void {
    console.error(error);
    if (this.inFlight >= this.MAX_INFLIGHT) return;
    this.inFlight++;
    const payload = this.buildPayload(error);
    firstValueFrom(this.http.post('/api/client-errors', payload, {
      headers: { 'Content-Type': 'application/json' },
    })).catch(() => {
      // Network errors here are expected (e.g. the backend itself crashed).
      // We already console.error'd above; nothing else to do.
    }).finally(() => {
      this.inFlight--;
    });
  }

  private buildPayload(error: unknown): Record<string, unknown> {
    let message = String(error);
    let stack: string | undefined;
    let name: string | undefined;
    if (error instanceof Error) {
      message = error.message;
      stack = error.stack;
      name = error.name;
    } else if (typeof error === 'object' && error !== null) {
      const e = error as Record<string, unknown>;
      message = String(e['message'] ?? e['statusText'] ?? error);
      stack = typeof e['stack'] === 'string' ? e['stack'] : undefined;
      name = typeof e['name'] === 'string' ? e['name'] : undefined;
    }
    return {
      message: message.slice(0, 2000),
      name: name?.slice(0, 200),
      stack: stack?.slice(0, 8000),
      url: typeof window !== 'undefined' ? window.location.href : null,
      user_agent: typeof navigator !== 'undefined' ? navigator.userAgent : null,
      ts: new Date().toISOString(),
    };
  }
}
