# -*- coding: utf-8 -*-
"""
核心管理模块 - 自动下载和更新 Yatori/Autovisor 核心
"""

import os
import json
import hashlib
import hmac
import urllib.request
import urllib.error
import zipfile
import shutil
import threading
import ssl
import re
import time
import glob
import tempfile
from datetime import datetime

try:
    from .atomic_io import atomic_dump_json
except ImportError:  # pragma: no cover - direct maintenance execution
    from atomic_io import atomic_dump_json


class CoreManager:
    """核心管理器 - 支持 GitHub 加速代理"""

    YATORI_FALLBACK_LOCAL_VERSION = "v2.6.2-beta.8"
    AUTOVISOR_FALLBACK_LOCAL_VERSION = "2025/5/2"
    AUTOVISOR_ENTRY_FILES = (
        "Autovisor.exe",
        "AUto.exe",
        "Auto.exe",
        "Autovisor.py",
        "Autovisor_Multi.py",
        "main.py",
        "run.py",
    )
    YATORI_ENTRY_FILES = ("yatori-go-console.exe", "start.bat")
    AUTOVISOR_UPSTREAM_MARKER_FILES = ("README.md", "LICENSE", "requirements.txt")
    AUTOVISOR_PATCH_MARKER_FILES = (
        "Autovisor.py",
        "Autovisor_Multi.py",
        "modules/bootstrap.py",
        "modules/multi_account_runner.py",
        "modules/configs.py",
    )
    AUTOVISOR_PRESERVE_FILES = (
        "configs.ini",
        os.path.join("res", "cookies.json"),
        os.path.join("res", "QRcode.jpg"),
    )
    AUTOVISOR_PRESERVE_DIRS = (
        "logs",
        "runtime_deps",
    )
    
    # GitHub API 配置
    YATORI_REPO = "Yatori-Dev/yatori-go-console"
    YATORI_API_URL = f"https://api.github.com/repos/{YATORI_REPO}/releases/latest"
    YATORI_RELEASES_API_URL = (
        f"https://api.github.com/repos/{YATORI_REPO}/releases?per_page=20"
    )
    
    # Autovisor 仓库
    AUTOVISOR_REPO = "CXRunfree/Autovisor"
    AUTOVISOR_API_URL = f"https://api.github.com/repos/{AUTOVISOR_REPO}/releases/latest"
    
    # GitHub 代理加速列表
    GITHUB_MIRRORS = (
        ("GitHub 直连", ""),
        ("ghproxy.net", "https://ghproxy.net/"),
        ("ghproxy.com", "https://ghproxy.com/"),
        ("mirror.ghproxy.com", "https://mirror.ghproxy.com/"),
        ("gh.ddlc.top", "https://gh.ddlc.top/"),
        ("github.moeyy.xyz", "https://github.moeyy.xyz/"),
        ("hub.gitmirror.com", "https://hub.gitmirror.com/"),
    )
    GITHUB_PROXIES = [prefix for _, prefix in GITHUB_MIRRORS if prefix]
    
    def __init__(self, base_dir, log_callback=None):
        self.base_dir = base_dir
        self.log_callback = log_callback
        self.yatori_path = os.path.join(base_dir, "Yatori")
        self.autovisor_path = os.path.join(base_dir, "Autovisor")
        
        # 创建目录
        os.makedirs(self.yatori_path, exist_ok=True)
        os.makedirs(self.autovisor_path, exist_ok=True)
        
        # 版本信息文件
        self.version_file = os.path.join(base_dir, ".core_versions.json")
        self.local_versions = self._load_local_versions()
        
        # Never disable certificate validation for executable/core downloads.
        self.ssl_context = ssl.create_default_context()
        self.allow_github_proxies = os.environ.get(
            "LAUNCHER_ALLOW_GITHUB_PROXIES", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.allow_unverified_core_updates = os.environ.get(
            "LAUNCHER_ALLOW_UNVERIFIED_CORE_UPDATES", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
    
    def _log(self, message):
        """记录日志"""
        if self.log_callback:
            self.log_callback(message)
        print(f"[CoreManager] {message}")

    def _format_version_date(self, timestamp):
        dt = datetime.fromtimestamp(timestamp)
        return f"{dt.year}/{dt.month}/{dt.day}"

    def _format_compact_date(self, timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%Y%m%d")

    @staticmethod
    def _parse_yatori_version(value):
        text = str(value or "").strip()
        match = re.fullmatch(
            r"[vV]?(\d+(?:\.\d+)*)"
            r"(?:-([0-9A-Za-z.-]+))?"
            r"(?:\+[0-9A-Za-z.-]+)?",
            text,
        )
        if not match:
            return None

        core = tuple(int(part) for part in match.group(1).split("."))
        prerelease = match.group(2)
        if prerelease is None:
            return core, None

        identifiers = []
        for part in re.findall(r"[A-Za-z]+|\d+", prerelease):
            if part.isdigit():
                identifiers.append((0, int(part)))
            else:
                identifiers.append((1, part.lower()))
        if not identifiers:
            return None
        return core, tuple(identifiers)

    @classmethod
    def _compare_yatori_versions(cls, left, right):
        left_parsed = cls._parse_yatori_version(left)
        right_parsed = cls._parse_yatori_version(right)
        if left_parsed is None or right_parsed is None:
            return None

        left_core, left_pre = left_parsed
        right_core, right_pre = right_parsed
        core_length = max(len(left_core), len(right_core))
        left_core = left_core + (0,) * (core_length - len(left_core))
        right_core = right_core + (0,) * (core_length - len(right_core))
        if left_core != right_core:
            return 1 if left_core > right_core else -1

        if left_pre is None and right_pre is None:
            return 0
        if left_pre is None:
            return 1
        if right_pre is None:
            return -1

        for index in range(max(len(left_pre), len(right_pre))):
            if index >= len(left_pre):
                return -1
            if index >= len(right_pre):
                return 1
            left_part = left_pre[index]
            right_part = right_pre[index]
            if left_part == right_part:
                continue
            if left_part[0] != right_part[0]:
                return -1 if left_part[0] == 0 else 1
            return 1 if left_part[1] > right_part[1] else -1
        return 0

    def _collect_existing_mtimes(self, root_dir, relative_paths):
        mtimes = []
        for relative_path in relative_paths:
            full_path = os.path.join(root_dir, relative_path)
            if os.path.exists(full_path):
                mtimes.append(os.path.getmtime(full_path))
        return mtimes

    def get_autovisor_local_version_info(self):
        recorded_version = self.local_versions.get('autovisor')
        if recorded_version:
            return {
                'display': recorded_version,
                'compare': recorded_version,
                'source': 'local_versions',
            }

        upstream_mtimes = self._collect_existing_mtimes(
            self.autovisor_path,
            self.AUTOVISOR_UPSTREAM_MARKER_FILES,
        )
        patch_mtimes = self._collect_existing_mtimes(
            self.autovisor_path,
            self.AUTOVISOR_PATCH_MARKER_FILES,
        )

        compare_version = self.AUTOVISOR_FALLBACK_LOCAL_VERSION
        if upstream_mtimes:
            compare_version = self._format_version_date(max(upstream_mtimes))

        display_version = compare_version
        if patch_mtimes:
            patch_version = self._format_compact_date(max(patch_mtimes))
            base_compact = compare_version.replace("/", "")
            if patch_version != base_compact:
                display_version = f"{compare_version}（{patch_version}修复版）"

        return {
            'display': display_version,
            'compare': compare_version,
            'source': 'inferred',
        }

    def _build_github_candidate_urls(self, url):
        """Build direct and explicitly enabled proxy candidates for a GitHub URL."""
        candidates = [("GitHub 直连", url)]
        if self.allow_github_proxies:
            candidates.extend(
                (name, prefix + url)
                for name, prefix in self.GITHUB_MIRRORS
                if prefix
            )
        return candidates
    
    def _format_size(self, size_bytes):
        """格式化字节大小"""
        if size_bytes is None:
            return "未知"
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"
    
    def _format_duration(self, seconds):
        """格式化耗时"""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}秒"
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}分{sec}秒"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}时{minutes}分{sec}秒"

    def _safe_name(self, value):
        return re.sub(r'[^A-Za-z0-9._-]+', '_', str(value or 'unknown')).strip('_') or 'unknown'

    @staticmethod
    def _safe_extract_zip(zip_ref, destination):
        """Extract a downloaded archive without allowing path traversal."""
        destination = os.path.abspath(destination)
        for member in zip_ref.infolist():
            target = os.path.abspath(os.path.join(destination, member.filename))
            if os.path.commonpath((destination, target)) != destination:
                raise ValueError(f"压缩包包含越界路径: {member.filename}")
        zip_ref.extractall(destination)
    
    def _load_local_versions(self):
        """加载本地版本信息"""
        defaults = {'yatori': None, 'autovisor': None, 'last_check': None}
        if os.path.exists(self.version_file):
            try:
                with open(self.version_file, 'r', encoding='utf-8') as f:
                    versions = json.load(f)
                if not isinstance(versions, dict):
                    self._log(
                        "版本文件顶层格式无效，期望 JSON 对象，使用默认版本信息"
                    )
                    return defaults
                return versions
            except Exception as e:
                self._log(f"加载版本文件失败: {e}")
        return defaults
    
    def _save_local_versions(self):
        """保存本地版本信息"""
        try:
            atomic_dump_json(self.version_file, self.local_versions)
            return True
        except Exception as e:
            self._log(f"保存版本文件失败: {e}")
            return False

    def _directory_has_any_file(self, directory, file_names):
        return os.path.isdir(directory) and any(
            os.path.exists(os.path.join(directory, name)) for name in file_names
        )

    def _find_existing_dir(self, candidates, required_files):
        seen = set()
        for candidate in candidates:
            matches = sorted(glob.glob(candidate)) if "*" in candidate else [candidate]
            for matched in matches:
                normalized = os.path.normpath(matched)
                normalized_case = os.path.normcase(normalized)
                if normalized_case in seen:
                    continue
                seen.add(normalized_case)
                if self._directory_has_any_file(normalized, required_files):
                    return normalized
        return None
    
    def check_yatori_installed(self):
        """检查 Yatori 是否已安装"""
        detected_path = self._find_existing_dir(
            [
                self.yatori_path,
                os.path.join(self.base_dir, "yatori-go-console*", "yatori-go-console*", "command"),
                os.path.join(self.base_dir, "yatori-go-console*", "command"),
                os.path.join(self.base_dir, "yatori-go-console*"),
            ],
            self.YATORI_ENTRY_FILES,
        )
        return bool(detected_path)
    
    def check_autovisor_installed(self):
        """检查 Autovisor 是否已安装 - 支持多个可能的入口文件"""
        return bool(self._find_existing_dir(
            [
                self.autovisor_path,
                os.path.join(self.base_dir, "AUto"),
                os.path.join(self.base_dir, "Auto"),
                os.path.join(self.base_dir, "Autovisor*", "Autovisor*"),
                os.path.join(self.base_dir, "Autovisor*"),
                os.path.join(self.base_dir, "AUto*", "AUto*"),
                os.path.join(self.base_dir, "AUto*"),
                os.path.join(self.base_dir, "Auto*", "Auto*"),
                os.path.join(self.base_dir, "Auto*"),
            ],
            self.AUTOVISOR_ENTRY_FILES,
        ))
    
    def get_autovisor_entry_file(self):
        """获取 Autovisor 入口文件路径 - 按优先级返回第一个存在的"""
        autovisor_dir = self._find_existing_dir(
            [
                self.autovisor_path,
                os.path.join(self.base_dir, "AUto"),
                os.path.join(self.base_dir, "Auto"),
                os.path.join(self.base_dir, "Autovisor*", "Autovisor*"),
                os.path.join(self.base_dir, "Autovisor*"),
                os.path.join(self.base_dir, "AUto*", "AUto*"),
                os.path.join(self.base_dir, "AUto*"),
                os.path.join(self.base_dir, "Auto*", "Auto*"),
                os.path.join(self.base_dir, "Auto*"),
            ],
            self.AUTOVISOR_ENTRY_FILES,
        )
        if not autovisor_dir:
            return None

        for entry in self.AUTOVISOR_ENTRY_FILES:
            entry_path = os.path.join(autovisor_dir, entry)
            if os.path.exists(entry_path):
                return entry_path

        return None
    
    def get_yatori_latest_release(self):
        """获取 Yatori 最新 release 信息 (增加代理支持)"""
        try:
            self._log("正在检查 Yatori 最新版本...")
            
            # 1. 优先读取 releases 列表。GitHub /releases/latest 对 beta/
            # prerelease 版本并不总是可靠，Yatori 的可用更新经常发布在 beta
            # 通道，因此这里主动从发布列表里选择最新可安装的 Windows ZIP。
            try:
                req = urllib.request.Request(
                    self.YATORI_RELEASES_API_URL,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Accept': 'application/vnd.github.v3+json'
                    }
                )
                with urllib.request.urlopen(req, timeout=5, context=self.ssl_context) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    release = self._select_yatori_release(data)
                    if release:
                        return release
                    self._log("发布列表中未找到可安装的 Yatori Windows ZIP，尝试 latest API...")
            except Exception as e:
                self._log(f"访问发布列表失败 ({e})，尝试 latest API...")

            # 2. 兼容旧路径：当 releases 列表不可用时再读取 latest。
            try:
                req = urllib.request.Request(
                    self.YATORI_API_URL,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Accept': 'application/vnd.github.v3+json'
                    }
                )
                with urllib.request.urlopen(req, timeout=5, context=self.ssl_context) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    return self._parse_yatori_release(data)
            except Exception as e:
                self._log(f"访问 latest API 失败 ({e})，尝试备用方式...")
            
            # 3. 尝试备用方式
            return self._get_yatori_latest_fallback()
                
        except Exception as e:
            self._log(f"获取版本信息完全失败: {e}")
        
        return None

    @staticmethod
    def _github_release_time_key(release):
        value = str(
            release.get("published_at")
            or release.get("created_at")
            or ""
        )
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0

    def _select_yatori_release(self, releases):
        """Select newest installable Yatori release, including beta/prerelease."""
        if not isinstance(releases, list):
            return None

        candidates = []
        for release in releases:
            if not isinstance(release, dict) or release.get("draft"):
                continue
            parsed = self._parse_yatori_release(release)
            if parsed:
                parsed["prerelease"] = bool(release.get("prerelease"))
                candidates.append(
                    (
                        self._parse_yatori_version(parsed.get("version")),
                        self._github_release_time_key(release),
                        parsed,
                    )
                )

        if not candidates:
            return None

        # GitHub usually returns releases by publication time, but an older
        # tag can be edited or republished later. Prefer the highest semantic
        # version so that a recently touched beta cannot hide a newer build.
        known_versions = [item for item in candidates if item[0] is not None]
        pool = known_versions or candidates
        selected = pool[0]
        for candidate in pool[1:]:
            if known_versions:
                comparison = self._compare_yatori_versions(
                    candidate[2].get("version"),
                    selected[2].get("version"),
                )
                if comparison > 0 or (
                    comparison == 0 and candidate[1] > selected[1]
                ):
                    selected = candidate
            elif candidate[1] > selected[1]:
                selected = candidate
        return selected[2]

    @staticmethod
    def _is_yatori_windows_amd64_asset(name):
        """Return whether an asset name unambiguously targets Windows x64."""
        if not isinstance(name, str) or not name.lower().endswith(".zip"):
            return False

        tokens = {
            token
            for token in re.split(r"[-_.]+", name.lower())
            if token
        }
        if tokens & {"darwin", "linux", "macos", "osx", "arm64", "aarch64"}:
            return False

        windows = bool(tokens & {"windows", "win", "win64"})
        amd64 = bool(tokens & {"amd64", "x64", "win64"}) or {
            "x86",
            "64",
        }.issubset(tokens)
        return windows and amd64

    def _parse_yatori_release(self, data):
        """解析 Yatori release 数据"""
        if not isinstance(data, dict):
            return None
        version = data.get('tag_name', 'unknown')
        assets = data.get('assets', [])

        download_url = None
        asset_name = None
        selected_asset = None

        # 只接受明确标注 Windows x64/amd64 的 ZIP。旧的 ``'win' in
        # name`` 会把 ``darwin-amd64`` 误判成 Windows 包；任意 ZIP
        # 回退还可能选中 Linux/ARM 构建。
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = asset.get('name', '')
            if self._is_yatori_windows_amd64_asset(name):
                download_url = asset.get('browser_download_url')
                if not isinstance(download_url, str) or not download_url.strip():
                    continue
                asset_name = name
                selected_asset = asset
                break

        if not download_url:
            self._log("未找到明确标注 Windows amd64/x64 的 ZIP 发行资源")
            return None

        self._log(f"最新版本: {version}")
        self._log(f"匹配到资源: {asset_name}")

        return {
            'version': version,
            'download_url': download_url,
            'asset_name': asset_name,
            'digest': selected_asset.get('digest') if selected_asset else None,
            'asset_size': selected_asset.get('size') if selected_asset else None,
            'asset_id': selected_asset.get('id') if selected_asset else None,
            'verification_source': 'github-release-asset-digest',
            'published_at': data.get('published_at'),
            'body': data.get('body', '')
        }
    
    def _get_yatori_latest_fallback(self):
        """备用方式获取最新版本（爬取 releases 页面获取实际下载链接）"""
        latest_url = f"https://github.com/{self.YATORI_REPO}/releases/latest"
        last_error = None
        for source_name, try_url in self._build_github_candidate_urls(latest_url):
            try:
                self._log(f"探测 Yatori 版本 ({source_name})")
                req = urllib.request.Request(
                    try_url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
                    method='HEAD'
                )

                with urllib.request.urlopen(req, timeout=10, context=self.ssl_context) as response:
                    final_url = response.geturl()
                    # 从 URL 中提取版本号，例如 /releases/tag/v1.2.3
                    if '/tag/' in final_url:
                        version = final_url.split('/tag/')[-1].split('?')[0]
                        # 爬取 releases/tag 页面获取实际的 Windows 资源下载链接
                        download_url = self._scrape_yatori_download_url(
                            f"https://github.com/{self.YATORI_REPO}/releases/tag/{version}"
                        )
                        if download_url:
                            self._log(f"获取到最新版本: {version} (通过探测)")
                            return {
                                'version': version,
                                'download_url': download_url,
                                'asset_name': '通过探测获取',
                                'digest': None,
                                'asset_size': None,
                                'verification_source': 'unavailable',
                                'published_at': None,
                                'body': '通过备用方式获取'
                            }
                        self._log(f"获取到版本 {version}，但无法解析下载链接，继续尝试...")
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            self._log(f"Yatori 备用版本探测失败: {last_error}")
        return None

    def _scrape_yatori_download_url(self, tag_url):
        """爬取 release tag 页面，解析出 Windows 实际 .zip 下载链接"""
        version = tag_url.rstrip('/').split('/tag/')[-1]
        expanded_url = (
            f"https://github.com/{self.YATORI_REPO}/releases/expanded_assets/"
            f"{version}"
        )
        page_urls = (expanded_url, tag_url)
        last_error = None

        for page_url in page_urls:
            for source_name, try_url in self._build_github_candidate_urls(page_url):
                try:
                    page_kind = "资源列表" if page_url == expanded_url else "发行页面"
                    self._log(f"解析 Yatori {page_kind} ({source_name})")
                    req = urllib.request.Request(
                        try_url,
                        headers={
                            'User-Agent': (
                                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                'AppleWebKit/537.36'
                            )
                        }
                    )
                    with urllib.request.urlopen(
                        req,
                        timeout=15,
                        context=self.ssl_context,
                    ) as response:
                        html = response.read().decode('utf-8', errors='replace')
                    asset = self._extract_yatori_asset_url(html)
                    if asset:
                        download_url, is_windows = asset
                        if is_windows:
                            self._log(
                                f"匹配到 Windows 资源: {download_url.split('/')[-1]}"
                            )
                        else:
                            self._log(
                                "未找到平台标识，使用唯一可用 ZIP: "
                                f"{download_url.split('/')[-1]}"
                            )
                        return download_url
                except Exception as exc:
                    last_error = exc
                    continue

        if last_error:
            self._log(f"Yatori 发行资源解析失败: {last_error}")
        else:
            self._log("Yatori 发行资源中未找到任何 .zip 下载链接")

        return None

    def _extract_yatori_asset_url(self, html):
        repo_path = re.escape(self.YATORI_REPO)
        base_pattern = rf'/{repo_path}/releases/download/[^"\']*'
        windows_match = re.search(
            rf'({base_pattern}(?:windows|win64|win-amd64)[^"\']*\.zip)',
            html,
            re.IGNORECASE,
        )
        if windows_match:
            return "https://github.com" + windows_match.group(1), True

        zip_matches = re.findall(
            rf'({base_pattern}\.zip)',
            html,
            re.IGNORECASE,
        )
        compatible = [
            path
            for path in zip_matches
            if not re.search(r'(linux|darwin|macos|aarch64|arm64)', path, re.I)
        ]
        if len(compatible) == 1:
            return "https://github.com" + compatible[0], False
        return None

    def get_autovisor_latest_release(self):
        """获取 Autovisor 最新信息 - 增强错误日志"""
        try:
            self._log("正在检查 Autovisor 最新版本...")
            try:
                req = urllib.request.Request(
                    self.AUTOVISOR_API_URL,
                    headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/vnd.github.v3+json'}
                )
                with urllib.request.urlopen(req, timeout=8, context=self.ssl_context) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    version = data.get('tag_name', 'latest')

                    # 优先使用 zipball_url，没有就报错
                    zipball_url = data.get('zipball_url')
                    if not zipball_url:
                        self._log("[错误] API 响应中未找到 zipball_url，无法获取下载链接")
                        return None

                    self._log(f"Autovisor 最新版本: {version}")
                    return {
                        'version': version,
                        'download_url': zipball_url,
                        'digest': None,
                        'asset_size': None,
                        'verification_source': 'unavailable',
                        'published_at': data.get('published_at'),
                        'source': 'zipball_url'
                    }
            except urllib.error.HTTPError as e:
                self._log(f"[错误] Autovisor API 获取失败: HTTP {e.code} - {e.reason}")
            except urllib.error.URLError as e:
                self._log(f"[错误] Autovisor 网络连接失败: {e.reason}")
            except json.JSONDecodeError as e:
                self._log(f"[错误] Autovisor API 响应解析失败: {e}")

            fallback_label = "GitHub 页面与代理" if self.allow_github_proxies else "GitHub 页面"
            self._log(f"Autovisor API 直连失败，尝试通过{fallback_label}回退...")
            return self._get_autovisor_latest_fallback()
        except Exception as e:
            self._log(f"[错误] Autovisor 获取版本信息时发生未知错误: {type(e).__name__}: {e}")
            return None

    def _get_autovisor_latest_fallback(self):
        """通过 GitHub releases/latest 页面及代理回退探测 Autovisor 最新版本"""
        latest_url = f"https://github.com/{self.AUTOVISOR_REPO}/releases/latest"
        last_error = None
        for source_name, try_url in self._build_github_candidate_urls(latest_url):
            try:
                self._log(f"探测 Autovisor 版本 ({source_name})")
                req = urllib.request.Request(
                    try_url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
                    method='HEAD'
                )

                with urllib.request.urlopen(req, timeout=10, context=self.ssl_context) as response:
                    final_url = response.geturl()
                    if '/tag/' in final_url:
                        version = final_url.split('/tag/')[-1].split('?')[0]
                        download_url = f"https://github.com/{self.AUTOVISOR_REPO}/archive/refs/tags/{version}.zip"
                        self._log(f"获取到 Autovisor 最新版本: {version} (通过探测)")
                        return {
                            'version': version,
                            'download_url': download_url,
                            'digest': None,
                            'asset_size': None,
                            'verification_source': 'unavailable',
                            'published_at': None,
                            'source': 'tag-archive'
                        }
            except Exception as e:
                last_error = e
                continue

        if last_error:
            self._log(f"Autovisor 版本探测失败: {last_error}")
        return None

    def download_file(self, url, target_path, progress_callback=None):
        """下载文件 (支持代理切换、重试、速度/耗时/进度显示)"""
        urls_to_try = [url]
        if self.allow_github_proxies and ("github.com" in url or "githubusercontent.com" in url):
            for proxy in self.GITHUB_PROXIES:
                urls_to_try.append(proxy + url)

        last_error = None

        for i, try_url in enumerate(urls_to_try):
            mode = "直接下载" if i == 0 else f"代理加速 {i}"

            try:
                self._log(f"正在尝试 {mode}: {try_url}")

                req = urllib.request.Request(
                    try_url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                )

                with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as response:
                    total_size = int(response.headers.get('Content-Length', 0)) or None
                    downloaded = 0
                    chunk_size = 64 * 1024   # 64KB，比 16KB 更顺一点
                    start_time = time.time()
                    last_log_time = 0
                    last_ui_time = 0

                    if total_size:
                        self._log(f"{mode} 已连接，文件大小: {self._format_size(total_size)}")
                    else:
                        self._log(f"{mode} 已连接，文件大小未知")

                    with open(target_path, 'wb') as f:
                        while True:
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break

                            f.write(chunk)
                            downloaded += len(chunk)

                            now = time.time()
                            elapsed = max(now - start_time, 0.001)
                            speed = downloaded / elapsed  # B/s

                            progress = None
                            eta = None
                            if total_size:
                                progress = (downloaded / total_size) * 100
                                remain = max(total_size - downloaded, 0)
                                eta = remain / speed if speed > 1 else None

                            # UI 更新频率高一点
                            if progress_callback and (now - last_ui_time >= 0.2):
                                progress_callback({
                                    'progress': progress if progress is not None else 0,
                                    'downloaded': downloaded,
                                    'total': total_size,
                                    'speed': speed,
                                    'elapsed': elapsed,
                                    'eta': eta,
                                    'mode': mode,
                                    'url': try_url,
                                })
                                last_ui_time = now

                            # 日志更新频率低一点，避免刷屏
                            if now - last_log_time >= 1.0:
                                if total_size:
                                    self._log(
                                        f"{mode} 下载中: {progress:.1f}% | "
                                        f"{self._format_size(downloaded)}/{self._format_size(total_size)} | "
                                        f"速度 {self._format_size(speed)}/s | "
                                        f"已用 {self._format_duration(elapsed)} | "
                                        f"剩余 {self._format_duration(eta) if eta is not None else '未知'}"
                                    )
                                else:
                                    self._log(
                                        f"{mode} 下载中: "
                                        f"{self._format_size(downloaded)} | "
                                        f"速度 {self._format_size(speed)}/s | "
                                        f"已用 {self._format_duration(elapsed)}"
                                    )
                                last_log_time = now

                    if total_size is not None and downloaded != total_size:
                        raise IOError(
                            f"下载不完整: 期望 {total_size} 字节，实际 {downloaded} 字节"
                        )

                    # 最后一帧，确保 100%
                    total_elapsed = time.time() - start_time
                    avg_speed = downloaded / max(total_elapsed, 0.001)

                    if progress_callback:
                        progress_callback({
                            'progress': 100 if total_size else 0,
                            'downloaded': downloaded,
                            'total': total_size,
                            'speed': avg_speed,
                            'elapsed': total_elapsed,
                            'eta': 0,
                            'mode': mode,
                            'url': try_url,
                        })

                    self._log(
                        f"下载成功！耗时 {self._format_duration(total_elapsed)}，"
                        f"平均速度 {self._format_size(avg_speed)}/s"
                    )
                    return True

            except urllib.error.HTTPError as e:
                last_error = e
                if e.code == 404:
                    self._log(f"{mode} 失败: 资源不存在 (404)")
                else:
                    self._log(f"{mode} 失败: HTTP {e.code} - {e.reason}")

            except Exception as e:
                last_error = e
                self._log(f"{mode} 失败: {e}")

            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except:
                    pass

        self._log(f"所有下载渠道均失败。最后错误: {last_error}")
        return False

    @staticmethod
    def _normalize_sha256_digest(value):
        """Normalize GitHub's ``sha256:<hex>`` asset digest."""
        if not isinstance(value, str):
            return None
        algorithm, separator, digest = value.strip().partition(":")
        if separator != ":" or algorithm.lower() != "sha256":
            return None
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return None
        return digest.lower()

    def _verify_release_archive(self, archive_path, release_info):
        """Verify a downloaded core before extraction or replacement.

        GitHub exposes a SHA-256 digest for uploaded release assets. Source
        archives and HTML fallback discovery do not expose an equivalent
        publisher-bound digest, so automatic installation is refused by
        default. A developer can explicitly opt in with
        ``LAUNCHER_ALLOW_UNVERIFIED_CORE_UPDATES=1``.
        """
        raw_digest = release_info.get("digest")
        expected_digest = self._normalize_sha256_digest(raw_digest)
        if expected_digest is None:
            reason = "缺少 SHA-256 摘要" if not raw_digest else "SHA-256 摘要格式无效"
            if self.allow_unverified_core_updates:
                self._log(
                    f"[安全警告] 更新资源{reason}，已按显式环境变量允许未校验安装"
                )
                return True
            self._log(
                f"[安全拦截] 更新资源{reason}，拒绝自动安装；"
                "可等待发布方提供 GitHub 资源摘要或手动核验安装"
            )
            return False

        expected_size = release_info.get("asset_size")
        try:
            expected_size = int(expected_size) if expected_size is not None else None
        except (TypeError, ValueError):
            self._log("[安全拦截] GitHub 资源大小元数据无效，拒绝自动安装")
            return False
        if expected_size is not None and expected_size >= 0:
            actual_size = os.path.getsize(archive_path)
            if actual_size != expected_size:
                self._log(
                    "[安全拦截] 更新资源大小不匹配: "
                    f"期望 {expected_size} 字节，实际 {actual_size} 字节"
                )
                return False

        digest = hashlib.sha256()
        with open(archive_path, "rb") as archive:
            for chunk in iter(lambda: archive.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_digest = digest.hexdigest()
        if not hmac.compare_digest(actual_digest, expected_digest):
            self._log(
                "[安全拦截] 更新资源 SHA-256 校验失败，下载内容可能损坏或被替换"
            )
            return False

        self._log(f"更新资源 SHA-256 校验通过: {actual_digest}")
        return True

    def _can_download_release(self, release_info):
        """Reject unverified automatic updates before spending bandwidth."""
        raw_digest = release_info.get("digest")
        if self._normalize_sha256_digest(raw_digest) is not None:
            return True
        if self.allow_unverified_core_updates:
            return True
        reason = "缺少 SHA-256 摘要" if not raw_digest else "SHA-256 摘要格式无效"
        self._log(
            f"[安全拦截] 更新资源{reason}，未开始下载；"
            "可等待发布方提供 GitHub 资源摘要或手动核验安装"
        )
        return False

    def _find_yatori_source_dir(self, extract_dir):
        """Locate the runnable Yatori directory inside a release archive."""
        candidates = []
        extract_dir = os.path.abspath(extract_dir)
        for root, _dirs, files in os.walk(extract_dir):
            names = set(files)
            if not any(entry in names for entry in self.YATORI_ENTRY_FILES):
                continue
            relative = os.path.relpath(root, extract_dir)
            depth = 0 if relative == "." else len(relative.split(os.sep))
            score = (
                1 if "yatori-go-console.exe" in names else 0,
                sum(entry in names for entry in self.YATORI_ENTRY_FILES),
                -depth,
            )
            candidates.append((score, root))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    
    def install_yatori(self, release_info, progress_callback=None):
        """Install Yatori through a validated, rollback-capable directory swap."""
        version = release_info['version']
        download_url = release_info['download_url']
        safe_version = self._safe_name(version)
        work_dir = tempfile.mkdtemp(
            prefix=f".yatori-update-{safe_version}-",
            dir=self.base_dir,
        )
        temp_zip = os.path.join(work_dir, "release.zip")
        temp_extract = os.path.join(work_dir, "extract")
        stage_dir = os.path.join(work_dir, "stage")
        backup_dir = os.path.join(work_dir, "previous")
        moved_old = False
        activated_new = False
        preserve_work_dir = False
        previous_versions = dict(self.local_versions)

        try:
            self._log(f"正在安装 Yatori {version}...")
            if not self._can_download_release(release_info):
                return False

            # Capture the current config only from the live core. A fixed
            # config.yaml.bak file can be stale and must never be reused.
            config_file = os.path.join(self.yatori_path, "config.yaml")
            config_bytes = None
            if os.path.isfile(config_file):
                with open(config_file, "rb") as handle:
                    config_bytes = handle.read()

            if not self.download_file(download_url, temp_zip, progress_callback):
                return False
            if not self._verify_release_archive(temp_zip, release_info):
                return False

            self._log("正在解压...")
            os.makedirs(temp_extract, exist_ok=True)
            with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
                self._safe_extract_zip(zip_ref, temp_extract)

            source_dir = self._find_yatori_source_dir(temp_extract)
            if not source_dir:
                raise ValueError("发行包中未找到可运行的 Yatori 入口文件")

            self._log("正在准备新版本...")
            shutil.copytree(source_dir, stage_dir)
            if not self._directory_has_any_file(stage_dir, self.YATORI_ENTRY_FILES):
                raise ValueError("Yatori 新版本暂存校验失败")

            if config_bytes is not None:
                with open(os.path.join(stage_dir, "config.yaml"), "wb") as handle:
                    handle.write(config_bytes)
                self._log("已恢复用户配置")

            # Both directories live under base_dir, so the final activation is
            # a same-volume rename. Keep the previous core until activation
            # and metadata updates have completed.
            if os.path.exists(self.yatori_path):
                self._log("正在切换到新版本...")
                os.replace(self.yatori_path, backup_dir)
                moved_old = True
            os.replace(stage_dir, self.yatori_path)
            activated_new = True

            self.local_versions['yatori'] = version
            self.local_versions['last_check'] = datetime.now().isoformat()
            if not self._save_local_versions():
                raise OSError("无法保存 Yatori 版本记录")

            if moved_old and os.path.exists(backup_dir):
                try:
                    shutil.rmtree(backup_dir)
                except Exception as cleanup_error:
                    preserve_work_dir = True
                    self._log(
                        "新版本已启用，但旧版本备份清理失败，"
                        f"已保留在 {backup_dir}: {cleanup_error}"
                    )

            self._log(f"Yatori {version} 安装完成！")
            return True

        except Exception as e:
            self.local_versions = previous_versions
            self._log(f"安装失败: {e}")
            if moved_old and os.path.exists(backup_dir):
                try:
                    if os.path.exists(self.yatori_path):
                        shutil.rmtree(self.yatori_path)
                    os.replace(backup_dir, self.yatori_path)
                    activated_new = False
                    self._log("已回滚到更新前的 Yatori")
                except Exception as rollback_error:
                    preserve_work_dir = True
                    self._log(
                        "回滚 Yatori 失败，旧版本备份已保留在 "
                        f"{backup_dir}: {rollback_error}"
                    )
            elif activated_new and os.path.exists(self.yatori_path):
                try:
                    shutil.rmtree(self.yatori_path)
                except Exception as cleanup_error:
                    self._log(f"清理未完成的 Yatori 安装失败: {cleanup_error}")
            return False
        finally:
            if not preserve_work_dir and os.path.exists(work_dir):
                try:
                    shutil.rmtree(work_dir)
                except Exception as cleanup_error:
                    self._log(f"清理 Yatori 更新临时目录失败: {cleanup_error}")

    def _find_autovisor_source_dir(self, extract_dir):
        candidates = [extract_dir]
        for root, dirs, files in os.walk(extract_dir):
            score = 0
            names = set(files) | set(dirs)
            if any(entry in names for entry in self.AUTOVISOR_ENTRY_FILES):
                score += 4
            if "requirements.txt" in names:
                score += 2
            if "modules" in names:
                score += 2
            if "res" in names:
                score += 1
            if score >= 4:
                return root
            candidates.append(root)

        items = os.listdir(extract_dir)
        if len(items) == 1 and os.path.isdir(os.path.join(extract_dir, items[0])):
            return os.path.join(extract_dir, items[0])

        return candidates[0]

    def _restore_path(self, source, target):
        if not os.path.exists(source):
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isdir(source):
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)

    def install_autovisor(self, release_info, progress_callback=None):
        """安装/更新 Autovisor，保留用户配置和运行日志。"""
        version = release_info['version']
        download_url = release_info['download_url']
        safe_version = self._safe_name(version)

        temp_zip = os.path.join(self.base_dir, f"autovisor-{safe_version}.zip")
        temp_extract = os.path.join(self.base_dir, f"autovisor-{safe_version}-temp")
        backup_dir = os.path.join(self.base_dir, f"Autovisor-backup-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        preserve_dir = os.path.join(self.base_dir, f"autovisor-preserve-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        moved_old = False

        try:
            self._log(f"正在安装 Autovisor {version}...")
            if not self._can_download_release(release_info):
                return False

            os.makedirs(preserve_dir, exist_ok=True)
            for relative_path in self.AUTOVISOR_PRESERVE_FILES:
                src = os.path.join(self.autovisor_path, relative_path)
                dst = os.path.join(preserve_dir, relative_path)
                if os.path.exists(src):
                    self._restore_path(src, dst)
            for relative_path in self.AUTOVISOR_PRESERVE_DIRS:
                src = os.path.join(self.autovisor_path, relative_path)
                dst = os.path.join(preserve_dir, relative_path)
                if os.path.exists(src):
                    self._restore_path(src, dst)
            self._log("已备份 Autovisor 用户配置和运行数据")

            if not self.download_file(download_url, temp_zip, progress_callback):
                return False
            if not self._verify_release_archive(temp_zip, release_info):
                return False

            self._log("正在解压 Autovisor...")
            with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
                self._safe_extract_zip(zip_ref, temp_extract)

            source_dir = self._find_autovisor_source_dir(temp_extract)
            if not source_dir or not os.path.isdir(source_dir):
                raise RuntimeError("未在压缩包中找到可用的 Autovisor 目录")

            if os.path.exists(self.autovisor_path):
                if os.path.exists(backup_dir):
                    shutil.rmtree(backup_dir)
                os.replace(self.autovisor_path, backup_dir)
                moved_old = True

            os.makedirs(self.autovisor_path, exist_ok=True)
            for item in os.listdir(source_dir):
                src = os.path.join(source_dir, item)
                dst = os.path.join(self.autovisor_path, item)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)

            for relative_path in self.AUTOVISOR_PRESERVE_FILES:
                self._restore_path(
                    os.path.join(preserve_dir, relative_path),
                    os.path.join(self.autovisor_path, relative_path),
                )
            for relative_path in self.AUTOVISOR_PRESERVE_DIRS:
                self._restore_path(
                    os.path.join(preserve_dir, relative_path),
                    os.path.join(self.autovisor_path, relative_path),
                )

            self.local_versions['autovisor'] = version
            self.local_versions['last_check'] = datetime.now().isoformat()
            self._save_local_versions()

            if moved_old and os.path.exists(backup_dir):
                shutil.rmtree(backup_dir)

            self._log(f"Autovisor {version} 安装完成！")
            return True

        except Exception as e:
            self._log(f"Autovisor 安装失败: {e}")
            if moved_old and os.path.exists(backup_dir):
                try:
                    if os.path.exists(self.autovisor_path):
                        shutil.rmtree(self.autovisor_path)
                    os.replace(backup_dir, self.autovisor_path)
                    self._log("已回滚到旧版 Autovisor")
                except Exception as rollback_error:
                    self._log(f"回滚 Autovisor 失败: {rollback_error}")
            return False
        finally:
            for path in (temp_zip, temp_extract, preserve_dir):
                if os.path.exists(path):
                    try:
                        if os.path.isdir(path):
                            shutil.rmtree(path)
                        else:
                            os.remove(path)
                    except:
                        pass

    def check_yatori_update(self):
        """检查 Yatori 更新"""
        release_info = self.get_yatori_latest_release()
        if not release_info:
            return None
        
        latest_version = release_info['version']
        local_version = self.local_versions.get('yatori')
        installed = self.check_yatori_installed()

        if not installed:
            return {'has_update': True, 'installed': False, 'info': release_info}

        if local_version is None:
            local_version = self.YATORI_FALLBACK_LOCAL_VERSION
            self._log(
                "已检测到本地 Yatori 文件但暂无版本记录，"
                f"将按内置版本 {local_version} 比较"
            )

        comparison = self._compare_yatori_versions(
            latest_version,
            local_version,
        )
        if comparison is None:
            has_update = str(latest_version).strip() != str(local_version).strip()
        else:
            has_update = comparison > 0

        if has_update:
            return {
                'has_update': True,
                'installed': True,
                'version': local_version,
                'info': release_info,
            }

        local_newer = comparison is not None and comparison < 0
        if local_newer:
            self._log(
                f"本地 Yatori 版本 {local_version} 高于远端 {latest_version}，"
                "跳过降级"
            )
        
        return {
            'has_update': False,
            'installed': True,
            'version': local_version,
            'info': release_info,
            'local_newer': local_newer,
        }

    def check_autovisor_update(self):
        """检查 Autovisor 更新"""
        release_info = self.get_autovisor_latest_release()
        if not release_info:
            return None

        latest_version = release_info['version']
        installed = self.check_autovisor_installed()
        local_info = self.get_autovisor_local_version_info()
        local_version = local_info.get('compare')

        if not installed:
            return {'has_update': True, 'installed': False, 'info': release_info}

        if latest_version != local_version:
            return {
                'has_update': True,
                'installed': True,
                'version': local_info.get('display', local_version),
                'info': release_info,
            }

        return {
            'has_update': False,
            'installed': True,
            'version': local_info.get('display', local_version),
            'info': release_info,
        }
    
    def auto_check_and_prompt(self, callback=None):
        """自动检查更新并提示"""
        def check_thread():
            result = self.check_yatori_update()
            if callback:
                callback(result)
        
        thread = threading.Thread(target=check_thread, daemon=True)
        thread.start()
        return thread


if __name__ == "__main__":
    manager = CoreManager(os.path.dirname(os.path.abspath(__file__)))
    print("=== 核心管理器测试 (代理增强版) ===")
    print(f"Yatori 状态: {'已安装' if manager.check_yatori_installed() else '未安装'}")
    
    # 测试获取版本
    rel = manager.get_yatori_latest_release()
    if rel:
        print(f"检测到最新版本: {rel['version']}")
        print(f"下载链接: {rel['download_url']}")
