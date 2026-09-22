"""Resolve a spoken course name and launch exactly that one course."""

from __future__ import annotations

from collections.abc import Callable

from src.course_overlay_service import CourseOverlayError


class CourseRunService:
    """Bridge the pre-filled registry, the account catalogs and core launch."""

    def __init__(
        self,
        launcher,
        *,
        plans,
        overlays,
        autovisor_catalog: Callable[[int], dict] | None = None,
        yatori_catalog: Callable[[int], dict] | None = None,
    ):
        self.launcher = launcher
        self.plans = plans
        self.overlays = overlays
        self._autovisor_catalog = autovisor_catalog
        self._yatori_catalog = yatori_catalog

    # ---------- helpers ----------
    @staticmethod
    def _account_index(value) -> int:
        if value is None or isinstance(value, bool):
            return 0
        try:
            index = int(value)
        except (TypeError, ValueError):
            return 0
        return max(index, 0)

    def _yatori_account(self, index: int):
        users = (self.launcher._load_yatori_config_data() or {}).get("users") or []
        if index < 0 or index >= len(users):
            return None
        account = users[index]
        return account if isinstance(account, dict) else None

    def _autovisor_account(self, index: int):
        accounts = (
            self.launcher._load_autovisor_config_data() or {}
        ).get("accounts") or []
        if index < 0 or index >= len(accounts):
            return None
        account = accounts[index]
        return account if isinstance(account, dict) else None

    def _catalog_entries(self, core: str, account_index: int) -> dict:
        if core == "yatori":
            account = self._yatori_account(account_index)
            if account is None:
                return {
                    "ok": False,
                    "code": "account_missing",
                    "message": f"Yatori 账号索引 {account_index} 不存在",
                }
            if not str(account.get("account") or "").strip() or not str(
                account.get("password") or ""
            ):
                return {
                    "ok": False,
                    "code": "account_missing",
                    "message": (
                        f"Yatori 账号 {account_index + 1} 还没填账号或密码，"
                        "请先在核心设置页补齐后再试"
                    ),
                }
            provider = self._yatori_catalog
        else:
            account = self._autovisor_account(account_index)
            if account is None:
                return {
                    "ok": False,
                    "code": "account_missing",
                    "message": f"Autovisor 账号索引 {account_index} 不存在",
                }
            if not str(account.get("username") or "").strip() or not str(
                account.get("password") or ""
            ):
                return {
                    "ok": False,
                    "code": "account_missing",
                    "message": (
                        f"Autovisor 账号 {account_index + 1} 还没填账号或密码，"
                        "请先在核心设置页补齐后再试"
                    ),
                }
            provider = self._autovisor_catalog

        if provider is None:
            return {
                "ok": False,
                "code": "catalog_unavailable",
                "message": "课程目录接口不可用",
            }
        try:
            result = provider(account_index) or {}
        except Exception as exc:
            return {
                "ok": False,
                "code": "catalog_failed",
                "message": f"拉取 {core} 课程失败: {exc}",
            }
        if not isinstance(result, dict) or not result.get("ok"):
            message = "拉取课程失败"
            if isinstance(result, dict) and result.get("message"):
                message = str(result["message"])
            return {"ok": False, "code": "catalog_failed", "message": message}
        entries = []
        for raw in result.get("courses") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            entries.append(
                {
                    "id": f"{core}-catalog-{len(entries)}",
                    "name": name,
                    "aliases": [],
                    "core": core,
                    "accountIndex": account_index,
                    "courseUrl": str(
                        raw.get("url") or raw.get("courseUrl") or ""
                    ).strip(),
                    "skipQuestions": False,
                    "maxMinutes": "",
                    "source": "catalog",
                }
            )
        return {"ok": True, "entries": entries}

    # ---------- 已配置 / 本地缓存课程 ----------
    @staticmethod
    def _entry_from_cached(core, account_index, raw, index):
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name") or "").strip()
        if not name:
            return None
        return {
            "id": f"{core}-cached-{index}",
            "name": name,
            "aliases": [],
            "core": core,
            "accountIndex": account_index,
            "courseUrl": str(raw.get("url") or raw.get("courseUrl") or "").strip(),
            "skipQuestions": False,
            "maxMinutes": "",
            "source": "cache",
        }

    def _cached_courses(self, provider, catalog_index, username):
        if not username:
            return []
        try:
            service = self.launcher._get_course_catalog_service()
            cached = service.get_cached(provider, catalog_index, username)
        except Exception:
            return []
        if not isinstance(cached, dict):
            return []
        courses = cached.get("courses")
        return courses if isinstance(courses, list) else []

    def _autovisor_catalog_index(self, index):
        account = self._autovisor_account(index)
        if account is None:
            return index
        try:
            account_number = int(account.get("account_id") or (index + 1))
        except (TypeError, ValueError):
            account_number = index + 1
        return max(account_number - 1, 0)

    def _configured_yatori_entries(self):
        """读取 Yatori 核心设置里已填的“只刷这些课程”。"""
        users = (
            self.launcher._load_yatori_config_data() or {}
        ).get("users") or []
        entries = []
        for index, user in enumerate(users):
            if not isinstance(user, dict):
                continue
            custom = user.get("coursesCustom")
            if not isinstance(custom, dict):
                continue
            names = custom.get("includeCourses")
            if not isinstance(names, list):
                continue
            for name in names:
                text = str(name or "").strip()
                if not text:
                    continue
                entries.append(
                    {
                        "id": f"yatori-configured-{index}-{len(entries)}",
                        "name": text,
                        "aliases": [],
                        "core": "yatori",
                        "accountIndex": index,
                        "courseUrl": "",
                        "skipQuestions": False,
                        "maxMinutes": "",
                        "source": "configured",
                    }
                )
        return entries

    def _configured_autovisor_entries(self):
        """已配置的智慧树课程链接，名称取自本地缓存。"""
        accounts = (
            self.launcher._load_autovisor_config_data() or {}
        ).get("accounts") or []
        entries = []
        for index, account in enumerate(accounts):
            if not isinstance(account, dict):
                continue
            urls = account.get("course_urls")
            if not isinstance(urls, list) or not urls:
                continue
            username = str(account.get("username") or "").strip()
            catalog_index = self._autovisor_catalog_index(index)
            by_url = {}
            for item in self._cached_courses("zhs", catalog_index, username):
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                name = str(item.get("name") or "").strip()
                if url and name:
                    by_url[url] = name
            for url in urls:
                text = str(url or "").strip()
                name = by_url.get(text) or ""
                if not text or not name:
                    continue
                entries.append(
                    {
                        "id": f"autovisor-configured-{index}-{len(entries)}",
                        "name": name,
                        "aliases": [],
                        "core": "autovisor",
                        "accountIndex": index,
                        "courseUrl": text,
                        "skipQuestions": False,
                        "maxMinutes": "",
                        "source": "configured",
                    }
                )
        return entries

    def _configured_entries(self, cores):
        entries = []
        if "yatori" in cores:
            entries.extend(self._configured_yatori_entries())
        if "autovisor" in cores:
            entries.extend(self._configured_autovisor_entries())
        return entries

    def _cached_catalog_entries(self, core, index):
        if core == "yatori":
            account = self._yatori_account(index)
            if account is None:
                return {
                    "ok": False,
                    "code": "account_missing",
                    "message": f"Yatori 账号索引 {index} 不存在",
                }
            username = str(account.get("account") or "").strip()
            courses = self._cached_courses("xxt", index, username)
        else:
            account = self._autovisor_account(index)
            if account is None:
                return {
                    "ok": False,
                    "code": "account_missing",
                    "message": f"Autovisor 账号索引 {index} 不存在",
                }
            username = str(account.get("username") or "").strip()
            courses = self._cached_courses(
                "zhs",
                self._autovisor_catalog_index(index),
                username,
            )
        entries = []
        for raw in courses:
            entry = self._entry_from_cached(core, index, raw, len(entries))
            if entry:
                entries.append(entry)
        return {"ok": True, "entries": entries}

    def _pick_match(self, text, entries):
        matches = self.plans.match(text, entries or [])
        if not matches:
            return None
        matches.sort(
            key=lambda item: (-item[1], -len(item[0].get("name") or ""))
        )
        best = matches[0][1]
        top = [item for item in matches if abs(item[1] - best) < 0.001]
        distinct = {(item[0]["core"], item[0]["name"]) for item in top}
        if len(distinct) > 1:
            return {
                "ok": False,
                "code": "ambiguous",
                "message": "匹配到多门课程，请说明是哪一门，或用核心名限定",
                "candidates": [item[0] for item in top],
            }
        entry, score = matches[0]
        return {
            "ok": True,
            "entry": dict(entry),
            "score": score,
            "source": entry.get("source") or "registry",
        }

    def _available_names(self, cores, limit=20):
        names = []
        for entry in self._configured_entries(cores):
            if entry["name"] not in names:
                names.append(entry["name"])
        for entry in self.plans.list():
            if cores and entry["core"] not in cores:
                continue
            if entry["name"] not in names:
                names.append(entry["name"])
        return names[:limit]

    # ---------- resolution ----------
    def resolve(
        self,
        query,
        *,
        core=None,
        account_index=0,
        allow_fetch: bool = False,
    ) -> dict:
        """先查课程表，再查核心设置里已填的课程，然后只读本地缓存。

        只有 allow_fetch=True 时才会真正拉取账号课程（可能打开浏览器）。
        """
        text = str(query or "").strip()
        if not text:
            return {
                "ok": False,
                "code": "empty_query",
                "message": "没有识别到课程名",
            }
        if core not in ("yatori", "autovisor"):
            core = None
        index = self._account_index(account_index)
        cores = [core] if core else ["yatori", "autovisor"]

        registry = self.plans.resolve(text, core=core, account_index=None)
        if registry.get("ok") or registry.get("code") == "ambiguous":
            return registry

        matched = self._pick_match(text, self._configured_entries(cores))
        if matched:
            return matched

        cached_entries = []
        for name in cores:
            result = self._cached_catalog_entries(name, index)
            if result.get("ok"):
                cached_entries.extend(result["entries"])
        matched = self._pick_match(text, cached_entries)
        if matched:
            return matched

        failures = []
        if allow_fetch:
            fetched = []
            for name in cores:
                result = self._catalog_entries(name, index)
                if not result.get("ok"):
                    failures.append(result)
                    continue
                fetched.extend(result["entries"])
            matched = self._pick_match(text, fetched)
            if matched:
                return matched
            if failures and all(
                item.get("code") == "account_missing" for item in failures
            ):
                return {
                    "ok": False,
                    "code": "account_missing",
                    "message": failures[0]["message"],
                }
            if failures:
                return {
                    "ok": False,
                    "code": failures[0].get("code", "catalog_failed"),
                    "message": failures[0]["message"],
                }

        return {
            "ok": False,
            "code": "course_not_configured",
            "message": (
                f"课程表里没有「{text}」，"
                "请先在课程表里配置，"
                "或告诉我去拉取一次账号课程"
            ),
            "available": self._available_names(cores),
        }

    def _yatori_fetch_blocker(self, index):
        account = self._yatori_account(index)
        if account is None:
            return {
                "ok": False,
                "code": "account_missing",
                "message": f"Yatori 账号索引 {index} 不存在",
            }
        if not str(account.get("account") or "").strip() or not str(
            account.get("password") or ""
        ):
            return {
                "ok": False,
                "code": "account_missing",
                "message": (
                    f"Yatori 账号 {index + 1} 还没填账号或密码，"
                    "请先在核心设置里补齐，我才能去拉取课程"
                ),
            }
        return None

    def _has_yatori_cache(self, index) -> bool:
        account = self._yatori_account(index)
        if account is None:
            return False
        username = str(account.get("account") or "").strip()
        return bool(self._cached_courses("xxt", index, username))

    def resolve_smart(self, query, *, core=None, account_index=0) -> dict:
        """先只读解析；未命中时自动拉一次 Yatori（后台登录不弹窗）。

        Yatori 拉完仍未命中时不会自动打开 Autovisor 浏览器，
        而是返回 needs_autovisor_fetch，等用户确认后再拉。
        """
        if core not in ("yatori", "autovisor"):
            core = None
        index = self._account_index(account_index)
        result = self.resolve(
            query,
            core=core,
            account_index=index,
            allow_fetch=False,
        )
        if result.get("ok") or result.get("code") == "ambiguous":
            return result

        blocker = None
        if core in (None, "yatori"):
            blocker = self._yatori_fetch_blocker(index)
            if blocker is not None:
                if core == "yatori":
                    return blocker
            elif not self._has_yatori_cache(index):
                fetched = self.fetch_catalog("yatori", index)
                if not fetched.get("ok"):
                    if core == "yatori":
                        return fetched
                else:
                    retry = self.resolve(
                        query,
                        core=core,
                        account_index=index,
                        allow_fetch=False,
                    )
                    if retry.get("ok") or retry.get("code") == "ambiguous":
                        return retry
                    result = retry

        if core in (None, "autovisor"):
            if blocker is not None:
                return {
                    "ok": False,
                    "code": "account_missing",
                    "message": (
                        blocker["message"]
                        + "；如果这门课在智慧树，也可以让我打开浏览器拉取 Autovisor 账号课程。"
                    ),
                    "available": result.get("available") or [],
                }
            return {
                "ok": False,
                "code": "needs_autovisor_fetch",
                "message": (
                    f"在课程表、核心设置和 Yatori 账号课程里都没找到"
                    f"「{str(query or '').strip()}」。如果它在智慧树，"
                    "需要你允许我打开浏览器拉取 Autovisor 账号课程。"
                ),
                "available": result.get("available")
                or self._available_names(["yatori", "autovisor"]),
            }
        return result

    def list_known_courses(self, core=None, account_index=0) -> list[dict]:
        """课程表 + 核心设置里已配置的课程（去重）。"""
        if core not in ("yatori", "autovisor"):
            core = None
        cores = [core] if core else ["yatori", "autovisor"]
        seen = set()
        result = []
        for entry in self.plans.list(core=core):
            key = (entry["core"], entry["name"])
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "name": entry["name"],
                    "core": entry["core"],
                    "source": "registry",
                }
            )
        for entry in self._configured_entries(cores):
            key = (entry["core"], entry["name"])
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "name": entry["name"],
                    "core": entry["core"],
                    "source": "configured",
                }
            )
        return result

    def fetch_catalog(self, core, account_index=0) -> dict:
        """真正拉取账号课程，仅在用户同意后调用。

        Yatori 是后台 HTTP 登录，不弹窗口；
        Autovisor 会启动 Playwright 浏览器登录智慧树。
        """
        explicit = core if core in ("yatori", "autovisor") else None
        cores = [explicit] if explicit else ["yatori", "autovisor"]
        index = self._account_index(account_index)
        collected = []
        failures = []
        for name in cores:
            result = self._catalog_entries(name, index)
            if not result.get("ok"):
                failures.append(
                    {
                        "core": name,
                        "code": result.get("code"),
                        "message": result.get("message"),
                    }
                )
                continue
            collected.extend(result["entries"])

        if explicit and failures:
            return {
                "ok": False,
                "core": explicit,
                "code": failures[0].get("code") or "catalog_failed",
                "message": failures[0].get("message"),
            }
        if not collected and failures:
            return {
                "ok": False,
                "code": failures[0].get("code") or "catalog_failed",
                "message": failures[0].get("message"),
            }
        response = {
            "ok": True,
            "courses": [
                {
                    "name": item["name"],
                    "core": item["core"],
                    "courseUrl": item["courseUrl"],
                }
                for item in collected
            ],
        }
        if failures:
            response["failed"] = failures
            response["message"] = "；".join(
                str(item.get("message")) for item in failures
            )
        return response

    # ---------- launch ----------
    def start(
        self,
        query,
        *,
        core=None,
        account_index=None,
        skip_questions=None,
        max_minutes=None,
    ) -> dict:
        resolved = self.resolve_smart(
            query,
            core=core,
            account_index=0 if account_index is None else account_index,
        )
        if not resolved.get("ok"):
            return resolved
        entry = dict(resolved["entry"])
        if account_index is not None:
            entry["accountIndex"] = self._account_index(account_index)
        if skip_questions is not None:
            entry["skipQuestions"] = bool(skip_questions)
        if max_minutes is not None and str(max_minutes).strip():
            entry["maxMinutes"] = str(max_minutes).strip()
        if entry.get("core") == "autovisor":
            return self._start_autovisor(entry)
        return self._start_yatori(entry)

    def _start_yatori(self, entry: dict) -> dict:
        launcher = self.launcher
        if launcher.running.get("yatori") or launcher.starting.get("yatori"):
            return {
                "ok": False,
                "code": "already_running",
                "message": "Yatori 已经在运行，请先停止后再启动单课程",
            }
        index = self._account_index(entry.get("accountIndex"))
        account = self._yatori_account(index)
        if account is None:
            return {
                "ok": False,
                "code": "account_missing",
                "message": f"Yatori 账号索引 {index} 不存在",
            }
        chapter = None
        if entry.get("skipQuestions"):
            chapter = {
                "cxChapterTestSw": 0,
                "cxWorkSw": 0,
                "cxExamSw": 0,
            }
        try:
            self.overlays.apply_yatori(
                config_path=launcher._get_yatori_config_path(),
                account_index=index,
                include_courses=[entry["name"]],
                exclude_courses=[],
                chapter_switches=chapter,
                study_time=entry.get("maxMinutes") or None,
            )
        except CourseOverlayError as exc:
            return {
                "ok": False,
                "code": "overlay_failed",
                "message": f"写入单课程配置失败: {exc}",
            }
        started = False
        try:
            started = bool(launcher.start_yatori())
        except Exception as exc:
            launcher.log_system(f"Yatori 单课程启动异常: {exc}")
        if not started:
            self.overlays.release("yatori")
            message = getattr(launcher, "_last_start_error", {}).get(
                "yatori"
            ) or "Yatori 启动失败"
            return {"ok": False, "code": "start_failed", "message": message}
        detail = f"已开始单刷 Yatori 课程《{entry['name']}》"
        if entry.get("skipQuestions"):
            detail += "（不做章节测试/作业/考试）"
        if entry.get("maxMinutes"):
            detail += f"，学习时长上限 {entry['maxMinutes']} 分钟"
        return {
            "ok": True,
            "core": "yatori",
            "course": entry["name"],
            "message": detail,
        }

    def _start_autovisor(self, entry: dict) -> dict:
        launcher = self.launcher
        if launcher.running.get("autovisor") or launcher.starting.get(
            "autovisor"
        ):
            return {
                "ok": False,
                "code": "already_running",
                "message": "Autovisor 已经在运行，请先停止后再启动单课程",
            }
        index = self._account_index(entry.get("accountIndex"))
        account = self._autovisor_account(index)
        if account is None:
            return {
                "ok": False,
                "code": "account_missing",
                "message": f"Autovisor 账号索引 {index} 不存在",
            }
        course_url = str(entry.get("courseUrl") or "").strip()
        if not course_url:
            fallback = self._cached_catalog_entries("autovisor", index)
            if not fallback.get("ok"):
                return {
                    "ok": False,
                    "code": fallback.get("code", "catalog_failed"),
                    "message": (
                        "课程表里这门课没有课程链接，且无法从账号课程里补全："
                        + str(fallback.get("message") or "")
                    ),
                }
            matched = self.plans.match(entry["name"], fallback["entries"])
            if not matched:
                return {
                    "ok": False,
                    "code": "course_not_configured",
                    "message": (
                        f"账号课程里没有《{entry['name']}》，"
                        "请先点‘获取课程’或在课程表里补上链接"
                    ),
                }
            course_url = matched[0][0]["courseUrl"]
        try:
            account_id = int(account.get("account_id") or (index + 1))
        except (TypeError, ValueError):
            account_id = index + 1

        try:
            result = launcher.start_autovisor_course(
                course_url=course_url,
                account_id=account_id,
                max_minutes=entry.get("maxMinutes") or None,
            )
        except Exception as exc:
            return {
                "ok": False,
                "code": "start_failed",
                "message": f"Autovisor 单课程启动异常: {exc}",
            }
        if not result:
            message = getattr(launcher, "_last_start_error", {}).get(
                "autovisor"
            ) or "Autovisor 启动失败"
            return {"ok": False, "code": "start_failed", "message": message}
        detail = f"已开始单刷智慧树课程《{entry['name']}》"
        if entry.get("maxMinutes"):
            detail += f"，单课程时长上限 {entry['maxMinutes']} 分钟"
        return {
            "ok": True,
            "core": "autovisor",
            "course": entry["name"],
            "message": detail,
        }

    def release_overlay(self, core) -> bool:
        try:
            return bool(self.overlays.release(core))
        except Exception as exc:
            try:
                self.launcher.log_system(f"单课程配置恢复失败: {exc}")
            except Exception:
                pass
            return False
