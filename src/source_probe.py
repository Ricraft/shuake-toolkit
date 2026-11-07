"""Concurrent download-source probing shared by the installer paths.

`CoreManager` first grew this for GitHub release mirrors; the dependency
bootstrap now needs the same behaviour for PyPI mirrors and Playwright CDN
hosts.  Keeping the mechanism here avoids two divergent sampling loops and
makes the behaviour testable without real network access.

Only the standard library is used: ``bootstrap.py`` imports this module before
the virtual environment exists.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeout,
    as_completed,
)

DEFAULT_TIMEOUT = 6.0
DEFAULT_SECONDS = 2.5
DEFAULT_BYTES = 512 * 1024
DEFAULT_DEADLINE = 5.0
DEFAULT_WORKERS = 6
CACHE_SECONDS = 300.0

# Tsinghua's PyPI mirror answers 403 to browser user agents; pip's own agent
# string is what the mirror allow-lists, so probing must impersonate pip.
USER_AGENT = (
    'pip/24.3.1 {"ci":null,"cpython":"3.11",'
    '"implementation":{"name":"CPython","version":"3.11.9"}}'
)

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def format_size(value) -> str:
    """Render a byte count for log lines (``None`` becomes ``未知``)."""
    if value is None:
        return "未知"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _total_size(response, fallback=None):
    """Return the full asset size from ``Content-Range`` or ``Content-Length``."""
    content_range = response.headers.get("Content-Range") or ""
    if "/" in content_range:
        total = content_range.rsplit("/", 1)[1].strip()
        if total.isdigit():
            return int(total)
    content_length = response.headers.get("Content-Length") or ""
    if content_length.strip().isdigit():
        return int(content_length.strip())
    return fallback


def probe_failure(name, url, error):
    """Result record for a source that could not be sampled at all."""
    return {
        "name": name,
        "url": url,
        "reachable": False,
        "speed": 0.0,
        "latency": None,
        "error": error,
    }


def probe_source(
    name,
    url,
    *,
    expected_size=None,
    headers=None,
    allow_html=True,
    accept_http_error=False,
    timeout=DEFAULT_TIMEOUT,
    seconds=DEFAULT_SECONDS,
    bytes_cap=DEFAULT_BYTES,
    ssl_context=None,
):
    """Sample the first ``bytes_cap`` bytes of ``url`` and estimate its speed.

    ``allow_html`` keeps mirror index pages usable (PyPI/Playwright probes),
    while release-asset probes pass ``False`` so an error page is never
    mistaken for a package.  ``accept_http_error`` treats a 4xx/5xx answer as
    "the host is reachable" — used when only connectivity can be measured.
    """
    info = probe_failure(name, url, None)
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Range": f"bytes=0-{max(1, int(bytes_cap)) - 1}",
    }
    if headers:
        request_headers.update(headers)
    started = time.time()
    try:
        request = urllib.request.Request(url, headers=request_headers)
        with urllib.request.urlopen(
            request, timeout=timeout, context=ssl_context
        ) as response:
            status = getattr(response, "status", 200)
            if status not in (200, 206):
                info["error"] = f"HTTP {status}"
                return info
            total_size = _total_size(response)
            if expected_size and total_size and total_size != expected_size:
                info["error"] = f"文件大小不符（{total_size} 字节）"
                return info
            content_type = (response.headers.get("Content-Type") or "").lower()
            if (
                not allow_html
                and content_type.startswith("text/html")
                and not str(url).endswith(".html")
            ):
                info["error"] = "返回的不是文件（text/html）"
                return info
            first_byte_at = time.time()
            info["latency"] = first_byte_at - started
            received = 0
            deadline = first_byte_at + seconds
            while received < bytes_cap and time.time() < deadline:
                chunk = response.read(min(64 * 1024, bytes_cap - received))
                if not chunk:
                    break
                received += len(chunk)
            elapsed = max(time.time() - first_byte_at, 0.001)
            info["speed"] = received / elapsed
            info["reachable"] = received > 0
            if not info["reachable"]:
                info["error"] = "未读到数据"
    except urllib.error.HTTPError as exc:
        info["latency"] = time.time() - started
        info["error"] = f"HTTP {exc.code}"
        if accept_http_error:
            info["reachable"] = True
    except Exception as exc:  # noqa: BLE001 - a failed probe only affects ranking
        if info["latency"] is None:
            info["latency"] = time.time() - started
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def sort_key(item):
    """Fastest reachable source first; unreachable ones keep a stable tail."""
    return (
        0 if item["reachable"] else 1,
        -(item["speed"] or 0.0),
        item["latency"] if item["latency"] is not None else float("inf"),
    )


def collect_probes(
    sources,
    probe_fn=probe_source,
    *,
    probe_kwargs=None,
    deadline=DEFAULT_DEADLINE,
    workers=DEFAULT_WORKERS,
    failure_factory=probe_failure,
):
    """Probe ``sources`` concurrently and return the results fastest-first.

    The whole round is capped by ``deadline`` seconds; sources that are still
    running are cancelled and reported as failures so a single hung host can
    never hold up the download.
    """
    sources = list(sources)
    if not sources:
        return []
    probe_kwargs = dict(probe_kwargs or {})
    workers = max(1, min(int(workers), len(sources)))
    results = []
    finished = set()
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {
        pool.submit(probe_fn, name, url, **probe_kwargs): (name, url)
        for name, url in sources
    }
    try:
        for future in as_completed(
            futures, timeout=max(0.1, deadline)
        ):
            finished.add(future)
            name, url = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                results.append(
                    failure_factory(name, url, f"{type(exc).__name__}: {exc}")
                )
    except FuturesTimeout:
        for future, (name, url) in futures.items():
            if future in finished:
                continue
            future.cancel()
            results.append(failure_factory(name, url, "探测超时"))
    finally:
        pool.shutdown(wait=False)
    results.sort(key=sort_key)
    return results


def cached(key, compute, *, ttl=CACHE_SECONDS):
    """Memoize a ranking for ``ttl`` seconds (shared across call sites)."""
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is not None and now - entry[0] < ttl:
            return entry[1]
    value = compute()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


def clear_cache() -> None:
    """Drop cached rankings (tests and explicit refreshes)."""
    with _CACHE_LOCK:
        _CACHE.clear()
