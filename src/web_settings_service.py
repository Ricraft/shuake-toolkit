"""Transactional Web configuration saving for all launcher components."""

from __future__ import annotations

from src.atomic_io import capture_file_state, restore_file_state


class SettingsValidationError(ValueError):
    """A stable user-facing validation failure."""


class WebSettingsService:
    """Normalize, validate, and atomically save the three Web config groups."""

    YATORI_SETTING_SECTIONS = (
        "basicSetting",
        "emailInform",
        "aiSetting",
        "apiQueSetting",
    )

    def __init__(self, launcher):
        self.launcher = launcher

    def _log(self, message):
        try:
            self.launcher.log_system(message)
        except Exception:
            pass

    def _with_state(self, response):
        result = dict(response)
        try:
            result["state"] = self.launcher.get_web_initial_state()
        except Exception as exc:
            result["stateUnavailable"] = True
            if result.get("ok"):
                result.setdefault(
                    "message",
                    "配置已保存，但界面状态暂未刷新",
                )
            self._log(f"配置保存后的 Web 状态刷新失败: {exc}")
        return result

    def _failure(self, message):
        return self._with_state({"ok": False, "message": str(message)})

    @staticmethod
    def _validate_question_bank_payload(qb_data):
        if not isinstance(qb_data, dict):
            return
        try:
            port = int(qb_data.get("port", 8083))
        except (TypeError, ValueError) as exc:
            raise SettingsValidationError("题库端口必须是整数") from exc
        if not 1024 <= port <= 65535:
            raise SettingsValidationError(
                "题库端口必须在 1024 到 65535 之间"
            )

    def _normalize_autovisor(self, autovisor_data):
        launcher = self.launcher
        raw_accounts = autovisor_data.get("accounts")
        if not raw_accounts:
            raw_accounts = [launcher._default_autovisor_account(1)]
        if not isinstance(raw_accounts, list):
            raise SettingsValidationError(
                "Autovisor 账号列表格式无效"
            )

        accounts = []
        for index, raw_account in enumerate(raw_accounts, start=1):
            if not isinstance(raw_account, dict):
                raise SettingsValidationError(
                    f"Autovisor 账号 {index} 的配置格式无效"
                )
            account = dict(raw_account)
            account["limit_speed"] = launcher._normalize_autovisor_speed(
                account.get("limit_speed", "1.0"),
                "1.0",
            )
            accounts.append(account)

        multi_mode = bool(autovisor_data.get("multi_mode"))
        if len(accounts) > 1:
            multi_mode = True
        if not multi_mode:
            accounts = accounts[:1]

        validation_error = launcher._validate_autovisor_accounts(accounts)
        if validation_error:
            raise SettingsValidationError(validation_error)

        return {
            "multi_mode": multi_mode,
            "browser_driver": str(
                autovisor_data.get("browser_driver", "Chrome")
                or "Chrome"
            ),
            "browser_path": str(
                autovisor_data.get("browser_path", "") or ""
            ).strip(),
            "accounts": accounts,
        }

    def _merge_yatori(self, yatori_data):
        launcher = self.launcher
        merged = launcher._load_yatori_config_data()
        if not isinstance(merged, dict):
            merged = launcher._default_yatori_config()
        merged.setdefault("setting", {})
        if not isinstance(merged["setting"], dict):
            merged["setting"] = {}

        incoming_setting = yatori_data.get("setting") or {}
        if not isinstance(incoming_setting, dict):
            raise SettingsValidationError("Yatori 全局设置格式无效")
        for section_name in self.YATORI_SETTING_SECTIONS:
            incoming_section = incoming_setting.get(section_name) or {}
            if not isinstance(incoming_section, dict):
                raise SettingsValidationError(
                    f"Yatori 设置项 {section_name} 格式无效"
                )
            existing_section = merged["setting"].get(section_name)
            if not isinstance(existing_section, dict):
                existing_section = {}
                merged["setting"][section_name] = existing_section
            existing_section.update(incoming_section)

        existing_users = (
            merged.get("users")
            if isinstance(merged.get("users"), list)
            else []
        )
        incoming_users = yatori_data.get("users")
        if not incoming_users:
            incoming_users = [launcher._default_yatori_user(1)]
        if not isinstance(incoming_users, list):
            raise SettingsValidationError("Yatori 账号列表格式无效")

        merged_users = []
        for index, incoming_user in enumerate(incoming_users, start=1):
            if not isinstance(incoming_user, dict):
                raise SettingsValidationError(
                    f"Yatori 账号 {index} 的配置格式无效"
                )
            existing = (
                existing_users[index - 1]
                if index <= len(existing_users)
                and isinstance(existing_users[index - 1], dict)
                else {}
            )
            merged_user = dict(existing)
            merged_user.update(
                {
                    key: value
                    for key, value in incoming_user.items()
                    if key != "coursesCustom"
                }
            )
            incoming_courses = incoming_user.get("coursesCustom") or {}
            if not isinstance(incoming_courses, dict):
                raise SettingsValidationError(
                    f"Yatori 账号 {index} 的课程设置格式无效"
                )
            existing_courses = existing.get("coursesCustom")
            if not isinstance(existing_courses, dict):
                existing_courses = {}
            merged_courses = dict(existing_courses)
            merged_courses.update(incoming_courses)
            merged_user["coursesCustom"] = merged_courses
            merged_users.append(merged_user)

        merged["users"] = (
            merged_users or [launcher._default_yatori_user(1)]
        )
        return merged

    def _prepare_payload(self, payload):
        launcher = self.launcher
        payload = payload if isinstance(payload, dict) else {}
        yatori_data = payload.get("yatori")
        autovisor_data = payload.get("autovisor")
        qb_data = payload.get("questionbank")
        if not isinstance(yatori_data, dict):
            yatori_data = launcher._load_yatori_config_data()
        if not isinstance(autovisor_data, dict):
            autovisor_data = launcher._load_autovisor_config_data()

        self._validate_question_bank_payload(qb_data)
        return (
            self._merge_yatori(yatori_data),
            self._normalize_autovisor(autovisor_data),
            qb_data if isinstance(qb_data, dict) else None,
        )

    def save(self, payload):
        launcher = self.launcher
        try:
            yatori_data, autovisor_data, qb_data = (
                self._prepare_payload(payload)
            )
        except SettingsValidationError as exc:
            return self._failure(exc)
        except Exception as exc:
            self._log(f"启动器配置准备失败: {exc}")
            return self._failure(f"配置数据处理失败: {exc}")

        config_paths = [
            launcher._get_yatori_config_path(),
            launcher._get_autovisor_config_path(),
        ]
        if qb_data is not None:
            config_paths.append(launcher.question_bank.config_path)
        try:
            file_snapshot = capture_file_state(config_paths)
        except Exception as exc:
            return self._failure(
                f"无法读取现有配置，已取消保存: {exc}"
            )

        previous_multi_mode = launcher._get_autovisor_multi_mode()
        try:
            launcher._save_yatori_config_data(yatori_data)
            launcher._save_autovisor_config_data(autovisor_data)
            launcher._set_autovisor_multi_mode(
                autovisor_data["multi_mode"]
            )
            # Question-bank persistence can restart its live server. Keep it
            # last so no fallible launcher mutation follows a successful
            # runtime update.
            if qb_data is not None:
                qb_result = launcher.save_qb_settings_from_web(qb_data)
                if not qb_result.get("ok"):
                    raise RuntimeError(
                        qb_result.get(
                            "message",
                            "题库设置保存失败",
                        )
                    )
        except Exception as exc:
            rollback_errors = []
            try:
                launcher._set_autovisor_multi_mode(previous_multi_mode)
            except Exception as rollback_exc:
                rollback_errors.append(
                    f"运行模式恢复失败: {rollback_exc}"
                )
            try:
                restore_file_state(file_snapshot)
            except Exception as rollback_exc:
                rollback_errors.append(
                    f"旧配置恢复失败: {rollback_exc}"
                )

            message = str(exc) or "配置保存失败"
            if rollback_errors:
                message = f"{message}；{'；'.join(rollback_errors)}"
            else:
                message = f"{message}；未保留任何部分改动"
            self._log(f"启动器配置保存失败: {message}")
            return self._failure(message)

        self._log("启动器配置已保存")
        return self._with_state({"ok": True})
