"""Tool-calling agent layer for the Lo Tianyi assistant."""

from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from collections.abc import Callable

from src.course_overlay_service import CourseOverlayError
from src.course_plan_service import normalize_course_name


PREFERENCE_WHITELIST = (
    "autoShutdown",
    "closeLauncherOnComplete",
    "autoRun",
    "notifyOnComplete",
    "notifyOnError",
    "soundEnabled",
    "minimizeToTray",
    "alwaysOnTop",
)

PREFERENCE_LABELS = {
    "autoShutdown": "刷完自动关机",
    "closeLauncherOnComplete": "刷完自动关闭程序",
    "autoRun": "启动后自动运行",
    "notifyOnComplete": "任务完成通知",
    "notifyOnError": "任务失败通知",
    "soundEnabled": "提示音",
    "minimizeToTray": "关闭时最小化到托盘",
    "alwaysOnTop": "窗口置顶",
}

CORE_LABELS = {"yatori": "Yatori", "autovisor": "Autovisor"}

AGENT_SYSTEM_PROMPT = (
    "你可以调用工具真正操作这个程序。需要读取状态或课程时直接调用只读工具；"
    "需要启动/停止刷课、切换偏好设置时必须调用对应工具，系统会先向用户展示确认卡片，"
    "用户确认后才真正执行。不要在回复里编造操作结果，也不要输出任何形式的指令标记；"
    "一次只提交一个写操作。工具执行结果会以 tool 消息返回给你。"
    "\n注意：查找课程 find_course 只读本地已有信息，绝不会打开浏览器；"
    "查找课程时如果课程表、核心设置和本地缓存都没命中，"
    "系统会自动拉一次 Yatori 账号课程（后台登录，不弹窗）。"
    "如果还没找到，会返回 needs_autovisor_fetch：这时你要先征得用户同意，"
    "再调用 refresh_account_courses（core=autovisor），因为它会真开浏览器。"
)


def _tool(name, description, properties, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
            },
        },
    }

TOOL_SPECS = [
    _tool(
        "get_status",
        "查询 Yatori / Autovisor / 题库服务器的当前运行状态。",
        {},
    ),
    _tool(
        "list_courses",
        "列出用户在课程表里预先填写的课程。",
        {
            "core": {
                "type": "string",
                "enum": ["yatori", "autovisor"],
                "description": "只列某个核心的课程，可省略。",
            }
        },
    ),
    _tool(
        "find_course",
        "按用户说的课程名查找课程：先查课程表，再查核心设置里已配置的课程和本地缓存。"
        "都没有时会自动拉一次 Yatori 账号课程（后台登录，不弹窗）；不会自动打开 Autovisor 浏览器，也不启动刷课。",
        {
            "query": {"type": "string", "description": "用户说的课程名"},
            "core": {
                "type": "string",
                "enum": ["yatori", "autovisor"],
                "description": "只在这一个核心里查找，可省略。",
            },
        },
        ("query",),
    ),
    _tool(
        "start_course",
        "只刷指定的这一门课。用户说帮我刷某门课时调用。",
        {
            "course": {"type": "string", "description": "课程名"},
            "core": {
                "type": "string",
                "enum": ["yatori", "autovisor"],
                "description": "指定用哪个核心，可省略。",
            },
            "skip_questions": {
                "type": "boolean",
                "description": "用户说不用做题时填 true（仅 Yatori 生效）。",
            },
            "max_minutes": {
                "type": "string",
                "description": "本次学习时长上限，例如 45 或 10-30。",
            },
            "account_index": {
                "type": "integer",
                "description": "账号序号，从 0 开始，可省略。",
            },
        },
        ("course",),
    ),
    _tool(
        "refresh_account_courses",
        "拉取账号里的课程列表（Yatori 和 Autovisor 都有“获取课程”）。"
        "Yatori 是后台登录不弹窗口；Autovisor 会打开浏览器登录智慧树。"
        "两者都会联网，必须先经用户同意才能调用。",
        {
            "core": {
                "type": "string",
                "enum": ["yatori", "autovisor"],
                "description": "只拉某个核心的账号课程，可省略。",
            },
            "account_index": {
                "type": "integer",
                "description": "账号序号，从 0 开始，可省略。",
            },
            "for_course": {
                "type": "string",
                "description": "正在找的课程名；填了之后系统会告诉用户拉到的课程里有没有它。",
            },
        },
    ),
    _tool(
        "control_runtime",
        "启动或停止刷课核心。",
        {
            "action": {
                "type": "string",
                "enum": ["start", "stop", "start_all", "stop_all"],
            },
            "core": {
                "type": "string",
                "enum": ["yatori", "autovisor"],
                "description": "action 为 start/stop 时必填。",
            },
        },
        ("action",),
    ),
    _tool(
        "set_preference",
        "开关软件设置，例如刷完自动关机、刷完自动关闭程序。",
        {
            "key": {
                "type": "string",
                "enum": list(PREFERENCE_WHITELIST),
            },
            "value": {"type": "boolean"},
        },
        ("key", "value"),
    ),
]

