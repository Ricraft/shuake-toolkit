"""登录态校验桥接：在 venv-zhs 解释器下运行，向 stdout 输出一行 JSON。

用法：
    <venv-zhs python> session_check.py
    （工作目录必须是账号数据目录，读取 .zhs/cookies.json）

输出（一行 JSON）：
    {"status": "ok", "cookie_count": 12, "uuid": "..."}
    {"status": "expired", "cookie_count": 12, "message": "..."}
    {"status": "unavailable", "cookie_count": 12, "message": "..."}
    {"status": "none", "cookie_count": 0, "message": "..."}
    {"status": "error", "cookie_count": 0, "message": "..."}

说明：
- 判定依据是服务端 getLoginUserInfo 是否返回用户信息（zhs 的 verify_session），
  仅看本地 cookies.json 里有没有 CASTGC/SESSION 无法发现凭证过期。
- 校验用 CASTGC 换 CAS ticket，会补种 onlineservice-api 域的 SESSION cookie，
  因此只有在 status=ok 时回写 cookies.json，后续 fetch/play 可直接复用。
- stdout 只允许输出这一行 JSON（loguru 全部 sink 已移除），否则启动器解析会失败。
"""

from __future__ import annotations

import json
import sys


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    try:
        from loguru import logger as _logger

        _logger.remove()  # 关闭所有 sink，stdout 只留给 JSON
    except Exception:  # pragma: no cover - loguru 缺失时继续
        pass

    try:
        from zhs.api.session_auth import SessionStatus, verify_session
        from zhs.config import ConfigManager
        from zhs.session import ZhsSession
        from zhs.utils.cookie import cookies_to_list, read_cookies_file
        from zhs.utils.path import get_data_dir

        cookies_path = get_data_dir() / "cookies.json"
        raw = read_cookies_file(cookies_path)
        if raw is None:
            emit({"status": "none", "cookie_count": 0, "message": "未找到 Cookie 文件"})
            return 0

        config = ConfigManager().load()
        session = ZhsSession(config, max_retries=1)
        session.cookies = raw
        status = verify_session(session)

        if status is SessionStatus.OK:
            try:
                cookies_path.write_text(
                    json.dumps(cookies_to_list(session.cookies), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass
            emit({"status": "ok", "cookie_count": len(raw), "uuid": session.uuid or ""})
            return 0

        if status is SessionStatus.EXPIRED:
            emit({"status": "expired", "cookie_count": len(raw), "message": "登录凭证已过期"})
            return 0

        emit({"status": "unavailable", "cookie_count": len(raw), "message": "无法连接智慧树服务器"})
        return 0
    except BaseException as exc:  # noqa: BLE001 - 任何异常都要变成 JSON
        emit({"status": "error", "cookie_count": 0, "message": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
