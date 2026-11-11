import os
import unittest
from unittest import mock

from promptlab.core import evaluate, placeholders, render
from promptlab.providers import default_model, pick_provider


class CoreTests(unittest.TestCase):
    def test_render_fills_known_and_keeps_unknown(self):
        self.assertEqual(render("Hi {{name}} {{ other }}", {"name": "Ada"}), "Hi Ada {{ other }}")

    def test_placeholders_unique_in_order(self):
        self.assertEqual(placeholders("{{b}} {{a}} {{b}}"), ["b", "a"])

    def test_basic_checks(self):
        self.assertEqual(evaluate("Version 2.4 is out", {"max_words": 5, "contains": ["2.4"]}), [])
        self.assertEqual(len(evaluate("one two three", {"max_words": 2, "contains": ["x"], "not_contains": ["TWO"]})), 3)
        self.assertEqual(len(evaluate("hi", {"min_words": 3})), 1)

    def test_contains_accepts_plain_string(self):
        self.assertEqual(evaluate("hello world", {"contains": "WORLD"}), [])

    def test_extra_checks(self):
        self.assertEqual(evaluate("Hello there", {"starts_with": "hello", "regex": r"th\w+e", "max_chars": 20}), [])
        self.assertEqual(len(evaluate("Hello", {"starts_with": "bye", "regex": r"\d+", "max_chars": 2})), 3)
        self.assertEqual(len(evaluate("x", {"regex": "("})), 1)  # invalid regex is reported, not raised

    def test_is_json(self):
        self.assertEqual(evaluate('```json\n{"a": 1}\n```', {"is_json": True}), [])
        self.assertEqual(len(evaluate("not json", {"is_json": True})), 1)


class ProviderTests(unittest.TestCase):
    def test_auto_uses_ollama_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pick_provider("auto"), "ollama")

    def test_auto_uses_anthropic_with_key(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}, clear=True):
            self.assertEqual(pick_provider("auto"), "anthropic")

    def test_env_model_override(self):
        with mock.patch.dict(os.environ, {"PROMPTLAB_MODEL": "llama3.2:3b"}, clear=True):
            self.assertEqual(default_model("ollama"), "llama3.2:3b")


if __name__ == "__main__":
    unittest.main()
