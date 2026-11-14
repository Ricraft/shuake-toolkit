"""Validated partial configuration edits for the assistant's confirmed tools."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from threading import RLock
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from src.preferences_service import DEFAULT_PREFERENCES, normalize_yatori_max_runtime, normalize_batch_targets
from src.web_settings_service import WebSettingsService

INTERNAL_PREFERENCES = {
    "achievements", "achievementResetToken", "tianyiThemeUnlocked",
    "tianyiAchievementShown", "tianyiChatHistory",
}
LAUNCHER_DEFAULTS = {
    **{key: value for key, value in DEFAULT_PREFERENCES.items() if key not in INTERNAL_PREFERENCES},
    "bgType": "none", "bgUrl": "", "glassBlur": 16, "overlayStrength": 70,
}
SCOPES = ("launcher", "yatori", "autovisor", "zhs", "questionbank")


def sensitive(key):
    name = ''.join(c for c in str(key).lower() if c.isalnum())
    return (name in {"password", "passwd", "pwd", "token", "cookie", "cookies", "secret", "authorization"}
            or name.endswith(("apikey", "password", "token", "secret"))) and name not in {"maxtoken", "maxtokens"}


def redact(value):
    if isinstance(value, dict):
        return {key: ("[已隐藏]" if item else "") if sensitive(key) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and '://' in value:
        try:
            url = urlsplit(value)
            host = url.netloc.split('@')[-1]
            query = urlencode([(key, '[已隐藏]' if sensitive(key) else item) for key, item in parse_qsl(url.query, keep_blank_values=True)])
            return urlunsplit((url.scheme, ('[已隐藏]@' if '@' in url.netloc else '') + host, url.path, query, url.fragment))
        except ValueError:
            return '[无效地址]'
    return deepcopy(value)


class AgentSettingsService:
    def __init__(self, launcher):
        self.launcher = launcher
        self.lock = launcher.__dict__.setdefault("_web_settings_transaction_lock", RLock())

    def _load(self, scope, account_index=0):
        launcher = self.launcher
        if scope == "launcher":
            stored = launcher.get_web_preferences()
            return {key: deepcopy(stored.get(key, default)) for key, default in LAUNCHER_DEFAULTS.items()}
        if scope == "yatori":
            return deepcopy(launcher._load_yatori_config_data())
        if scope == "autovisor":
            return deepcopy(launcher._load_autovisor_config_data())
        if scope == "questionbank":
            return deepcopy(launcher.get_qb_settings_from_web())
        if scope == "zhs":
            data = deepcopy(launcher._get_zhs_service().load_settings(account_index + 1))
            data.pop("qr", None)  # Runtime-owned output path, not a user setting.
            return data
        raise ValueError("scope 必须是 launcher/yatori/autovisor/zhs/questionbank")

    def read(self, args):
        scope = args.get("scope")
        data = self._load(scope, args.get('account_index', 0))
        paths = args.get('paths')
        if paths is not None:
            if not isinstance(paths, list) or not 1 <= len(paths) <= 20 or not all(isinstance(p, str) for p in paths):
                raise ValueError('paths 须包含 1 到 20 个字段路径')
            selected = {}
            for path in paths:
                value = data
                for part in path.split('.'):
                    value = value[int(part)] if isinstance(value, list) and part.isdigit() else value[part]
                selected[path] = '[已隐藏]' if any(sensitive(p) for p in path.split('.')) else redact(value)
            data = selected
        return {"ok": True, "scope": scope, "settings": redact(data),
                "message": "密码、Cookie、密钥已隐藏。修改使用现有字段路径，例如 accounts.0.limit_speed；不修改账号数量或内部成就状态。"}

    def patched(self, args):
        scope = args.get("scope")
        data = self._load(scope, args.get("account_index", 0))
        changes = args.get("changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= 20:
            raise ValueError("changes 必须包含 1 到 20 项修改")
        for change in changes:
            if not isinstance(change, dict) or "value" not in change:
                raise ValueError("每项修改需要 path 和 value")
            path = change.get("path")
            if not isinstance(path, str) or not path or len(path) > 200:
                raise ValueError("设置路径格式无效")
            parts = path.split('.')
            if any(not part or part.startswith('_') for part in parts):
                raise ValueError("不支持内部设置路径")
            parent = data
            for part in parts[:-1]:
                if isinstance(parent, list) and part.isdigit() and int(part) < len(parent):
                    parent = parent[int(part)]
                elif isinstance(parent, dict) and part in parent:
                    parent = parent[part]
                else:
                    raise ValueError("设置路径不存在，请先调用 get_settings 查询")
            key = parts[-1]
            if not isinstance(parent, dict) or key not in parent:
                raise ValueError("只支持修改现有设置字段，请先查询字段路径")
            old, value = parent[key], deepcopy(change['value'])
            if sensitive(key) and value == '[已隐藏]':
                raise ValueError("隐藏占位符不能作为密码或密钥保存")
            if isinstance(old, dict) or isinstance(old, list) and any(isinstance(v, dict) for v in old):
                raise ValueError("请按账号索引修改具体字段，不要整体替换配置或账号列表")
            if isinstance(old, bool) and not isinstance(value, bool):
                raise ValueError("开关设置须为 true 或 false")
            if isinstance(old, str) and not isinstance(value, str):
                raise ValueError("文本设置须为字符串")
            if isinstance(old, (int, float)) and not isinstance(old, bool):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("数值设置须为有限数字")
            if isinstance(old, list) and (not isinstance(value, list) or not all(isinstance(v, str) for v in value)):
                raise ValueError("列表设置须为字符串数组")
            if scope == "launcher":
                if key == "batchTargets":
                    value = normalize_batch_targets(value)
                if key == "theme" and value not in ("dark", "light", "tianyi"):
                    raise ValueError("主题须为 dark/light/tianyi")
                if key == "theme" and value == "tianyi" and not self.launcher.get_web_preferences().get('tianyiThemeUnlocked'):
                    raise ValueError("天依主题尚未解锁")
                if key == "yatoriMaxRuntimeMinutes":
                    value = normalize_yatori_max_runtime(value)
                if key in ("glassBlur", "overlayStrength") and not 0 <= value <= (40 if key == "glassBlur" else 100):
                    raise ValueError("背景效果数值超出范围")
                if key == "bgType" and value not in ("none", "custom", "tianyi"):
                    raise ValueError("背景类型无效")
                if key == "bgUrl" and value and not value.startswith(('https://', 'http://', 'data:image/')):
                    raise ValueError("背景来源须为 HTTP(S) 或图片 data URL")
            parent[key] = value
        if scope == 'questionbank':
            WebSettingsService._validate_question_bank_payload(data)
        return data

    @staticmethod
    def summary(args):
        fields = []
        for change in args.get('changes') or []:
            path = change.get('path', '')
            value = '[已隐藏]' if any(sensitive(part) for part in path.split('.')) else redact(change.get('value'))
            fields.append(path + ' = ' + json.dumps(value, ensure_ascii=False)[:160])
        return '修改 ' + str(args.get('scope')) + ' 设置：' + '；'.join(fields)

    def apply(self, args):
        with self.lock:
            # Read again on confirmation, preserving unrelated edits made while
            # the confirmation card was pending. Revalidate against current data.
            data = self.patched(args)
            scope = args['scope']
            if scope == 'launcher':
                patch = {item['path']: data[item['path']] for item in args['changes']}
                result = self.launcher.save_web_preference(patch)
            elif scope == 'zhs':
                result = self.launcher.save_zhs_settings_from_web({'accountIndex': args.get('account_index', 0) + 1, 'settings': data})
            elif scope == 'questionbank':
                result = self.launcher.save_qb_settings_from_web(data)
            else:
                result = self.launcher.save_settings_from_web({scope: data})
            # UI responses contain entire state, including credentials. Never
            # send those snapshots back into the assistant's tool conversation.
            ok = isinstance(result, dict) and result.get('ok') is True
            response = {"ok": ok, "message": "设置已保存" if ok else "设置保存失败，请查看控制台中的配置校验错误", "settingsChanged": scope if ok else None}
            if ok:
                try:
                    effective = self._load(scope, args.get('account_index', 0))
                    normalized = []
                    for change in args['changes']:
                        value = effective
                        for part in change['path'].split('.'):
                            value = value[int(part)] if isinstance(value, list) else value[part]
                        if value != change['value']:
                            normalized.append({'path': change['path'], 'value': '[已隐藏]' if any(sensitive(p) for p in change['path'].split('.')) else redact(value)})
                    if normalized:
                        response['message'] += '；部分值已按核心规则调整：' + json.dumps(normalized, ensure_ascii=False)
                except Exception:
                    response['message'] += '，实际值请在设置页确认'
            return response
