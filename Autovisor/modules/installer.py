import glob
import importlib
import os
import platform
import re
import shutil
import sys
import traceback
import zipfile
from importlib import import_module

import requests

from modules.configs import Config
from modules.logger import Logger
from modules.progress import show_progress

config = Config()
logger = Logger()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_SITE_PACKAGES = os.path.join(PROJECT_ROOT, "runtime_deps")
LEGACY_RES_DIR = os.path.join(PROJECT_ROOT, "res")

packages = {
    "numpy": "2.4.1",
    "opencv-python": "4.13.0.92",
}

mapping = {
    "numpy": "numpy",
    "opencv-python": "cv2",
}

PACKAGE_CLEANUP_PATTERNS = {
    "numpy": ("numpy", "numpy.libs", "numpy-*.dist-info", "numpy-*.whl"),
    "opencv-python": ("cv2", "opencv_python-*.dist-info", "opencv-python-*.whl"),
}


def test_mirrors():
    for name, url in config.mirrors.items():
        logger.info(f"正在测试 {name} 镜像源...")
        try:
            response = requests.get(url + "/simple/0", headers=config.headers, timeout=5)
            if response.status_code == 200:
                logger.info(f"{name} 镜像源 连接成功!")
                return name, url
            logger.error(f"{name} 镜像源 连接失败(状态码 {response.status_code})!")
        except requests.exceptions.RequestException as exc:
            logger.error(f"{name} 镜像源 连接失败: {exc}")
    logger.error("所有镜像源都不可用!")
    return None, None


def extract_whl(whl_path, extract_to):
    if not zipfile.is_zipfile(whl_path):
        raise ValueError(f"{whl_path} 不是一个有效的 .whl 文件!")
    with zipfile.ZipFile(whl_path, "r") as whl_zip:
        destination = os.path.abspath(extract_to)
        for member in whl_zip.infolist():
            target = os.path.abspath(os.path.join(destination, member.filename))
            if os.path.commonpath((destination, target)) != destination:
                raise ValueError(f"wheel 包含越界路径: {member.filename}")
        whl_zip.extractall(extract_to)
    logger.info(f"已将 {whl_path} 解压到: {extract_to}")


def get_system_arch():
    return "win_amd64" if platform.architecture()[0] == "64bit" else "win32"


def normalize_wheel_filename(link):
    return os.path.basename(link.split("#")[0].split("?")[0])


def get_supported_wheel_score(link):
    filename = normalize_wheel_filename(link)
    if not filename.endswith(".whl"):
        return None

    parts = filename[:-4].rsplit("-", 3)
    if len(parts) != 4:
        return None

    py_tags = parts[1].split(".")
    abi_tags = parts[2].split(".")
    platform_tags = parts[3].split(".")
    arch = get_system_arch()

    if arch not in platform_tags and "any" not in platform_tags:
        return None

    exact_cp_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    implementation = sys.implementation.name.lower()

    if implementation == "cpython":
        if any(tag.startswith("pp") for tag in py_tags):
            return None
        if exact_cp_tag in py_tags and exact_cp_tag in abi_tags:
            return 500
        if exact_cp_tag in py_tags and "abi3" in abi_tags:
            return 450
        if exact_cp_tag in py_tags and "none" in abi_tags:
            return 420
        if any(tag.startswith("cp3") for tag in py_tags) and "abi3" in abi_tags:
            return 350
        if any(tag in {"py3", f"py{sys.version_info.major}", f"py{sys.version_info.major}{sys.version_info.minor}"} for tag in py_tags) and "none" in abi_tags:
            return 250
        return None

    return None


def pick_best_wheel_link(whl_links, version=None):
    candidates = whl_links
    if version:
        candidates = [link for link in whl_links if version in link]
        if not candidates:
            raise ValueError(f"找不到版本为 {version} 的 wheel 文件")

    scored = []
    for link in candidates:
        score = get_supported_wheel_score(link)
        if score is not None:
            scored.append((score, link))

    if not scored:
        runtime_name = f"{sys.implementation.name} {sys.version_info.major}.{sys.version_info.minor} {get_system_arch()}"
        raise ValueError(f"当前运行时环境 {runtime_name} 没有可用的兼容 wheel")

    scored.sort(key=lambda item: (item[0], normalize_wheel_filename(item[1])))
    return scored[-1][1]


