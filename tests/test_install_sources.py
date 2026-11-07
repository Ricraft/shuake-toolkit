"""PyPI 镜像与 Playwright 下载源排序的单元测试：不访问真实网络。"""

import unittest
from types import SimpleNamespace

from src import install_sources, source_probe


def _record(name, url, speed, reachable=True, error=None):
    return {
        "name": name,
        "url": url,
        "reachable": reachable,
        "speed": speed,
        "latency": 0.1,
        "error": error,
    }


class PypiMirrorTests(unittest.TestCase):
    def tearDown(self):
        source_probe.clear_cache()

    def test_mirrors_are_ranked_by_measured_speed(self):
        speeds = {
            "清华": 0.5 * 1024 * 1024,
            "阿里": 6.0 * 1024 * 1024,
            "华为": 1.0 * 1024 * 1024,
            "官方": 0.2 * 1024 * 1024,
        }
        logs = []

        def fake_probe(name, url, **_kwargs):
            return _record(name, url, speeds[name])

        ranked = install_sources.rank_pypi_mirrors(
            logger=logs.append,
            probe_fn=fake_probe,
            use_cache=False,
        )

        self.assertEqual([name for name, _url in ranked], ["阿里", "华为", "清华", "官方"])
        self.assertTrue(any("已选择 阿里 镜像安装依赖" in line for line in logs))
        self.assertTrue(any("PyPI 镜像检测结果" in line for line in logs))

    def test_all_failed_probes_keep_the_configured_order(self):
        logs = []

        def fake_probe(name, url, **_kwargs):
            return _record(name, url, 0.0, reachable=False, error="timeout")

        ranked = install_sources.rank_pypi_mirrors(
            logger=logs.append,
            probe_fn=fake_probe,
            use_cache=False,
        )

        self.assertEqual([name for name, _url in ranked], ["清华", "阿里", "华为", "官方"])
        self.assertTrue(any("均失败" in line for line in logs))

    def test_probe_url_points_at_the_pip_index_page(self):
        self.assertEqual(
            install_sources.pypi_probe_url("https://pypi.org/simple/"),
            "https://pypi.org/simple/pip/",
        )

    def test_index_urls_follow_the_ranking(self):
        def fake_probe(name, url, **_kwargs):
            return _record(name, url, 2.0 if name == "官方" else 1.0)

        urls = install_sources.rank_pypi_index_urls(
            probe_fn=fake_probe,
            use_cache=False,
        )

        self.assertEqual(urls[0], "https://pypi.org/simple")
        self.assertEqual(len(urls), 4)

    def test_single_mirror_skips_probing(self):
        calls = []

        def fake_probe(name, url, **_kwargs):
            calls.append(name)
            return _record(name, url, 1.0)

        ranked = install_sources.rank_pypi_mirrors(
            (("only", "https://only.invalid/simple"),),
            probe_fn=fake_probe,
            use_cache=False,
        )

        self.assertEqual(ranked, (("only", "https://only.invalid/simple"),))
        self.assertEqual(calls, [])


class PlaywrightSourceTests(unittest.TestCase):
    def tearDown(self):
        source_probe.clear_cache()

    def test_relative_path_strips_the_longest_known_prefix(self):
        url = (
            "https://cdn.playwright.dev/dbazure/download/playwright/"
            "builds/ffmpeg/1011/ffmpeg-win64.zip"
        )

        self.assertEqual(
            install_sources.playwright_relative_path(url),
            "/builds/ffmpeg/1011/ffmpeg-win64.zip",
        )
        self.assertIsNone(
            install_sources.playwright_relative_path("https://example.invalid/x.zip")
        )

    def test_mirror_url_maps_official_artifact_onto_host(self):
        official = (
            "https://cdn.playwright.dev/builds/cft/153.0.8010.12/win64/chrome-win64.zip"
        )

        self.assertEqual(
            install_sources.playwright_mirror_url(
                official, "https://npmmirror.com/mirrors/playwright"
            ),
            "https://npmmirror.com/mirrors/playwright/builds/cft/153.0.8010.12/"
            "win64/chrome-win64.zip",
        )
        self.assertEqual(
            install_sources.playwright_mirror_url(official, None), official
        )

    def test_parse_dry_run_urls_ignores_other_lines(self):
        output = (
            "Install location: C:\\cache\n"
            "Download url: https://cdn.playwright.dev/builds/cft/1/a.zip\n"
            "Download url: not-a-url\n"
            "Download url: https://cdn.playwright.dev/builds/ffmpeg/2/b.zip\n"
        )

        self.assertEqual(
            install_sources.parse_dry_run_urls(output),
            [
                "https://cdn.playwright.dev/builds/cft/1/a.zip",
                "https://cdn.playwright.dev/builds/ffmpeg/2/b.zip",
            ],
        )

    def test_detect_urls_tolerates_missing_playwright(self):
        def fake_run(*_args, **_kwargs):
            raise OSError("playwright not installed")

        self.assertEqual(
            install_sources.detect_playwright_urls("python", run=fake_run), []
        )

    def test_detect_urls_reads_dry_run_stdout(self):
        def fake_run(command, **_kwargs):
            self.assertIn("--dry-run", command)
            return SimpleNamespace(
                returncode=0,
                stdout="Download url: https://cdn.playwright.dev/builds/cft/1/a.zip\n",
            )

        self.assertEqual(
            install_sources.detect_playwright_urls("python", run=fake_run),
            ["https://cdn.playwright.dev/builds/cft/1/a.zip"],
        )

    def test_hosts_are_ranked_by_artifact_speed(self):
        official = "https://cdn.playwright.dev/builds/cft/1/win64/chrome-win64.zip"
        speeds = {"npmmirror": 8.0, "官方": 0.3}
        logs = []

        def fake_probe(name, url, **_kwargs):
            return _record(name, url, speeds[name] * 1024 * 1024)

        ranked = install_sources.rank_playwright_hosts(
            artifact_urls=[official],
            logger=logs.append,
            probe_fn=fake_probe,
            use_cache=False,
        )

        self.assertEqual(ranked[0][0], "npmmirror")
        self.assertEqual(ranked[0][1], "https://npmmirror.com/mirrors/playwright")
        self.assertEqual(ranked[1], ("官方", None))
        self.assertTrue(any("已选择 npmmirror 源下载 Playwright" in line for line in logs))

    def test_all_failed_hosts_keep_the_configured_order(self):
        official = "https://cdn.playwright.dev/builds/cft/1/win64/chrome-win64.zip"

        def fake_probe(name, url, **_kwargs):
            return _record(name, url, 0.0, reachable=False, error="timeout")

        ranked = install_sources.rank_playwright_hosts(
            artifact_urls=[official],
            probe_fn=fake_probe,
            use_cache=False,
        )

        self.assertEqual([name for name, _url in ranked], ["npmmirror", "官方"])


if __name__ == "__main__":
    unittest.main()
