import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from promptlab import server


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old = os.getcwd()
        cls._tmp = tempfile.TemporaryDirectory()
        os.chdir(cls._tmp.name)
        cls.srv = server.make_server(0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        os.chdir(cls._old)
        cls._tmp.cleanup()

    def call(self, path, body=None, method=None, headers=None):
        req = urllib.request.Request(
            self.base + path, data=None if body is None else json.dumps(body).encode(),
            method=method or ("POST" if body is not None else "GET"),
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_full_flow(self):
        code, page = self.call("/")
        self.assertEqual(code, 200)
        self.assertIn("promptlab", page)
        self.assertEqual(self.call("/api/init", {"lang": "tr"})[0], 200)
        proj = json.loads(self.call("/api/project")[1])
        self.assertEqual(len(proj["prompts"]), 2)

        code, text = self.call("/api/run", {"prompts": ["v1", "v2"], "models": ["m"], "provider": "ollama", "dry_run": True})
        self.assertEqual(code, 200)
        events = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(events[0]["type"], "start")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(sum(e["type"] == "result" for e in events), 4)
        run_id = events[0]["id"]

        self.assertEqual(self.call(f"/api/runs/{run_id}/vote", {"input": events[0]["inputs"][0], "variant": "v1"})[0], 200)
        run = json.loads(self.call(f"/api/runs/{run_id}")[1])
        self.assertEqual(list(run["votes"].values()), ["v1"])
        self.assertEqual(self.call(f"/api/runs/{run_id}/vote", {"input": "nope", "variant": "v1"})[0], 400)
        self.assertIn("variant", self.call(f"/api/runs/{run_id}/export?fmt=csv")[1])
        self.assertEqual(len(json.loads(self.call("/api/runs")[1])), 1)

    def test_rejects_foreign_origin_and_host(self):
        self.assertEqual(self.call("/api/project", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("/api/project", headers={"Host": "evil.example"})[0], 403)

    def test_rejects_path_tricks(self):
        self.assertEqual(self.call("/api/project", {"prompts": [{"name": "../x", "text": "y"}], "inputs": []}, method="PUT")[0], 400)
        self.assertEqual(self.call("/api/runs/..%2f..%2fsecret")[0], 404)


if __name__ == "__main__":
    unittest.main()
