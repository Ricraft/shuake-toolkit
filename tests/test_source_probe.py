"""下载源探测的单元测试：不访问真实网络。"""

import time
import unittest
import urllib.error
from unittest.mock import patch

from src import source_probe


class _Response:
    def __init__(
        self,
        payload=b"x" * 1024,
        *,
        content_type="application/zip",
        status=200,
        headers=None,
    ):
        self.payload = payload
        self.status = status
        self.headers = {
            "Content-Length": str(len(payload)),
            "Content-Type": content_type,
        }
        if headers:
            self.headers.update(headers)
        self.offset = 0

    def read(self, size):
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ProbeSourceTests(unittest.TestCase):
    def test_probe_measures_speed_and_latency(self):
        with patch(
            "src.source_probe.urllib.request.urlopen",
            return_value=_Response(),
        ):
            info = source_probe.probe_source("mirror", "https://mirror.invalid/pip/")

        self.assertTrue(info["reachable"])
        self.assertGreater(info["speed"], 0)
        self.assertIsNotNone(info["latency"])
        self.assertIsNone(info["error"])

    def test_probe_rejects_html_for_release_assets(self):
        with patch(
            "src.source_probe.urllib.request.urlopen",
            return_value=_Response(content_type="text/html; charset=utf-8"),
        ):
            info = source_probe.probe_source(
                "proxy",
                "https://proxy.invalid/core.zip",
                allow_html=False,
            )

        self.assertFalse(info["reachable"])
        self.assertIn("text/html", info["error"])

    def test_probe_checks_expected_size(self):
        with patch(
            "src.source_probe.urllib.request.urlopen",
            return_value=_Response(
                headers={"Content-Range": "bytes 0-1023/4096"},
            ),
        ):
            info = source_probe.probe_source(
                "proxy",
                "https://proxy.invalid/core.zip",
                expected_size=2048,
                allow_html=False,
            )

        self.assertFalse(info["reachable"])
        self.assertIn("文件大小不符", info["error"])

    def test_probe_can_treat_http_error_as_reachable(self):
        error = urllib.error.HTTPError(
            "https://cdn.invalid/", 400, "Bad Request", {}, None
        )
        with patch("src.source_probe.urllib.request.urlopen", side_effect=error):
            strict = source_probe.probe_source("cdn", "https://cdn.invalid/")
            lenient = source_probe.probe_source(
                "cdn", "https://cdn.invalid/", accept_http_error=True
            )

        self.assertFalse(strict["reachable"])
        self.assertTrue(lenient["reachable"])
        self.assertIn("HTTP 400", lenient["error"])


class CollectProbesTests(unittest.TestCase):
    def _record(self, name, url, speed):
        return {
            "name": name,
            "url": url,
            "reachable": True,
            "speed": speed,
            "latency": 0.1,
            "error": None,
        }

    def test_results_are_sorted_fastest_first(self):
        speeds = {"slow": 1.0, "fast": 4.0, "medium": 2.0}

        def fake_probe(name, url, **_kwargs):
            return self._record(name, url, speeds[name])

        results = source_probe.collect_probes(
            [("slow", "s"), ("fast", "f"), ("medium", "m")],
            fake_probe,
        )

        self.assertEqual([item["name"] for item in results], ["fast", "medium", "slow"])

    def test_hung_source_is_cancelled_by_the_deadline(self):
        def fake_probe(name, _url, **_kwargs):
            if name == "hung":
                time.sleep(0.6)
            return self._record(name, _url, 1.0)

        started = time.monotonic()
        results = source_probe.collect_probes(
            [("hab", "h"), ("hung", "x")],
            fake_probe,
            deadline=0.2,
        )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.5)
        hung = [item for item in results if item["name"] == "hung"][0]
        self.assertFalse(hung["reachable"])
        self.assertEqual(hung["error"], "探测超时")

    def test_probe_exception_becomes_a_failure_record(self):
        def fake_probe(_name, _url, **_kwargs):
            raise RuntimeError("boom")

        results = source_probe.collect_probes([("bad", "b")], fake_probe)

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["reachable"])
        self.assertIn("RuntimeError", results[0]["error"])


class CacheTests(unittest.TestCase):
    def tearDown(self):
        source_probe.clear_cache()

    def test_cached_value_is_reused_within_ttl(self):
        calls = []

        def compute():
            calls.append(True)
            return ("value",)

        first = source_probe.cached(("key",), compute)
        second = source_probe.cached(("key",), compute)

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_expired_cache_recomputes(self):
        calls = []

        def compute():
            calls.append(True)
            return len(calls)

        source_probe.cached(("key",), compute, ttl=0.0)
        source_probe.cached(("key",), compute, ttl=0.0)

        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
