"""Course catalog parsing, identity-safe caching, and Xuexitong retrieval."""

from __future__ import annotations

import base64
import configparser
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urljoin

from src.atomic_io import atomic_dump_json


CACHE_TTL = timedelta(minutes=30)
XUEXITONG_AES_KEY = "u2oh6Vu^HWe4_AES"


class CourseCatalogError(ValueError):
    """Raised when source data cannot be mapped to the requested account."""


def normalize_account_index(value: Any) -> int:
    if isinstance(value, bool):
        raise CourseCatalogError("账号索引格式错误")
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise CourseCatalogError("账号索引格式错误") from exc
    if index < 0:
        raise CourseCatalogError("账号索引不能为负数")
    return index


def autovisor_account_section(account_index: int) -> str:
    index = normalize_account_index(account_index)
    return "user-account" if index == 0 else f"user-account-{index + 1}"


def read_autovisor_username(base_dir: str | Path, account_index: int) -> str:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(Path(base_dir) / "Autovisor" / "configs.ini", encoding="utf-8")
    return parser.get(
        autovisor_account_section(account_index),
        "username",
        fallback="",
    ).strip()


def _identity_digest(provider: str, identity: str) -> str:
    material = f"{provider}\0{identity.strip()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class CourseCatalogCache:
    """Atomic cache whose entries are bound to an account identity digest."""

    def __init__(
        self,
        path: str | Path,
        *,
        ttl: timedelta = CACHE_TTL,
        now: Callable[[], datetime] | None = None,
        logger: Callable[[str], None] | None = None,
    ):
        self.path = Path(path)
        self.ttl = ttl
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._logger = logger or (lambda _message: None)
        self._lock = threading.RLock()

    @staticmethod
    def key(provider: str, account_index: int) -> str:
        return f"{provider}_account_{normalize_account_index(account_index)}"

    def _read_all(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception as exc:
            self._logger(f"[课程缓存] 读取失败，将忽略损坏缓存: {exc}")
            return {}

    def get(self, provider: str, account_index: int, identity: str):
        identity = str(identity or "").strip()
        if not identity:
            return None
        with self._lock:
            entry = self._read_all().get(self.key(provider, account_index))
        if not isinstance(entry, dict):
            return None
        if entry.get("identity") != _identity_digest(provider, identity):
            return None
        try:
            cached_at = datetime.fromisoformat(str(entry["cached_at"]))
            if cached_at.tzinfo is None:
                return None
            age = self._now() - cached_at.astimezone(timezone.utc)
        except Exception:
            return None
        if age < timedelta(0) or age >= self.ttl:
            return None
        data = entry.get("data")
        return data if isinstance(data, dict) else None

    def put(
        self,
        provider: str,
        account_index: int,
        identity: str,
        data: dict,
    ) -> None:
        identity = str(identity or "").strip()
        if not identity or not isinstance(data, dict):
            return
        with self._lock:
            cache = self._read_all()
            cache[self.key(provider, account_index)] = {
                "cached_at": self._now().astimezone(timezone.utc).isoformat(),
                "identity": _identity_digest(provider, identity),
                "data": data,
            }
            atomic_dump_json(self.path, cache)


def _safe_list(value) -> list:
    return value if isinstance(value, list) else []


def get_zhs_course_access_id(raw: dict) -> str:
    """Read the runnable course identifier from old or current API fields."""
    if not isinstance(raw, dict):
        return ""
    for field in ("secret", "recruitAndCourseId"):
        value = str(raw.get(field) or "").strip()
        if value:
            return value
    return ""


def parse_zhs_course_data(
    data: dict,
    username: str = "",
) -> tuple[list[dict], str]:
    """Return normalized Zhihuishu courses and the selected source identity."""
    if not isinstance(data, dict) or not data:
        return [], username.strip()
    requested = username.strip()
    if requested:
        account_data = data.get(requested)
        if not isinstance(account_data, dict):
            return [], requested
        selected_identity = requested
    else:
        candidates = [
            (str(key), value)
            for key, value in data.items()
            if isinstance(value, dict)
        ]
        if len(candidates) != 1:
            raise CourseCatalogError("无法确定智慧树账号，课程文件包含多个账号")
        selected_identity, account_data = candidates[0]

    courses = []
    for raw in _safe_list(account_data.get("courses")):
        if not isinstance(raw, dict):
            continue
        secret = get_zhs_course_access_id(raw)
        if not secret:
            continue
        try:
            course_type = int(raw.get("courseType", 1))
        except (TypeError, ValueError):
            course_type = 0
        if course_type == 7:
            base_url = "https://wisdom-mooc.zhihuishu.com/study/index"
            type_label = "共享课"
        else:
            base_url = "https://studyvideoh5.zhihuishu.com/stuStudy"
            type_label = "普通课" if course_type == 1 else "未知类型"
        url = (
            f"{base_url}?{urlencode({'recruitAndCourseId': secret})}"
            if secret
            else ""
        )
        courses.append(
            {
                "name": str(raw.get("courseName") or "未知课程"),
                "title": str(raw.get("lessonName") or ""),
                "id": secret,
                "url": url,
                "progress": str(raw.get("progress") or "0%"),
                "type": type_label,
                "courseType": course_type,
            }
        )

    for notice in _safe_list(account_data.get("notices")):
        if not isinstance(notice, dict):
            continue
        live_id = str(notice.get("liveCourseId") or "").strip()
        course_id = str(notice.get("courseId") or "").strip()
        recruit_id = str(notice.get("recruitId") or "").strip()
        if not all((live_id, course_id, recruit_id)):
            continue
        query = urlencode(
            {
                "liveId": live_id,
                "courseId": course_id,
                "recruitId": recruit_id,
            }
        )
        course_name = str(notice.get("courseName") or "未知课程")
        task_name = str(notice.get("taskName") or "见面课")
        courses.append(
            {
                "name": f"{course_name} - {task_name}",
                "title": "见面课",
                "id": f"live_{live_id}",
                "url": f"https://lc.zhihuishu.com/live/vod_room.html?{query}",
                "progress": "-",
                "type": "见面课",
                "courseType": "live",
            }
        )
    return courses, selected_identity


def parse_xuexitong_course_data(data: dict) -> list[dict]:
    if not isinstance(data, dict):
        return []
    courses = []
    seen_names = set()
    for channel in _safe_list(data.get("channelList")):
        if not isinstance(channel, dict):
            continue
        content = channel.get("content")
        content = content if isinstance(content, dict) else {}
        course_object = content.get("course")
        course_object = course_object if isinstance(course_object, dict) else {}
        course_data = _safe_list(course_object.get("data"))
        main_name = str(content.get("name") or "").strip()
        if course_data:
            for raw in course_data:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name") or main_name).strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                courses.append(
                    {
                        "name": name,
                        "courseId": str(raw.get("id") or ""),
                        "teacher": str(raw.get("teacherfactor") or ""),
                        "school": str(raw.get("schools") or ""),
                        "imageurl": str(raw.get("imageurl") or ""),
                        "isstart": bool(content.get("isstart", False)),
                        "isretire": content.get("isretire", 0),
                    }
                )
        elif main_name and main_name not in seen_names:
            seen_names.add(main_name)
            courses.append(
                {
                    "name": main_name,
                    "courseId": str(channel.get("key") or ""),
                    "teacher": "",
                    "school": "",
                    "imageurl": "",
                    "isstart": bool(content.get("isstart", False)),
                    "isretire": content.get("isretire", 0),
                }
            )
    return courses


