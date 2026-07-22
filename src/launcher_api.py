"""Thin pywebview API adapter for :class:`UnifiedLauncher`.

Keeping the bridge free of UI and process-management logic makes the public
surface easy to inspect and test without coupling JavaScript to the large
launcher implementation.
"""


class WebLauncherAPI:
    """Expose the deliberately small API that JavaScript is allowed to call."""

    def __init__(self, launcher):
        self._launcher = launcher

    def get_initial_state(self):
        return self._launcher.get_web_initial_state()

    def get_runtime_state(self):
        return self._launcher.get_web_runtime_state()

    def get_preferences(self):
        return self._launcher.get_web_preferences()

    def save_settings(self, payload):
        return self._launcher.save_settings_from_web(payload)

    def save_preference(self, payload):
        return self._launcher.save_web_preference(payload)

    def perform_action(self, action, script_type=None):
        return self._launcher.perform_web_action(action, script_type)

    def toggle_yatori(self):
        return self._launcher.toggle_web_runtime("yatori")

    def toggle_autovisor(self):
        return self._launcher.toggle_web_runtime("autovisor")

    def detect_browser_path(self, browser_name="Chrome"):
        return self._launcher.detect_browser_path_for_web(browser_name)

    def browse_browser_path(self):
        return self._launcher.browse_browser_path_for_web()

    def test_ai_connectivity(self, config):
        return self._launcher.test_ai_connectivity_from_web(config)

    def fetch_model_list(self, config):
        return self._launcher.fetch_model_list_from_web(config)

    def get_qb_settings(self):
        return self._launcher.get_qb_settings_from_web()

    def save_qb_settings(self, payload):
        return self._launcher.save_qb_settings_from_web(payload)

    def get_autovisor_courses(self, account_index=0):
        return self._launcher.get_autovisor_courses_from_web(
            account_index,
            force_refresh=True,
        )

    def get_xuexitong_courses(self, account_index=0):
        return self._launcher.get_xuexitong_courses_from_web(
            account_index,
            force_refresh=True,
        )

    def cancel_shutdown(self):
        return self._launcher._cancel_shutdown()

    def export_logs(self, tab, text):
        return self._launcher.export_logs_to_file(tab, text)

    def start_practice_mode(self, account_index=0):
        return self._launcher.start_practice_mode_from_web(account_index)

    def stop_practice_mode(self):
        return self._launcher.stop_practice_mode_from_web()
