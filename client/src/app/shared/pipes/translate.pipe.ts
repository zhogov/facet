import { Pipe, PipeTransform, inject } from '@angular/core';
import { I18nService } from '../../core/services/i18n.service';

@Pipe({ name: 'translate', pure: false })
export class TranslatePipe implements PipeTransform {
  private i18n = inject(I18nService);

  private lastKey: string | null = null;
  private lastVarsHash = '';
  private lastTranslations: Record<string, unknown> | null = null;
  private cachedValue = '';

  transform(key: string, vars?: Record<string, string | number>): string {
    const translations = this.i18n.translations();
    const varsHash = vars ? JSON.stringify(vars) : '';

    if (
      key === this.lastKey &&
      varsHash === this.lastVarsHash &&
      translations === this.lastTranslations
    ) {
      return this.cachedValue;
    }

    this.cachedValue = this.i18n.t(key, vars);
    this.lastKey = key;
    this.lastVarsHash = varsHash;
    this.lastTranslations = translations;
    return this.cachedValue;
  }
}