def is_xuexitong_course_payload(data: Any) -> bool:
    """Distinguish a valid empty course list from an error/login payload."""
    return isinstance(data, dict) and isinstance(data.get("channelList"), list)


def encrypt_xuexitong_credential(message: str) -> str:
    from Crypto.Cipher import AES

    key = XUEXITONG_AES_KEY.encode("utf-8")
    payload = str(message).encode("utf-8")
    padding = AES.block_size - len(payload) % AES.block_size
    padded = payload + bytes([padding] * padding)
    cipher = AES.new(key, AES.MODE_CBC, key)
    return base64.b64encode(cipher.encrypt(padded)).decode("ascii")


class CourseCatalogService:
    LOGIN_URL = "https://passport2.chaoxing.com/fanyalogin"
    COURSE_URL = (
        "https://mooc1-api.chaoxing.com/mycourse/"
        "backclazzdata?view=json&rss=1"
    )

    def __init__(
        self,
        base_dir: str | Path,
        *,
        logger: Callable[[str], None] | None = None,
        now: Callable[[], datetime] | None = None,
        session_factory: Callable[[], Any] | None = None,
    ):
        self.base_dir = Path(base_dir)
        self.log = logger or (lambda _message: None)
        self.cache = CourseCatalogCache(
            self.base_dir / "data" / "course_cache.json",
            now=now,
            logger=self.log,
        )
        self._session_factory = session_factory or self._create_http_session
        self._session = None
        self._session_identity = None

    @staticmethod
    def _create_http_session():
        import requests

        return requests.Session()

    def get_cached(self, provider: str, account_index: int, identity: str):
        return self.cache.get(provider, account_index, identity)

    def put_cached(
        self,
        provider: str,
        account_index: int,
        identity: str,
        result: dict,
    ) -> None:
        self.cache.put(provider, account_index, identity, result)

    def _session_for(self, username: str):
        if self._session is None or self._session_identity != username:
            self._session = self._session_factory()
            self._session.trust_env = False
            self._session_identity = username
        return self._session

    def get_xuexitong_courses(
        self,
        account_index: int,
        username: str,
        password: str,
        *,
        force_refresh: bool = False,
    ) -> dict:
        index = normalize_account_index(account_index)
        username = str(username or "").strip()
        password = str(password or "").strip()
        if not username or not password:
            return {"ok": False, "message": "账号或密码为空，请先在配置中填写"}
        if not force_refresh:
            cached = self.get_cached("xxt", index, username)
            if cached is not None:
                self.log("[学习通课程] 账号身份匹配，使用30分钟内缓存")
                return cached
        else:
            self.log("[学习通课程] 用户主动刷新，跳过本地课程缓存")

        session = self._session_for(username)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        login_data = {
            "fid": "-1",
            "uname": encrypt_xuexitong_credential(username),
            "password": encrypt_xuexitong_credential(password),
            "refer": "http%253A%252F%252Fi.chaoxing.com",
            "t": "true",
            "forbidotherlogin": "0",
            "validate": "",
            "doubleFactorLogin": "0",
            "independentId": "0",
        }
        try:
            response = session.post(
                self.LOGIN_URL,
                data=login_data,
                headers=headers,
                timeout=30,
                allow_redirects=False,
            )
        except Exception as exc:
            return {"ok": False, "message": f"登录请求失败: {exc}"}

        login_ok = response.status_code in (301, 302, 303, 307, 308)
        if login_ok:
            redirect_url = urljoin(
                self.LOGIN_URL,
                response.headers.get("Location", "https://i.chaoxing.com"),
            )
            try:
                session.get(redirect_url, headers=headers, timeout=30)
            except Exception as exc:
                self.log(
                    "[学习通课程] 登录跳转检查失败，继续尝试课程列表: "
                    f"{exc}"
                )
        else:
            try:
                login_result = response.json()
            except Exception:
                login_result = None
            if isinstance(login_result, dict):
                login_ok = (
                    login_result.get("mes") == "成功"
                    or login_result.get("status") is True
                )
                if not login_ok:
                    return {
                        "ok": False,
                        "message": "学习通登录失败: %s"
                        % login_result.get("mes", "未知错误"),
                    }
            else:
                login_ok = bool(session.cookies)
        if not login_ok:
            return {"ok": False, "message": "学习通登录失败，请检查账号密码"}

        self.log(f"[学习通课程] 登录成功 (cookies: {len(session.cookies)} 个)")
        course_headers = {
            "User-Agent": headers["User-Agent"],
            "Referer": "https://mooc1-1.chaoxing.com/visit/courses",
        }
        try:
            response = session.get(
                self.COURSE_URL,
                headers=course_headers,
                timeout=30,
            )
            if response.status_code != 200:
                return {
                    "ok": False,
                    "message": f"课程列表请求失败 (HTTP {response.status_code})",
                }
            payload = response.json()
        except Exception as exc:
            return {"ok": False, "message": f"课程列表请求失败: {exc}"}
        if not is_xuexitong_course_payload(payload):
            return {
                "ok": False,
                "message": "课程列表返回格式异常，可能登录已失效或接口已变化",
            }
        courses = parse_xuexitong_course_data(payload)
        result = {"ok": True, "courses": courses}
        self.put_cached("xxt", index, username, result)
        self.log(f"[学习通课程] 获取到 {len(courses)} 门课程")
        return result
