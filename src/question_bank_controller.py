"""Question-bank settings and local HTTP service lifecycle.

The controller deliberately has no WebView dependency. The launcher supplies
small callbacks for logging, ZError discovery, status changes, and syncing the
running server URL into Yatori configuration.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import time
import traceback
import urllib.request
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from src.atomic_io import atomic_dump_json, capture_file_state, restore_file_state

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
        self._zerror_db_logged = False
        self.prepare_environment = (
            prepare_environment or self.configure_zerror_environment
        )
        self.get_external_db_info = (
            get_external_db_info or self.get_zerror_db_info
        )

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
                    "ai_api_key": self.ai_api_key,
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
        try:
            file_snapshot = capture_file_state([self.config_path])
        except OSError as exc:
            return {"ok": False, "message": f"题库旧设置无法读取: {exc}"}
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

        restart_attempted = False
        try:
            self.apply_ai_config()
            if self.available:
                self.configure_auto_save_setting(enabled=self.auto_save)
            if was_running and port != previous["port"]:
                self.log(
                    f"[QB] 端口由 {previous['port']} 改为 {port}，正在重启题库服务器"
                )
                restart_attempted = True
                self.stop()
                if not self.start(silent=True):
                    return self._rollback_settings_update(
                        previous,
                        file_snapshot,
                        was_running=was_running,
                        restart_server=True,
                        failure_message=f"题库服务器未能在新端口 {port} 启动",
                    )
        except Exception as exc:
            return self._rollback_settings_update(
                previous,
                file_snapshot,
                was_running=was_running,
                restart_server=restart_attempted,
                failure_message=f"题库设置应用失败: {exc}",
            )
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

    def _rollback_settings_update(
        self,
        previous: dict,
        file_snapshot,
        *,
        was_running: bool,
        restart_server: bool,
        failure_message: str,
    ) -> dict:
        self._restore_settings(previous)
        details = []
        try:
            restore_file_state(file_snapshot)
        except OSError as exc:
            details.append(f"旧设置文件恢复失败: {exc}")

        try:
            self.apply_ai_config()
            if self.available:
                self.configure_auto_save_setting(enabled=self.auto_save)
        except Exception as exc:
            details.append(f"旧运行配置恢复失败: {exc}")

        if restart_server and was_running:
            try:
                if self.running or self.server:
                    self.stop()
                if not self.start(silent=True):
                    details.append(f"旧端口 {previous['port']} 的服务恢复失败")
            except Exception as exc:
                details.append(f"旧端口 {previous['port']} 的服务恢复失败: {exc}")

        suffix = f"；{'；'.join(details)}" if details else "，已恢复旧设置"
        return {"ok": False, "message": f"{failure_message}{suffix}"}

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

    @property
    def database_path(self) -> Path:
        return self.base_dir / "data" / "题库缓存.db"

    def import_questions(self, file_path: str | os.PathLike[str]) -> dict:
        if not self.available:
            return {"ok": False, "message": "题库服务器不可用"}
        try:
            data = json.loads(Path(file_path).read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return {"ok": False, "message": "文件格式错误：需要JSON数组"}
            self._ensure_database()
            imported = 0
            with closing(sqlite3.connect(self.database_path)) as connection:
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    question = item.get("question")
                    answer = item.get("answer")
                    if not question or not answer:
                        continue
                    if isinstance(answer, (list, dict)):
                        answer = json.dumps(answer, ensure_ascii=False)
                    options = item.get("options")
                    if isinstance(options, (list, dict)):
                        options = json.dumps(options, ensure_ascii=False)
                    connection.execute(
                        "INSERT INTO AIResponses "
                        "(Question, Answer, Options, QuestionType, IsAi) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            question,
                            answer,
                            options,
                            item.get("type"),
                            int(bool(item.get("is_ai", False))),
                        ),
                    )
                    imported += 1
                connection.commit()
            self.log(f"[QB] 成功导入 {imported} 条题目")
            return {"ok": True, "count": imported, "message": f"已导入 {imported} 条题目"}
        except Exception as exc:
            self.log(f"[QB] 导入失败: {exc}")
            return {"ok": False, "message": str(exc)}

    def export_questions(self, file_path: str | os.PathLike[str]) -> dict:
        try:
            rows = []
            if self.database_path.exists():
                self._ensure_database()
                with closing(sqlite3.connect(self.database_path)) as connection:
                    rows = connection.execute(
                        "SELECT Question, Answer, Options, QuestionType, IsAi "
                        "FROM AIResponses"
                    ).fetchall()
            data = [
                {
                    "question": row[0],
                    "answer": row[1],
                    "options": row[2],
                    "type": row[3],
                    "is_ai": bool(row[4]),
                }
                for row in rows
            ]
            Path(file_path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.log(f"[QB] 成功导出 {len(data)} 条题目到 {file_path}")
            return {"ok": True, "count": len(data), "message": f"已导出 {len(data)} 条题目"}
        except Exception as exc:
            self.log(f"[QB] 导出失败: {exc}")
            return {"ok": False, "message": str(exc)}

    def clear_questions(self) -> dict:
        if not self.database_path.exists():
            return {"ok": True, "count": 0, "message": "题库缓存为空，无需清空"}
        try:
            self._ensure_database()
            with closing(sqlite3.connect(self.database_path)) as connection:
                cursor = connection.execute("DELETE FROM AIResponses")
                count = max(cursor.rowcount, 0)
                connection.commit()
            self.log(f"[QB] 已清空题库缓存，共删除 {count} 条记录")
            return {"ok": True, "count": count, "message": f"已清空题库缓存，共删除 {count} 条记录"}
        except Exception as exc:
            self.log(f"[QB] 清空题库缓存失败: {exc}")
            return {"ok": False, "message": str(exc)}

    def deduplicate_questions(self) -> dict:
        if self.running and self.server:
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{getattr(self.server, 'port', self.port)}/api/deduplicate",
                    method="POST",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    result = json.loads(response.read().decode("utf-8"))
                if result.get("success"):
                    deleted = int(result.get("deleted", 0) or 0)
                    message = f"去重完成，已清理 {deleted} 条重复记录"
                    self.log(f"[QB] {message}")
                    return {"ok": True, "count": deleted, "message": message}
                return {
                    "ok": False,
                    "message": result.get("message", "去重失败"),
                }
            except Exception:
                self.log("[QB] HTTP 去重失败，回退到直接操作数据库...")

        try:
            deleted = self._deduplicate_database()
            message = (
                f"去重完成，已清理 {deleted} 条重复记录"
                if deleted
                else "未发现重复记录"
            )
            self.log(f"[QB] {message}")
            return {"ok": True, "count": deleted, "message": message}
        except Exception as exc:
            self.log(f"[QB] 去重失败: {exc}")
            return {"ok": False, "message": str(exc)}

    def _ensure_database(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS AIResponses (
                    Id INTEGER PRIMARY KEY AUTOINCREMENT,
                    Question TEXT NOT NULL,
                    Options TEXT,
                    QuestionType TEXT,
                    Answer TEXT NOT NULL,
                    CreateTime DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FolderId INTEGER DEFAULT 0,
                    FolderName TEXT DEFAULT '默认文件夹',
                    IsAi BOOLEAN DEFAULT 1,
                    IsPendingCorrection BOOLEAN DEFAULT 0
                )
                """
            )
            connection.commit()

    def _deduplicate_database(self) -> int:
        if not self.database_path.exists():
            return 0
        self._ensure_database()

        def normalize(text) -> str:
            value = str(text or "").strip().lower()
            value = re.sub(r"\s+", "", value)
            value = value.replace("&nbsp;", " ")
            return re.sub(r"[，、；：。！？【】《》\"\"''（）…—·]+", "", value)

        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT Id, Question, COALESCE(Options, '') "
                "FROM AIResponses ORDER BY CreateTime ASC, Id ASC"
            ).fetchall()
            seen = set()
            duplicate_ids = []
            for row_id, question, options in rows:
                key = (normalize(question), normalize(options))
                if key in seen:
                    duplicate_ids.append(row_id)
                else:
                    seen.add(key)
            connection.executemany(
                "DELETE FROM AIResponses WHERE Id = ?",
                [(row_id,) for row_id in duplicate_ids],
            )
            connection.commit()
        return len(duplicate_ids)

    def detect_zerror_db_path(self) -> str:
        candidates = []
        configured = os.environ.get("ZERROR_DB_PATH", "").strip()
        if configured:
            candidates.append(configured)
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            candidates.append(os.path.join(local_appdata, "ZError", "airesponses.db"))
        user_profile = os.environ.get("USERPROFILE", "").strip()
        if user_profile:
            candidates.append(
                os.path.join(user_profile, "AppData", "Local", "ZError", "airesponses.db")
            )
        candidates.append(str(self.base_dir / "airesponses.db"))
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                return os.path.abspath(candidate)
        return ""

    def get_zerror_db_info(self) -> str:
        path = self.detect_zerror_db_path()
        if not path:
            return ""
        try:
            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute("SELECT COUNT(*) FROM AIResponses").fetchone()[0]
            return f"{count} 条记录 ({path})"
        except Exception:
            return f"已检测 ({path})"

    def configure_zerror_environment(self) -> None:
        path = self.detect_zerror_db_path()
        if path:
            os.environ["ZERROR_DB_PATH"] = path
            if not self._zerror_db_logged:
                self.log(f"📦 检测到 ZError 题库数据库: {self.get_zerror_db_info()}")
        elif not self._zerror_db_logged:
            self.log("⚠️ 未检测到 ZError 题库数据库，将使用本地缓存")
        self._zerror_db_logged = True

    @staticmethod
    def _is_port_listening(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            return sock.connect_ex(("127.0.0.1", port)) == 0
