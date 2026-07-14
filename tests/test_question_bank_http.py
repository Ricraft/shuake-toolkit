import threading
import unittest
import urllib.error
import urllib.request

from src.题库服务器 import QuestionBankHandler, ThreadedHTTPServer


class QuestionBankHttpPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadedHTTPServer(("127.0.0.1", 0), QuestionBankHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/api/status"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_native_client_without_origin_is_allowed(self):
        with urllib.request.urlopen(self.url, timeout=3) as response:
            self.assertEqual(response.status, 200)

    def test_untrusted_browser_origin_is_rejected(self):
        request = urllib.request.Request(self.url, headers={"Origin": "https://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 403)

    def test_local_browser_origin_receives_exact_cors_header(self):
        origin = "http://localhost:9999"
        request = urllib.request.Request(self.url, headers={"Origin": origin})
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)


if __name__ == "__main__":
    unittest.main()
