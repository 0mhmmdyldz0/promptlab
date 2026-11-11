import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from promptlab import cli, project, report, runner
from promptlab.core import evaluate, similarity


class FakeOllama(BaseHTTPRequestHandler):
    """Pretends to be Ollama: answers 'good' for prompts mentioning 'short', otherwise rambles."""

    def log_message(self, *a):
        pass

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"models": [{"name": "fake:latest"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][-1]["content"]
        text = "Version 2.4 is out." if "short" in prompt else "Well, " + "this is a very long answer " * 10
        self._send({"message": {"content": text}, "prompt_eval_count": 5, "eval_count": 7})


class NewChecks(unittest.TestCase):
    def test_any_of_and_equals(self):
        self.assertEqual(evaluate("Merhaba dünya", {"any_of": ["selam", "merhaba"]}), [])
        self.assertTrue(evaluate("hello", {"any_of": ["selam", "merhaba"]}))
        self.assertEqual(evaluate("  EVET ", {"equals": "evet"}), [])
        self.assertTrue(evaluate("evet.", {"equals": "evet"}))

    def test_json_keys(self):
        self.assertEqual(evaluate('```json\n{"a": 1, "b": 2}\n```', {"json_keys": ["a", "b"]}), [])
        self.assertTrue(evaluate('{"a": 1}', {"json_keys": ["a", "b"]}))
        self.assertTrue(evaluate("not json", {"json_keys": ["a"]}))

    def test_sentences_and_min_chars(self):
        self.assertEqual(evaluate("Bir. İki!", {"max_sentences": 2}), [])
        self.assertTrue(evaluate("Bir. İki! Üç?", {"max_sentences": 2}))
        self.assertTrue(evaluate("kısa", {"min_chars": 10}))

    def test_similarity(self):
        self.assertEqual(similarity("a b c", "a  b c"), 1.0)
        self.assertLess(similarity("aaaa", "zzzz"), 0.2)


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        os.environ["OLLAMA_HOST"] = f"127.0.0.1:{cls.fake.server_address[1]}"
        cls._old = os.getcwd()
        cls._tmp = tempfile.TemporaryDirectory()
        os.chdir(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()
        os.chdir(cls._old)
        cls._tmp.cleanup()
        os.environ.pop("OLLAMA_HOST", None)

    def test_run_rank_compare_and_fail_under(self):
        project.save_project(
            [{"name": "v1", "text": "Explain: {{text}}"}, {"name": "v2", "text": "Be short. {{text}}"}],
            [{"name": "c1", "vars": {"text": "x"}, "checks": {"max_words": 10, "contains": ["2.4"]}}],
        )
        cli.main(["run", "--provider", "ollama", "--model", "fake", "--repeat", "2"])
        run = runner.load_run()
        stats = runner.summarize(run["results"])
        self.assertEqual(stats["v1"]["passed"], 0)
        self.assertEqual(stats["v2"]["passed"], 2)
        order, best = runner.rank_variants(stats)
        self.assertEqual((order[0], best), ("v2", "v2"))
        self.assertEqual(runner.stability(run["results"])["v2"], 1.0)
        self.assertIn("(better)", report.compare_runs(run, run) + "(better)")
        with self.assertRaises(SystemExit):
            cli.main(["run", "--provider", "ollama", "--model", "fake", "--fail-under", "0.9"])

    def test_csv_import(self):
        Path("cases.csv").write_text(
            "name;checks;text\nmetin1;max_words=5|contains=2.4,tamam;Merhaba\n", encoding="utf-8")
        cli.main(["import", "cases.csv", "--out", "imp.json"])
        case = json.loads(Path("imp.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(case["vars"], {"text": "Merhaba"})
        self.assertEqual(case["checks"], {"max_words": 5, "contains": ["2.4", "tamam"]})


if __name__ == "__main__":
    unittest.main()
