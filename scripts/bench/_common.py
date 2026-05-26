"""Shared primitives for the Facet benchmark harness.

Keep this module dependency-light (httpx + stdlib) so the harness runs in
any virtualenv with the project requirements installed.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "bench" / "results"


@dataclass
class Sample:
    """One request observation."""

    label: str
    latency_ms: float
    status: int
    error: str | None = None


@dataclass
class SuiteRun:
    """All observations for one suite invocation."""

    suite: str
    base_url: str
    branch: str
    commit: str
    started_at: str
    concurrency: int
    warmup: int
    requests_per_endpoint: int
    samples: list[Sample] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def repo_branch() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(REPO_ROOT),
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip() or "detached"
    except Exception:
        return "unknown"


def repo_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT),
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def results_path(suite: str, branch: str | None = None) -> Path:
    branch = (branch or repo_branch()).replace("/", "_")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR / f"{utc_stamp()}-{branch}-{suite}.json"


async def hit(
    client: httpx.AsyncClient,
    label: str,
    method: str,
    path: str,
    **kwargs: Any,
) -> Sample:
    t0 = time.perf_counter()
    try:
        resp = await client.request(method, path, timeout=30.0, **kwargs)
        await resp.aread()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return Sample(
            label=label,
            latency_ms=elapsed_ms,
            status=resp.status_code,
            error=None if resp.status_code < 400 else f"http_{resp.status_code}",
        )
    except httpx.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return Sample(label=label, latency_ms=elapsed_ms, status=0, error=str(exc))


async def run_suite(
    base_url: str,
    endpoints: list[tuple[str, str, str, dict]],
    *,
    concurrency: int,
    warmup: int,
    requests_per_endpoint: int,
    auth_token: str | None = None,
) -> list[Sample]:
    """Hit each endpoint ``requests_per_endpoint`` times at ``concurrency``.

    ``endpoints`` is a list of (label, method, path, kwargs) tuples. ``kwargs``
    are forwarded to ``httpx.AsyncClient.request`` (typically ``params``).
    """

    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )
    samples: list[Sample] = []
    async with httpx.AsyncClient(
        base_url=base_url,
        limits=limits,
        headers=headers,
        http2=False,
    ) as client:
        if warmup > 0:
            for label, method, path, kwargs in endpoints:
                for _ in range(warmup):
                    await hit(client, label, method, path, **kwargs)

        sem = asyncio.Semaphore(concurrency)

        async def fire(label: str, method: str, path: str, kwargs: dict) -> None:
            async with sem:
                samples.append(await hit(client, label, method, path, **kwargs))

        tasks = []
        for label, method, path, kwargs in endpoints:
            for _ in range(requests_per_endpoint):
                tasks.append(fire(label, method, path, kwargs))

        await asyncio.gather(*tasks)
    return samples


def summarize(label: str, samples: Iterable[Sample]) -> dict[str, Any]:
    good = [s.latency_ms for s in samples if s.error is None]
    bad = sum(1 for s in samples if s.error is not None)
    total = good_len = len(good)
    if total == 0:
        return {
            "label": label,
            "n": 0,
            "errors": bad,
            "min": None,
            "p50": None,
            "p95": None,
            "p99": None,
            "max": None,
            "mean": None,
        }
    good.sort()
    return {
        "label": label,
        "n": good_len,
        "errors": bad,
        "min": round(good[0], 2),
        "p50": round(good[good_len // 2], 2),
        "p95": round(good[min(good_len - 1, int(good_len * 0.95))], 2),
        "p99": round(good[min(good_len - 1, int(good_len * 0.99))], 2),
        "max": round(good[-1], 2),
        "mean": round(statistics.mean(good), 2),
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("(no samples)")
        return
    cols = ["label", "n", "errors", "min", "p50", "p95", "p99", "max", "mean"]
    widths = {
        c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols
    }
    print(" | ".join(c.rjust(widths[c]) for c in cols))
    print("-+-".join("-" * widths[c] for c in cols))
    for r in rows:
        print(" | ".join(str(r.get(c, "")).rjust(widths[c]) for c in cols))


def save_results(
    run: SuiteRun,
    *,
    extra: dict[str, Any] | None = None,
    path: Path | None = None,
) -> Path:
    summary_rows = []
    by_label: dict[str, list[Sample]] = {}
    for s in run.samples:
        by_label.setdefault(s.label, []).append(s)
    for label, entries in by_label.items():
        summary_rows.append(summarize(label, entries))
    out_path = path or results_path(run.suite, run.branch)
    payload = {
        "suite": run.suite,
        "base_url": run.base_url,
        "branch": run.branch,
        "commit": run.commit,
        "started_at": run.started_at,
        "concurrency": run.concurrency,
        "warmup": run.warmup,
        "requests_per_endpoint": run.requests_per_endpoint,
        "summary": summary_rows,
        "metadata": {**run.metadata, **(extra or {})},
        "samples": [
            {
                "label": s.label,
                "latency_ms": round(s.latency_ms, 3),
                "status": s.status,
                "error": s.error,
            }
            for s in run.samples
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path


def env_token() -> str | None:
    """Read JWT from ``FACET_BENCH_TOKEN`` env var. Optional."""
    return os.environ.get("FACET_BENCH_TOKEN") or None
