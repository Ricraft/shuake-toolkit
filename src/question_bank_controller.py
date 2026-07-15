"""Question-bank settings and local HTTP service lifecycle.

The controller deliberately has no WebView dependency. The launcher supplies
small callbacks for logging, ZError discovery, status changes, and syncing the
running server URL into Yatori configuration.
"""

from __future__ import annotations

import json
import os
import socket
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from src.atomic_io import atomic_dump_json

try:
    from src.题库服务器 import (
        QuestionBankServer,
        configure_ai_models,
        configure_auto_save,
    )

    QUESTION_BANK_AVAILABLE = True
except ImportError:  # pragma: no cover - packaging/runtime dependency failure
    QuestionBankServer = None
    configure_ai_models = lambda **_kwargs: None
    configure_auto_save = lambda **_kwargs: None
    QUESTION_BANK_AVAILABLE = False


class QuestionBankController:
    """Own question-bank settings and server state."""

    DEFAULT_PORT = 8083

    def __init__(
        self,
        base_dir: str | os.PathLike[str],
        *,
        log: Callable[[str], None],
        server_factory=None,
        configure_models: Callable[..., None] = configure_ai_models,
        configure_auto_save_setting: Callable[..., None] = configure_auto_save,
        prepare_environment: Callable[[], None] | None = None,
        get_external_db_info: Callable[[], str] | None = None,
        on_status_change: Callable[[], None] | None = None,
        sync_external_url: Callable[[], None] | None = None,
        port_checker: Callable[[int], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        available: bool | None = None,
    ):
        self.base_dir = Path(base_dir)
        self.log = log
        self.server_factory = server_factory if server_factory is not None else QuestionBankServer
        self.configure_models = configure_models
        self.configure_auto_save_setting = configure_auto_save_setting
        self.prepare_environment = prepare_environment or (lambda: None)
        self.get_external_db_info = get_external_db_info or (lambda: "")
        self.on_status_change = on_status_change or (lambda: None)
        self.sync_external_url = sync_external_url or (lambda: None)
        self.port_checker = port_checker or self._is_port_listening
        self.sleep = sleep
        self.available = bool(
            QUESTION_BANK_AVAILABLE if available is None else available
        ) and self.server_factory is not None

        self.server = None
        self.running = False
        self.port = self.DEFAULT_PORT
        self.auto_start = True
        self.ai_enabled = True
        self.ai_url = ""
        self.ai_model = ""
        self.ai_api_key = ""
        self.ai_type = "OPENAI"
        self.ai_concurrent = True
        self.auto_save = True

        self.load_settings()

    @property
    def config_path(self) -> Path:
        return self.base_dir / "data" / "qb_config.json"

    def load_settings(self) -> None:
        """Load persisted settings without overwriting a damaged file."""
        try:
            if not self.config_path.exists():
                return
            saved = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                return

            self.auto_start = bool(saved.get("auto_start", True))
            self.ai_enabled = bool(saved.get("ai_enabled", True))
            self.ai_type = str(saved.get("ai_type", "OPENAI"))
            self.ai_url = str(saved.get("ai_url", ""))
            self.ai_model = str(saved.get("ai_model", ""))
            self.ai_api_key = str(saved.get("ai_api_key", "")) or os.environ.get(
                "QB_AI_API_KEY", ""
            )
            self.auto_save = bool(saved.get("auto_save", True))
            try:
                saved_port = int(saved.get("port", self.DEFAULT_PORT))
            except (TypeError, ValueError):
                saved_port = self.DEFAULT_PORT
            self.port = (
                saved_port if 1024 <= saved_port <= 65535 else self.DEFAULT_PORT
            )
            self.log("[QB] 已从本地加载题库设置")
            self.apply_ai_config()
            if self.available:
                self.configure_auto_save_setting(enabled=self.auto_save)
        except Exception as exc:
            self.log(f"[QB] 加载题库设置失败: {exc}")

    def apply_ai_config(self) -> None:
        if not self.available:
            return
        try:
            models = []
            if self.ai_enabled and self.ai_api_key:
                models.append(
                    {
                        "type": self.ai_type or "OPENAI",
                        "url": self.ai_url or "",
                        "model": self.ai_model or "",
                        "api_key": self.ai_api_key or "",
                    }
                )
            self.configure_models(
                models=models,
                enabled=self.ai_enabled,
                concurrent=self.ai_concurrent,
            )
            self.log(
                f"[QB] AI配置已应用 (enabled={self.ai_enabled}, models={len(models)})"
            )
        except Exception as exc:
            self.log(f"[QB] AI配置应用失败: {exc}")

    def save_settings(self) -> bool:
        try:
            atomic_dump_json(
                self.config_path,
                {
                    "auto_start": self.auto_start,
                    "ai_enabled": self.ai_enabled,
                    "ai_type": self.ai_type,
                    "ai_url": self.ai_url,
                    "ai_model": self.ai_model,
                    # API keys stay in memory or QB_AI_API_KEY, never on disk.
                    "auto_save": self.auto_save,
                    "port": self.port,
                },
            )
            self.log("[QB] 题库设置已保存到本地")
            return True
        except Exception as exc:
            self.log(f"[QB] 保存题库设置失败: {exc}")
            return False

    def get_settings(self) -> dict:
        return {
            "auto_start": self.auto_start,
            "ai_enabled": self.ai_enabled,
            "ai_type": self.ai_type,
            "ai_url": self.ai_url,
            "ai_model": self.ai_model,
            "ai_api_key": self.ai_api_key,
            "auto_save": self.auto_save,
            "port": self.port,
        }

    def update_settings(self, payload) -> dict:
        if not isinstance(payload, dict):
            return {"ok": False, "message": "题库设置格式错误"}
        try:
            port = int(payload.get("port", self.DEFAULT_PORT))
        except (TypeError, ValueError):
            return {"ok": False, "message": "题库端口必须是整数"}
        if not 1024 <= port <= 65535:
            return {"ok": False, "message": "题库端口必须在 1024 到 65535 之间"}

        previous = self.get_settings()
        was_running = self.running
        self.auto_start = bool(payload.get("auto_start", True))
        self.ai_enabled = bool(payload.get("ai_enabled", False))
        self.ai_type = str(payload.get("ai_type", "OPENAI"))
        self.ai_url = str(payload.get("ai_url", ""))
        self.ai_model = str(payload.get("ai_model", ""))
        self.ai_api_key = str(payload.get("ai_api_key", ""))
        self.auto_save = bool(payload.get("auto_save", True))
        self.port = port

        if not self.save_settings():
            self._restore_settings(previous)
            return {"ok": False, "message": "题库设置写入失败"}

        self.apply_ai_config()
        if self.available:
            self.configure_auto_save_setting(enabled=self.auto_save)
        if was_running and port != previous["port"]:
            self.log(
                f"[QB] 端口由 {previous['port']} 改为 {port}，正在重启题库服务器"
            )
            self.stop()
            if not self.start(silent=True):
                return {
                    "ok": False,
                    "message": f"题库服务器未能在新端口 {port} 启动",
                }
        return {"ok": True, "message": "题库设置已保存"}

    def _restore_settings(self, settings: dict) -> None:
        self.auto_start = settings["auto_start"]
        self.ai_enabled = settings["ai_enabled"]
        self.ai_type = settings["ai_type"]
        self.ai_url = settings["ai_url"]
        self.ai_model = settings["ai_model"]
        self.ai_api_key = settings["ai_api_key"]
        self.auto_save = settings["auto_save"]
        self.port = settings["port"]

    def auto_start_if_enabled(self) -> bool:
        if not self.available:
            return False
        self.prepare_environment()
        self.on_status_change()
        return not self.auto_start or self.start(silent=True)

    def toggle(self) -> bool:
        if self.running:
            self.stop()
            return False
        return self.start()

    def start(self, silent: bool = False) -> bool:
        if not self.available:
            if not silent:
                self.log("题库服务器模块未找到，请确保 题库服务器.py 在 src 目录下")
            return False
        if self.running:
            return True

        try:
            self.prepare_environment()
            if self.server:
                self.server.stop()
            self.server = self.server_factory(port=self.port)
            self.log(f"[QB] 创建题库服务器实例，端口={self.port}")
            self.server.start()
            self.sleep(0.5)

            for attempt in range(1, 6):
                if self.port_checker(self.port):
                    self.log(f"[QB] 端口 {self.port} 验证成功")
                    break
                self.log(f"[QB] 端口验证失败 (尝试 {attempt}/5)")
                if attempt < 5:
                    self.sleep(0.3)
            else:
                raise OSError(f"题库服务器启动后端口 {self.port} 未监听")

            self.running = True
            self.apply_ai_config()
            self.on_status_change()
            if silent:
                self.log(f"题库服务器已自动启动 → {self.server.url}")
            else:
                self.log(f"题库服务器已启动 → {self.server.url}")
                external_info = self.get_external_db_info()
                if external_info:
                    self.log(f"📦 ZError题库已内置: {external_info}")
            self.sync_external_url()
            return True
        except OSError as exc:
            self.log(f"题库服务器启动失败(端口{self.port}不可用): {exc}")
            self.log(f"[QB] 详细错误: {traceback.format_exc()[:500]}")
        except Exception as exc:
            self.log(f"题库服务器启动异常: {exc}")
            self.log(f"[QB] 详细错误: {traceback.format_exc()[:500]}")

        failed_server = self.server
        self.running = False
        self.server = None
        if failed_server:
            try:
                failed_server.stop()
            except Exception:
                pass
        self.on_status_change()
        return False

    def stop(self) -> None:
        if self.server and self.running:
            try:
                self.server.stop()
            except Exception as exc:
                self.log(f"[QB] 停止题库服务器时出现异常: {exc}")
        self.running = False
        self.server = None
        self.on_status_change()
        self.log("题库服务器已停止")

    def get_stats(self):
        if self.running and self.server:
            return self.server.get_stats()
        return None

    def get_query_url(self) -> str | None:
        if self.running and self.server:
            return f"{self.server.url}/query"
        return None

    @staticmethod
    def _is_port_listening(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            return sock.connect_ex(("127.0.0.1", port)) == 0
