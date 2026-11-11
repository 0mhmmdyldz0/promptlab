import os
import tempfile
import unittest
from pathlib import Path

from promptlab import project, report, runner


class InFolder(unittest.TestCase):
    def setUp(self):
        self._old = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old)
        self._tmp.cleanup()


class ProjectTests(InFolder):
    def test_examples_and_roundtrip(self):
        self.assertEqual(len(project.init_examples("tr")), 3)
        self.assertEqual(project.init_examples("en"), [])  # never overwrites
        proj = project.load_project()
        self.assertEqual([p["name"] for p in proj["prompts"]], ["v1", "v2"])
        self.assertEqual(len(proj["inputs"]), 2)

    def test_save_renames_and_removes_files(self):
        project.init_examples("en")
        proj = project.load_project()
        proj["prompts"][0]["name"] = "V1"  # case-only rename must not delete the file
        project.save_project(proj["prompts"], proj["inputs"])
        names = sorted(p.stem.lower() for p in Path("prompts").glob("*.txt"))
        self.assertEqual(names, ["v1", "v2"])
        project.save_project(proj["prompts"][:1], proj["inputs"])
        self.assertEqual(len(list(Path("prompts").glob("*.txt"))), 1)

    def test_rejects_bad_names(self):
        for bad in ["../evil", "a/b", "", ".hidden", "x" * 80, "dup"]:
            prompts = [{"name": bad, "text": "x"}]
            if bad == "dup":
                prompts = [{"name": "dup", "text": "x"}, {"name": "DUP", "text": "y"}]
            with self.assertRaises(project.ProjectError):
                project.save_project(prompts, [])

    def test_broken_inputs_file_is_backed_up(self):
        Path("inputs.json").write_text("{not json", encoding="utf-8")
        self.assertIn("not valid", project.load_project()["warning"])
        project.save_project([], [])
        self.assertTrue(Path("inputs.json.bak").exists())


class RunnerTests(InFolder):
    def run_dry(self, **kw):
        events = list(runner.execute({"v1": "Say {{x}}", "v2": "Please say {{x}}"},
                                     [{"name": "a", "vars": {"x": "hi"}}, {"name": "a", "vars": {"x": "yo"}}],
                                     kw.pop("models", ["m1"]), "ollama", dry_run=True, **kw))
        return events[0], [e["result"] for e in events[1:]]

    def test_plan_and_results(self):
        start, results = self.run_dry(repeat=2)
        self.assertEqual(start["total"], 8)
        self.assertEqual(len(results), 8)
        self.assertEqual(start["inputs"], ["a", "a (2)"])  # duplicate names are made unique

    def test_multiple_models_label_variants(self):
        start, _ = self.run_dry(models=["m1", "m2"])
        self.assertEqual([v["label"] for v in start["variants"]], ["v1 · m1", "v2 · m1", "v1 · m2", "v2 · m2"])

    def test_save_load_summarize_export(self):
        start, results = self.run_dry()
        run = runner.build_run(start, results, {"models": ["m1"]})
        runner.write_run(run)
        loaded = runner.load_run(run["id"])
        loaded["votes"] = {"a": "v1"}
        stats = runner.summarize(loaded["results"], loaded["votes"])
        self.assertEqual(stats["v1"]["wins"], 1)
        self.assertEqual(len(runner.list_runs()), 1)
        csv_text = report.to_csv(loaded)
        self.assertIn("variant", csv_text.splitlines()[0])
        self.assertIn("<table>", report.render_html(loaded))

    def test_old_run_format_still_loads(self):
        old = {"id": "20260101-000000", "results": [{
            "input": "a", "prompt": "v1", "output": "hello", "seconds": 1.0, "error": None,
            "input_tokens": 1, "output_tokens": 1, "model": "m"}]}
        runner.write_run(old)
        run = runner.load_run("20260101-000000")
        self.assertEqual(run["results"][0]["variant"], "v1")
        self.assertEqual(run["results"][0]["words"], 1)


if __name__ == "__main__":
    unittest.main()
