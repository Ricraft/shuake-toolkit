# encoding=utf-8
"""成就「吃水不忘挖井人」的回归契约。

触发条件：首次通过启动器打开开发者主页——Yatori 文档站 / Autovisor 仓库 /
ZError 站点，以及更新弹窗里的 Autovisor GitHub 链接；蓝奏云等其他链接不计入。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_JS = PROJECT_ROOT / "web" / "app.js"
FRONTEND = APP_JS.read_text(encoding="utf-8")
MARKER = "const DEVELOPER_LINK_ACHIEVEMENT_KEYS"

HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const marker = "const DEVELOPER_LINK_ACHIEVEMENT_KEYS";
const start = src.indexOf(marker);
if (start < 0) { console.error('marker not found'); process.exit(2); }
const code = src.slice(start);
const keys = ['yatori', 'autovisor', 'zerror', 'autovisor_github', 'autovisor_lanzou', '', 'evil', null, undefined];
const out = {};
for (const key of keys) {
  const calls = [];
  const note = new Function('unlockAchievement', code + '\nreturn noteDeveloperLinkVisited;')((id) => { calls.push(id); return true; });
  out[String(key)] = { returned: note(key), unlocked: calls.slice() };
}
const calls = [];
const note = new Function('unlockAchievement', code + '\nreturn noteDeveloperLinkVisited;')((id) => { calls.push(id); return false; });
out['duplicate_call_returns_false'] = { returned: note('yatori'), unlocked: calls.slice() };
console.log(JSON.stringify(out));
"""


class AchievementContractTests(unittest.TestCase):
    def test_achievement_definition_exists_with_required_fields(self):
        block = FRONTEND.split("const ACHIEVEMENTS = {", 1)[1].split("\n        };", 1)[0]
        self.assertIn("dev_link_visit:", block)
        entry = block.split("dev_link_visit:", 1)[1].split("},", 1)[0]
        self.assertIn("吃水不忘挖井人", entry)
        self.assertIn("icon:", entry)
        self.assertIn("color:", entry)
        self.assertIn("desc:", entry)
        self.assertIn("开发者主页", entry)

    def test_trigger_key_set_matches_the_developer_links(self):
        block = FRONTEND.split(MARKER, 1)[1].split("];", 1)[0]
        for key in ("'yatori'", "'autovisor'", "'zerror'", "'autovisor_github'"):
            self.assertIn(key, block)
        # 蓝奏云是国内网盘，不属于开发者 GitHub 主页
        self.assertNotIn("autovisor_lanzou", block)

    def test_gate_unlocks_only_through_unlockAchievement(self):
        gate = FRONTEND.split("function noteDeveloperLinkVisited", 1)[1].split("\n        }", 1)[0]
        self.assertIn("DEVELOPER_LINK_ACHIEVEMENT_KEYS.includes(normalized)", gate)
        self.assertIn("unlockAchievement('dev_link_visit')", gate)

    def test_both_entry_points_report_the_visit(self):
        contributor = FRONTEND.split("function openContributorLink", 1)[1].split(
            "\n        function ", 1
        )[0]
        self.assertIn("noteDeveloperLinkVisited(key)", contributor)

        update_flow = FRONTEND.split("async function openAutovisorDownload", 1)[1].split(
            "\n        function ", 1
        )[0]
        self.assertIn("noteDeveloperLinkVisited(key)", update_flow)
        # 必须在校验动作成功之后才计入
        self.assertLess(
            update_flow.index("handleWebActionResult"),
            update_flow.index("noteDeveloperLinkVisited"),
        )


class AchievementBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node is required for the achievement behaviour contract")
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="dsh-achievement-"))
        harness = cls.tmpdir / "achievement_harness.js"
        harness.write_text(HARNESS, encoding="utf-8")
        completed = subprocess.run(
            [cls.node, str(harness), str(APP_JS)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        cls.result = json.loads(completed.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_developer_links_unlock_the_achievement(self):
        for key in ("yatori", "autovisor", "zerror", "autovisor_github"):
            with self.subTest(key=key):
                entry = self.result[key]
                self.assertTrue(entry["returned"], entry)
                self.assertEqual(entry["unlocked"], ["dev_link_visit"], entry)

    def test_other_links_do_not_unlock_it(self):
        for key in ("autovisor_lanzou", "", "evil", "null", "undefined"):
            with self.subTest(key=key):
                entry = self.result[key]
                self.assertFalse(entry["returned"], entry)
                self.assertEqual(entry["unlocked"], [], entry)

    def test_already_unlocked_keeps_returning_false(self):
        entry = self.result["duplicate_call_returns_false"]
        self.assertFalse(entry["returned"], entry)
        self.assertEqual(entry["unlocked"], ["dev_link_visit"], entry)


if __name__ == "__main__":
    unittest.main()
