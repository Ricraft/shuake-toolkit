"""扫码登录桥接：在 venv-zhs 解释器下运行，向 stdout 输出 JSON 事件行。

用法：
    <venv-zhs python> qr_login.py <二维码 PNG 路径>
    （工作目录必须是账号数据目录，zk 数据写入 .zhs/）

事件格式（每行一个 JSON 对象）：
    {"event": "qr", "image_path": "...", "data_url": "data:image/png;base64,..."}
    {"event": "scanned"}
    {"event": "ok", "uuid": "...", "cookie_count": 12}
    {"event": "error", "message": "..."}

注意：stdout 只允许输出事件，日志一律关闭（loguru 全部 sink 移除），
否则启动器解析会失败。
"""

from __future__ import annotations

import base64
import json
import sys


def emit(event: str, **payload: object) -> None:
    sys.stdout.write(json.dumps({"event": event, **payload}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    image_path = argv[1] if len(argv) > 1 else ""

    try:
        from loguru import logger as _logger

        _logger.remove()  # 关闭所有 sink，stdout 只留给 JSON 事件
    except Exception:  # pragma: no cover - loguru 缺失时继续
        pass

    try:
        from pathlib import Path

        from zhs.config import ConfigManager
        from zhs.login import LoginManager
        from zhs.session import ZhsSession

        config = ConfigManager().load()
        if not image_path:
            image_path = str(Path(".zhs") / "qrcode.png")
        config.qr.image_path = image_path

        session = ZhsSession(config)
        manager = LoginManager(session, config)

        def on_qr(img_bytes: bytes) -> None:
            emit(
                "qr",
                image_path=str(image_path),
                data_url="data:image/png;base64," + base64.b64encode(img_bytes).decode("ascii"),
            )

        result = manager.login_with_qr(
            on_qr,
            on_scanned=lambda: emit("scanned"),
            image_path=image_path,
        )

        if not result.success:
            emit("error", message="扫码登录未完成")
            return 1

        cookies_path = Path(".zhs") / "cookies.json"
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        manager.save_cookies(cookies_path)

        cookie_count = len(session.cookies) if session.cookies is not None else 0
        emit("ok", uuid=result.uuid or "", cookie_count=cookie_count)
        return 0
    except BaseException as exc:  # noqa: BLE001 - 任何异常都要变成事件
        emit("error", message=f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main(sys.argv))