def remove_path(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        os.remove(path)


def cleanup_package_targets(package, *base_dirs):
    for base_dir in base_dirs:
        if not base_dir or not os.path.isdir(base_dir):
            continue
        for pattern in PACKAGE_CLEANUP_PATTERNS.get(package, ()):
            for matched in glob.glob(os.path.join(base_dir, pattern)):
                remove_path(matched)


def download_wheel(mirror_name, base_url, package_name, version=None):
    package_url = f"{base_url}/simple/{package_name}/"
    logger.info(f"正在从镜像源下载 {package_name}.whl 文件...")
    response = requests.get(package_url, headers=config.headers, timeout=20)
    response.raise_for_status()

    arch = get_system_arch()
    pattern = re.compile(rf'href="(?:\.\./)*([^"]+{arch}\.whl[^"]+)"')
    whl_links = pattern.findall(response.text)
    if not whl_links:
        raise ValueError(f"没有找到适用于当前架构的 {package_name}.whl 文件")

    wheel_link = pick_best_wheel_link(whl_links, version)
    wheel_url = f"{base_url}/{wheel_link}" if mirror_name != "官方" else wheel_link
    whl_path = os.path.join(RUNTIME_SITE_PACKAGES, normalize_wheel_filename(wheel_url))

    response = requests.get(wheel_url, headers=config.headers, stream=True, timeout=60)
    response.raise_for_status()
    total_size = int(response.headers.get("content-length", 0))
    with open(whl_path, "wb") as handle:
        for chunk in response.iter_content(chunk_size=512):
            if chunk:
                handle.write(chunk)
                show_progress("下载进度:", current=handle.tell(), total=total_size)

    logger.info(f"{whl_path} 下载完成!")
    return whl_path


def is_installed(package, version):
    try:
        module = import_module(mapping[package])
        logger.info(f"{package}-{version} 已安装!")
        return module, True
    except ImportError:
        return None, False


def install_package(package, version, mirror_name, base_url):
    alias = mapping[package]
    logger.info(f"{package}-{version} 未安装, 开始下载...")

    wheel_path = None
    try:
        os.makedirs(RUNTIME_SITE_PACKAGES, exist_ok=True)
        cleanup_package_targets(package, RUNTIME_SITE_PACKAGES, LEGACY_RES_DIR)

        wheel_path = download_wheel(mirror_name, base_url, package, version)
        extract_whl(wheel_path, RUNTIME_SITE_PACKAGES)
        importlib.invalidate_caches()

        module = import_module(alias)
        logger.info(f"{package}-{version} 安装完成!")
        return module
    except Exception as exc:
        error_message = f"{package}-{version} 处理失败!\n错误详情: {repr(exc)}"
        logger.write_log(f"[ERROR] {error_message}\n{traceback.format_exc()}")
        logger.error(error_message)
        return None
    finally:
        if wheel_path:
            try:
                if os.path.exists(wheel_path):
                    os.remove(wheel_path)
            except OSError as exc:
                logger.warn(f"清理临时 wheel 文件失败: {wheel_path}: {exc}")


def start():
    modules = []
    os.makedirs(RUNTIME_SITE_PACKAGES, exist_ok=True)
    if RUNTIME_SITE_PACKAGES not in sys.path:
        sys.path.insert(0, RUNTIME_SITE_PACKAGES)

    logger.info(
        f"当前运行时环境: Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} ({sys.implementation.name})"
    )

    mirror_name, base_url = None, None
    for package, version in packages.items():
        module, exist = is_installed(package, version)
        if not exist:
            if not mirror_name:
                mirror_name, base_url = test_mirrors()
                if not mirror_name:
                    logger.error("没有可用的镜像源, 程序终止")
                    sys.exit(-1)
            module = install_package(package, version, mirror_name, base_url)
            if not module:
                logger.save()
                sys.exit(-1)
        modules.append(module)

    return modules


if __name__ == "__main__":
    start()
