import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from helpers import DemoHome

from mnemo import dashboard
from mnemo.index import Index

TOKEN = "test-token"


class DashboardServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.demo = DemoHome()
        cls.demo.activate()  # searches sync the "local" index from HOME; keep it hermetic
        idx = Index(cls.demo.db)
        idx.sync(home=cls.demo.home)
        idx.close()

        cls.dist = tempfile.mkdtemp(prefix="mnemo-dist-")
        os.makedirs(os.path.join(cls.dist, "search"))
        os.makedirs(os.path.join(cls.dist, "_next", "static", "chunks"))
        page = b'<meta name="mnemo-token" content="__MNEMO_TOKEN__"><p>ok</p>'
        for rel in ("index.html", os.path.join("search", "index.html")):
            with open(os.path.join(cls.dist, rel), "wb") as f:
                f.write(page)
        with open(os.path.join(cls.dist, "404.html"), "wb") as f:
            f.write(b"not found page")
        with open(os.path.join(cls.dist, "_next", "static", "chunks", "app.js"), "wb") as f:
            f.write(b"console.log(1)")

        cls._saved = (dashboard.WEB_DIST, dashboard.DEFAULT_DB_PATH, dashboard.Handler.token)
        dashboard.WEB_DIST = cls.dist
        dashboard.DEFAULT_DB_PATH = cls.demo.db
        dashboard.Handler.token = TOKEN
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        dashboard.WEB_DIST, dashboard.DEFAULT_DB_PATH, dashboard.Handler.token = cls._saved
        shutil.rmtree(cls.dist, ignore_errors=True)
        cls.demo.deactivate()
        cls.demo.cleanup()

    def request(self, method, path, body=None, token=None, host=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or "127.0.0.1:%d" % self.port}
        if token:
            headers["X-Dashboard-Token"] = token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        return res, payload

    # ------------------------------------------------------------ static UI

    def test_html_gets_the_token_injected(self):
        res, body = self.request("GET", "/")
        self.assertEqual(res.status, 200)
        self.assertIn(b'content="test-token"', body)
        self.assertNotIn(b"__MNEMO_TOKEN__", body)
        self.assertEqual(res.getheader("Cache-Control"), "no-store")

    def test_trailing_slash_and_bare_routes(self):
        for path in ("/search/", "/search", "/search/?q=x"):
            res, body = self.request("GET", path)
            self.assertEqual(res.status, 200, path)
            self.assertIn(b"test-token", body)

    def test_unknown_route_serves_404_page(self):
        res, body = self.request("GET", "/nope/")
        self.assertEqual(res.status, 404)
        self.assertEqual(body, b"not found page")

    def test_path_traversal_is_blocked(self):
        for path in ("/../../etc/passwd", "/_next/../../../dashboard.py", "/%2e%2e/%2e%2e/etc/passwd"):
            res, body = self.request("GET", path)
            self.assertEqual(res.status, 404, path)
            self.assertNotIn(b"root:", body)

    def test_hashed_assets_are_immutable(self):
        res, _ = self.request("GET", "/_next/static/chunks/app.js")
        self.assertEqual(res.status, 200)
        self.assertIn("javascript", res.getheader("Content-Type"))
        self.assertIn("immutable", res.getheader("Cache-Control"))

    def test_head_matches_get_without_body(self):
        res, body = self.request("HEAD", "/search/")
        self.assertEqual(res.status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(res.getheader("Content-Length")), 0)
        res, _ = self.request("HEAD", "/api/status")
        self.assertIn(res.status, (403, 405))

    def test_missing_build_explains_itself(self):
        saved = dashboard.WEB_DIST
        dashboard.WEB_DIST = os.path.join(self.dist, "does-not-exist")
        try:
            res, body = self.request("GET", "/")
        finally:
            dashboard.WEB_DIST = saved
        self.assertEqual(res.status, 503)
        self.assertIn(b"pnpm build", body)

    # ------------------------------------------------------------------ API

    def test_api_requires_the_token(self):
        res, _ = self.request("GET", "/api/status")
        self.assertEqual(res.status, 403)
        res, _ = self.request("GET", "/api/status", token="wrong")
        self.assertEqual(res.status, 403)

    def test_foreign_host_header_is_rejected(self):
        res, _ = self.request("GET", "/", host="evil.example")
        self.assertEqual(res.status, 403)
        res, _ = self.request("GET", "/api/status", token=TOKEN, host="evil.example")
        self.assertEqual(res.status, 403)

    def test_status_and_search(self):
        res, body = self.request("GET", "/api/status", token=TOKEN)
        self.assertEqual(res.status, 200)
        status = json.loads(body)
        self.assertEqual(sum(v["files"] for v in status["sources"].values()), 5)

        res, body = self.request("POST", "/api/search", {"query": "backoff", "limit": 20}, token=TOKEN)
        self.assertEqual(res.status, 200)
        result = json.loads(body)
        self.assertEqual(result["per_host"][0]["host"], "local")
        self.assertEqual(len(result["merged"]), 17)

    def test_session_roundtrip(self):
        _, body = self.request("POST", "/api/search", {"query": "budget", "limit": 1}, token=TOKEN)
        hit = json.loads(body)["merged"][0]
        res, body = self.request("POST", "/api/session", {"path": hit["path"], "host": "local"}, token=TOKEN)
        self.assertEqual(res.status, 200)
        session = json.loads(body)["session"]
        self.assertEqual(session["count"], len(session["messages"]))
        self.assertIn(hit["lineno"], [m["lineno"] for m in session["messages"]])

    def test_node_settings(self):
        _, body = self.request("GET", "/api/node", token=TOKEN)
        node = json.loads(body)
        self.assertFalse(node["forward"])
        res, body = self.request("POST", "/api/node", {"name": " laptop ", "forward": True}, token=TOKEN)
        self.assertEqual(res.status, 200)
        self.assertEqual(json.loads(body)["name"], "laptop")
        _, body = self.request("GET", "/api/node", token=TOKEN)
        self.assertEqual(json.loads(body), dict(node, name="laptop", forward=True))
        res, _ = self.request("POST", "/api/node", {"name": "  "}, token=TOKEN)
        self.assertEqual(res.status, 400)
        self.request("POST", "/api/node", {"forward": False}, token=TOKEN)
        res, body = self.request("POST", "/api/topology", {}, token=TOKEN)
        topo = json.loads(body)["topology"]
        self.assertEqual((topo["id"], topo["neighbors"]), (node["id"], []))
        res, _ = self.request("POST", "/api/node/remote", {"name": "nope", "forward": True}, token=TOKEN)
        self.assertEqual(res.status, 400)
        self.assertEqual(topo["code"], node["code"])
        _, body = self.request("POST", "/api/upgrade-devices", {}, token=TOKEN)
        self.assertEqual(json.loads(body), {"ok": True, "results": [], "warnings": []})
        res, _ = self.request("POST", "/api/upgrade-devices", {"routes": [""]}, token=TOKEN)
        self.assertEqual(res.status, 400)

    def test_bad_requests_are_400(self):
        res, _ = self.request("POST", "/api/search", {"query": "  "}, token=TOKEN)
        self.assertEqual(res.status, 400)
        res, _ = self.request("POST", "/api/session", {"path": ""}, token=TOKEN)
        self.assertEqual(res.status, 400)


if __name__ == "__main__":
    unittest.main()