WRITE_TOOLS = {
    "start_course",
    "control_runtime",
    "set_preference",
    "refresh_account_courses",
}

class TianyiAgentService:
    """Run a bounded tool loop and require confirmation for write actions."""

    MAX_ROUNDS = 3
    PENDING_TTL = 300.0

    def __init__(
        self,
        launcher,
        *,
        ai_service=None,
        logger: Callable[[str], None] | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.launcher = launcher
        self._ai_service = ai_service
        self.log = logger or getattr(launcher, "log_system", lambda _m: None)
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._pending: dict[str, dict] = {}

    def _ai(self):
        if self._ai_service is None:
            self._ai_service = self.launcher._get_ai_service()
        return self._ai_service

    def _course_run(self):
        return self.launcher._get_course_run_service()

    def tool_specs(self) -> list:
        return copy.deepcopy(TOOL_SPECS)

    @staticmethod
    def _parse_args(raw) -> dict:
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _status(self) -> dict:
        running = getattr(self.launcher, "running", {}) or {}
        question_bank = getattr(self.launcher, "question_bank", None)
        return {
            "yatori_running": bool(running.get("yatori")),
            "autovisor_running": bool(running.get("autovisor")),
            "practice_running": bool(running.get("practice")),
            "question_bank_running": bool(
                getattr(question_bank, "running", False)
            ),
            "shutdown_pending": bool(
                getattr(self.launcher, "_shutdown_pending", False)
            ),
        }

    def _list_courses(self, args) -> dict:
        core = args.get("core") or None
        if core not in (None, "yatori", "autovisor"):
            core = None
        try:
            courses = self._course_run().list_known_courses(core)
        except Exception:
            try:
                courses = self.launcher._get_course_plan_service().list(core=core)
            except Exception as exc:
                return {"ok": False, "message": f"读取课程失败: {exc}"}
        return {"ok": True, "courses": courses}

    def _find_course(self, args) -> dict:
        query = str(args.get("query") or "").strip()
        core = args.get("core") or None
        if core not in (None, "yatori", "autovisor"):
            core = None
        if not query:
            return {"ok": False, "message": "没有提供课程名"}
        account_index = args.get("account_index")
        result = self._course_run().resolve_smart(
            query,
            core=core,
            account_index=0 if account_index is None else account_index,
        )
        if result.get("ok"):
            entry = result["entry"]
            return {
                "ok": True,
                "found": True,
                "name": entry.get("name"),
                "core": entry.get("core"),
                "accountIndex": entry.get("accountIndex"),
                "source": result.get("source"),
            }
        return {
            "ok": False,
            "found": False,
            "code": result.get("code"),
            "message": result.get("message"),
            "available": result.get("available") or [],
            "candidates": [
                item.get("name") for item in result.get("candidates") or []
            ],
        }

    def _validate(self, name: str, args: dict):
        if name == "control_runtime":
            action = args.get("action")
            core = args.get("core")
            if action not in ("start", "stop", "start_all", "stop_all"):
                return "action 必须是 start/stop/start_all/stop_all"
            if action in ("start", "stop") and core not in (
                "yatori",
                "autovisor",
            ):
                return "请说明要操作哪个核心（yatori 或 autovisor）"
            return None
        if name == "refresh_account_courses":
            core = args.get("core")
            if core is not None and core not in ("yatori", "autovisor"):
                return "core 只能是 yatori 或 autovisor"
            return None
        if name == "set_preference":
            if args.get("key") not in PREFERENCE_WHITELIST:
                return f"不支持修改偏好项: {args.get('key')}"
            if not isinstance(args.get("value"), bool):
                return "偏好值必须是 true 或 false"
            return None
        if name == "start_course":
            if not str(args.get("course") or "").strip():
                return "请提供课程名"
            core = args.get("core")
            if core is not None and core not in ("yatori", "autovisor"):
                return "core 只能是 yatori 或 autovisor"
            return None
        return None

    @staticmethod
    def _core_text(core) -> str:
        return CORE_LABELS.get(core or "", "") or "自动选择核心"

    def _summarize(self, name: str, args: dict) -> str:
        if name == "start_course":
            course = str(args.get("course") or "").strip()
            detail = [self._core_text(args.get("core"))]
            if args.get("skip_questions"):
                detail.append("不做题")
            if str(args.get("max_minutes") or "").strip():
                detail.append(f"时长上限 {str(args['max_minutes']).strip()} 分钟")
            return f"单刷课程《{course}》（{'，'.join(detail)}）"
        if name == "control_runtime":
            action = args.get("action")
            core = self._core_text(args.get("core"))
            labels = {
                "start": f"启动 {core}",
                "stop": f"停止 {core}",
                "start_all": "一键启动全部核心",
                "stop_all": "停止全部核心",
            }
            return labels.get(action, str(action))
        if name == "refresh_account_courses":
            core = args.get("core")
            if core == "yatori":
                return "拉取 Yatori 账号课程（后台登录，不弹窗）"
            if core == "autovisor":
                return "拉取 Autovisor 账号课程（会打开浏览器登录）"
            return "拉取 Yatori 和 Autovisor 的账号课程（Autovisor 会打开浏览器登录）"
        if name == "set_preference":
            key = args.get("key")
            label = PREFERENCE_LABELS.get(key, key)
            return f"{'开启' if args.get('value') else '关闭'}「{label}」"
        return name

    def _describe_result(self, action: str, args: dict, result: dict) -> str:
        if action == "refresh_account_courses":
            courses = result.get("courses")
            names = []
            for item in courses if isinstance(courses, list) else []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
            if not result.get("ok"):
                return str(result.get("message") or "拉取账号课程失败")
            target = str(args.get("for_course") or "").strip()
            if not names:
                text = "拉取完成，但这个账号下没有返回任何课程（可能还没选课，或登录没成功）。"
                if target:
                    text += f"暂时没找到《{target}》。"
                return text
            preview = "、".join(f"《{name}》" for name in names[:8])
            if len(names) > 8:
                preview += f" 等共 {len(names)} 门"
            text = f"拉取完成，共 {len(names)} 门课程：{preview}。"
            if target:
                wanted = normalize_course_name(target)
                hit = [
                    name
                    for name in names
                    if wanted
                    and (
                        normalize_course_name(name) == wanted
                        or wanted in normalize_course_name(name)
                        or normalize_course_name(name) in wanted
                    )
                ]
                if hit:
                    text += f"\n其中就有《{hit[0]}》，要我现在单刷这一门吗？"
                else:
                    text += f"\n里面没有《{target}》。"
            return text
        return str(result.get("message") or result.get("summary") or "")

    def _make_pending(self, name: str, args: dict) -> dict:
        action_id = uuid.uuid4().hex
        record = {
            "action": name,
            "args": dict(args),
            "created": self._clock(),
        }
        with self._lock:
            self._prune_locked()
            self._pending[action_id] = record
        return {
            "id": action_id,
            "action": name,
            "args": dict(args),
            "summary": self._summarize(name, args),
        }

    def _prune_locked(self) -> None:
        now = self._clock()
        expired = [
            key
            for key, record in self._pending.items()
            if now - float(record.get("created") or 0) > self.PENDING_TTL
        ]
        for key in expired:
            self._pending.pop(key, None)

    def pending_ids(self) -> list[str]:
        with self._lock:
            self._prune_locked()
            return list(self._pending)

    def _dispatch(self, call: dict) -> dict:
        name = str(call.get("name") or "")
        args = self._parse_args(call.get("arguments"))
        if name == "get_status":
            return {"tool": name, "ok": True, "result": self._status()}
        if name == "list_courses":
            return {"tool": name, "ok": True, "result": self._list_courses(args)}
        if name == "find_course":
            return {"tool": name, "ok": True, "result": self._find_course(args)}
        if name in WRITE_TOOLS:
            problem = self._validate(name, args)
            if problem:
                return {
                    "tool": name,
                    "ok": False,
                    "result": {"ok": False, "message": problem},
                }
            return {"pending": self._make_pending(name, args)}
        return {
            "tool": name,
            "ok": False,
            "result": {"ok": False, "message": f"未知工具: {name}"},
        }

    def _execute(self, name: str, args: dict) -> dict:
        if name == "start_course":
            try:
                return self._course_run().start(
                    args.get("course"),
                    core=args.get("core") or None,
                    account_index=args.get("account_index"),
                    skip_questions=args.get("skip_questions"),
                    max_minutes=args.get("max_minutes"),
                )
            except CourseOverlayError as exc:
                return {"ok": False, "message": str(exc)}
        if name == "control_runtime":
            action = args.get("action")
            core = args.get("core") if action in ("start", "stop") else None
            result = self.launcher.perform_web_action(action, core)
            return result if isinstance(result, dict) else {"ok": bool(result)}
        if name == "refresh_account_courses":
            core = args.get("core")
            if core not in ("yatori", "autovisor"):
                core = None
            try:
                return self._course_run().fetch_catalog(
                    core,
                    args.get("account_index") or 0,
                )
            except Exception as exc:
                return {"ok": False, "message": f"拉取账号课程失败: {exc}"}
        if name == "refresh_account_courses":
            core = args.get("core")
            if core not in ("yatori", "autovisor"):
                core = None
            try:
                return self._course_run().fetch_catalog(
                    core,
                    args.get("account_index") or 0,
                )
            except Exception as exc:
                return {"ok": False, "message": f"拉取账号课程失败: {exc}"}
        if name == "set_preference":
            key = args.get("key")
            value = bool(args.get("value"))
            result = self.launcher.save_web_preference({key: value})
            return result if isinstance(result, dict) else {"ok": bool(result)}
        return {"ok": False, "message": f"未知操作: {name}"}

    def confirm(self, action_id: str) -> dict:
        with self._lock:
            self._prune_locked()
            record = self._pending.pop(str(action_id or ""), None)
        if not record:
            return {
                "ok": False,
                "code": "pending_expired",
                "message": "这条待确认操作已经失效，请重新说一次",
            }
        result = self._execute(record["action"], record["args"])
        result = dict(result) if isinstance(result, dict) else {"ok": bool(result)}
        result.setdefault(
            "summary", self._summarize(record["action"], record["args"])
        )
        result.setdefault(
            "detail",
            self._describe_result(record["action"], record["args"], result),
        )
        return result

    def cancel(self, action_id: str) -> dict:
        with self._lock:
            removed = bool(self._pending.pop(str(action_id or ""), None))
        return {"ok": True, "cancelled": removed}

    def _history_with_agent_prompt(self, messages) -> list:
        history = [dict(m) for m in messages if isinstance(m, dict)]
        for message in history:
            if str(message.get("role")) == "system":
                content = str(message.get("content") or "")
                message["content"] = f"{content}\n\n{AGENT_SYSTEM_PROMPT}"
                return history
        history.insert(0, {"role": "system", "content": AGENT_SYSTEM_PROMPT})
        return history

    def _fallback(self, config, messages, *, reason: str) -> dict:
        self.log(f"[天依] 工具调用不可用，回退到文本指令协议: {reason}")
        return {
            "ok": True,
            "mode": "fallback",
            "fallbackReason": str(reason),
            "reply": "",
            "actions": [],
            "pending": None,
        }

    def chat(self, config, messages) -> dict:
        history = self._history_with_agent_prompt(messages)
        actions = []
        reply = ""
        for round_index in range(self.MAX_ROUNDS):
            result = self._ai().chat_with_tools(
                config,
                history,
                self.tool_specs(),
            )
            if not result.get("ok"):
                if round_index == 0:
                    return self._fallback(
                        config,
                        messages,
                        reason=result.get("message") or "工具调用失败",
                    )
                return {
                    "ok": True,
                    "mode": "tools",
                    "reply": reply
                    or "操作已经处理，但我没能生成总结，请看上面的执行结果。",
                    "actions": actions,
                    "pending": None,
                }
            reply = str(result.get("reply") or "")
            tool_calls = result.get("tool_calls") or []
            if not tool_calls:
                return {
                    "ok": True,
                    "mode": "tools",
                    "reply": reply,
                    "actions": actions,
                    "pending": None,
                }

            assistant_message = {"role": "assistant", "content": reply}
            serialized = []
            for index, call in enumerate(tool_calls):
                call_id = str(call.get("id") or f"call_{index}")
                arguments = call.get("arguments")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments or {}, ensure_ascii=False)
                serialized.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": str(call.get("name") or ""),
                            "arguments": arguments,
                        },
                    }
                )
            assistant_message["tool_calls"] = serialized
            history.append(assistant_message)

            for index, call in enumerate(tool_calls):
                outcome = self._dispatch(call)
                if outcome.get("pending"):
                    pending = outcome["pending"]
                    text = reply.strip()
                    hint = f"要我现在{pending['summary']}吗？确认后我立刻执行。"
                    return {
                        "ok": True,
                        "mode": "tools",
                        "reply": f"{text}\n\n{hint}".strip(),
                        "actions": actions,
                        "pending": pending,
                    }
                if outcome.get("ok"):
                    actions.append(outcome["result"])
                call_id = str(call.get("id") or f"call_{index}")
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(
                            outcome.get("result") or {},
                            ensure_ascii=False,
                        ),
                    }
                )
        return {
            "ok": True,
            "mode": "tools",
            "reply": reply or "任务已完成。",
            "actions": actions,
            "pending": None,
        }
