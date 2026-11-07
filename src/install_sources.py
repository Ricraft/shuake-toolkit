"""Rank PyPI mirrors and Playwright CDN hosts by measured download speed.

The launcher previously retried these lists in a fixed order, so a mirror that
answered but throttled (or a dead one) cost a full timeout before the next
candidate was tried.  Both lists are now sampled concurrently and ordered by
observed throughput, with the configured order kept as the fallback when every
probe fails.
"""

from __future__ import annotations

import subprocess

from src.source_probe import (
    DEFAULT_DEADLINE,
    cached,
    collect_probes,
    format_size,
    probe_source,
)

PYPI_MIRRORS = (
    ("清华", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里", "https://mirrors.aliyun.com/pypi/simple"),
    ("华为", "https://mirrors.huaweicloud.com/repository/pypi/simple"),
    ("官方", "https://pypi.org/simple"),
)

PLAYWRIGHT_HOSTS = (
    ("npmmirror", "https://npmmirror.com/mirrors/playwright"),
    ("官方", None),
)

# Playwright downloads from several equivalent CDN hosts; the relative path
# below the prefix is what the npmmirror binary mirror reproduces.
PLAYWRIGHT_CDN_PREFIXES = (
    "https://cdn.playwright.dev/dbazure/download/playwright",
    "https://playwright.download.prss.microsoft.com/dbazure/download/playwright",
    "https://playwright.azureedge.net",
    "https://cdn.playwright.dev",
)

PIP_PROBE_PACKAGE = "pip"


def pypi_probe_url(index_url: str) -> str:
    """Probe target for one PyPI mirror: the index page of ``pip`` itself."""
    return f"{str(index_url).rstrip('/')}/{PIP_PROBE_PACKAGE}/"


def _emit(logger, message: str, level: str = "info") -> None:
    if logger is None:
        return
    writer = getattr(logger, level, None) or getattr(logger, "info", None)
    if writer is None and callable(logger):
        writer = logger
    if callable(writer):
        writer(message)


def _summary(results) -> str:
    parts = []
    for item in results:
        if item["reachable"]:
            parts.append(f"{item['name']} {format_size(item['speed'])}/s")
        else:
            parts.append(f"{item['name']} 不可用")
    return "；".join(parts)


def _order_results(results, configured_order) -> list:
    """Reachable sources by speed, then the rest in the configured order.

    Probe results arrive in completion order, so unreachable entries must be
    re-anchored to the configured list to keep the fallback deterministic.
    """
    by_name = {item["name"]: item for item in results}
    ordered = [item for item in results if item["reachable"]]
    seen = {item["name"] for item in ordered}
    for name in configured_order:
        if name in seen or name not in by_name:
            continue
        ordered.append(by_name[name])
        seen.add(name)
    return ordered


def rank_pypi_mirrors(
    mirrors=PYPI_MIRRORS,
    *,
    logger=None,
    probe_fn=probe_source,
    deadline: float = DEFAULT_DEADLINE,
    use_cache: bool = True,
):
    """Return ``mirrors`` ordered fastest-first (original order if all fail)."""
    mirrors = tuple(mirrors)
    if len(mirrors) <= 1:
        return mirrors

    def compute():
        candidates = [(name, pypi_probe_url(url)) for name, url in mirrors]
        _emit(logger, f"正在检测 {len(candidates)} 个 PyPI 镜像的连通性与速度...")
        results = collect_probes(
            candidates,
            probe_fn,
            probe_kwargs={"allow_html": True},
            deadline=deadline,
        )
        configured = [name for name, _url in mirrors]
        ordered = _order_results(results, configured)
        _emit(logger, "PyPI 镜像检测结果: " + _summary(ordered))
        url_by_name = {name: url for name, url in mirrors}
        ranked = tuple(
            (item["name"], url_by_name[item["name"]])
            for item in ordered
            if item["name"] in url_by_name
        )
        working = [item for item in ordered if item["reachable"]]
        if working:
            _emit(logger, f"已选择 {working[0]['name']} 镜像安装依赖")
        else:
            _emit(logger, "所有 PyPI 镜像探测均失败，将按原顺序逐个尝试", "warning")
        return ranked

    if not use_cache:
        return compute()
    return cached(("pypi", tuple(str(url) for _, url in mirrors)), compute)


def rank_pypi_index_urls(mirrors=PYPI_MIRRORS, **kwargs):
    """Return only the index URLs, fastest-first (for ``pip -i`` flags)."""
    return [url for _, url in rank_pypi_mirrors(mirrors, **kwargs)]


def playwright_relative_path(url) -> str | None:
    """Strip a known Playwright CDN prefix, keeping the portable path."""
    text = str(url or "").strip()
    for prefix in PLAYWRIGHT_CDN_PREFIXES:
        if text.startswith(prefix):
            rest = text[len(prefix):]
            if not rest.startswith("/"):
                rest = f"/{rest}"
            return rest
    return None


def playwright_mirror_url(url, host_url) -> str | None:
    """Map one official artifact URL onto a mirror host (``None`` = official)."""
    if not host_url:
        return str(url) if url else None
    path = playwright_relative_path(url)
    if path is None:
        return None
    return f"{str(host_url).rstrip('/')}{path}"


def parse_dry_run_urls(output_text: str):
    """Collect ``Download url:`` lines from ``playwright install --dry-run``."""
    urls = []
    for line in str(output_text or "").splitlines():
        marker = "Download url:"
        if marker not in line:
            continue
        value = line.split(marker, 1)[1].strip()
        if value.startswith("http"):
            urls.append(value)
    return urls


def detect_playwright_urls(
    python_exe,
    *,
    browser: str = "chromium",
    run=subprocess.run,
    timeout: float = 120.0,
):
    """Ask Playwright which artifacts it would download, to probe real URLs."""
    try:
        completed = run(
            [python_exe, "-m", "playwright", "install", "--dry-run", browser],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - probing must never break installation
        return []
    if getattr(completed, "returncode", 1) != 0:
        return []
    return parse_dry_run_urls(getattr(completed, "stdout", "") or "")


def rank_playwright_hosts(
    hosts=PLAYWRIGHT_HOSTS,
    *,
    artifact_urls=(),
    python_exe=None,
    logger=None,
    probe_fn=probe_source,
    run=subprocess.run,
    deadline: float = DEFAULT_DEADLINE,
    use_cache: bool = True,
):
    """Return ``(name, host_url)`` pairs ordered fastest-first."""
    hosts = tuple(hosts)
    if len(hosts) <= 1:
        return hosts
    urls = list(artifact_urls or ()) or detect_playwright_urls(python_exe, run=run)
    primary = urls[0] if urls else None

    candidates = []
    for name, host_url in hosts:
        if primary:
            mapped = playwright_mirror_url(primary, host_url) or primary
            candidates.append((name, mapped))
        else:
            base = (host_url or "https://cdn.playwright.dev").rstrip("/")
            candidates.append((name, f"{base}/"))

    def compute():
        _emit(
            logger,
            f"正在检测 {len(candidates)} 个 Playwright 下载源的连通性与速度...",
        )
        results = collect_probes(
            candidates,
            probe_fn,
            probe_kwargs={"allow_html": True, "accept_http_error": True},
            deadline=deadline,
        )
        ordered = _order_results(results, [name for name, _url in hosts])
        _emit(logger, "Playwright 下载源检测结果: " + _summary(ordered))
        host_by_name = {name: host_url for name, host_url in hosts}
        ranked = tuple(
            (item["name"], host_by_name[item["name"]])
            for item in ordered
            if item["name"] in host_by_name
        )
        working = [item for item in ordered if item["reachable"]]
        if working:
            _emit(logger, f"已选择 {working[0]['name']} 源下载 Playwright")
        else:
            _emit(
                logger,
                "所有 Playwright 下载源探测均失败，将按原顺序逐个尝试",
                "warning",
            )
        return ranked

    if not use_cache:
        return compute()
    return cached(("playwright", tuple(name for name, _ in hosts), primary), compute)
