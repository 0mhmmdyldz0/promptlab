"""The project on disk: prompts/*.txt and inputs.json in the current folder."""
import json
import re
import shutil
from pathlib import Path

PROMPTS_DIR = Path("prompts")
INPUTS_FILE = Path("inputs.json")
NAME_RE = re.compile(r"[\w\-. ]{1,60}")

EXAMPLES = {
    "en": {
        "v1": "Summarize the following text in one sentence:\n\n{{text}}\n",
        "v2": (
            "You are a precise editor. Summarize the text below in one sentence "
            "of at most 20 words. Keep names and numbers. Output only the summary.\n\n"
            "Text:\n{{text}}\n"
        ),
        "inputs": [
            {
                "name": "release-note",
                "vars": {"text": "Version 2.4 adds dark mode, fixes a crash on startup, and drops support for Python 3.8."},
                "checks": {"max_words": 20, "contains": ["2.4"]},
            },
            {
                "name": "meeting",
                "vars": {"text": "The team agreed to move the launch from May 3 to May 17 because QA found two blocking bugs in checkout."},
                "checks": {"max_words": 20, "contains": ["May 17"]},
            },
        ],
    },
    "tr": {
        "v1": "Aşağıdaki metni tek cümleyle özetle:\n\n{{text}}\n",
        "v2": (
            "Sen titiz bir editörsün. Aşağıdaki metni en fazla 20 kelimelik tek bir "
            "cümleyle özetle. İsimleri ve sayıları koru. Sadece özeti yaz.\n\n"
            "Metin:\n{{text}}\n"
        ),
        "inputs": [
            {
                "name": "surum-notu",
                "vars": {"text": "Sürüm 2.4 karanlık mod ekliyor, açılıştaki çökmeyi düzeltiyor ve Python 3.8 desteğini kaldırıyor."},
                "checks": {"max_words": 20, "contains": ["2.4"]},
            },
            {
                "name": "toplanti",
                "vars": {"text": "Ekip, QA ödeme sayfasında iki engelleyici hata bulduğu için lansmanı 3 Mayıs'tan 17 Mayıs'a ertelemeye karar verdi."},
                "checks": {"max_words": 20, "contains": ["17"]},
            },
        ],
    },
}


class ProjectError(ValueError):
    pass


def valid_name(name):
    return (
        isinstance(name, str)
        and NAME_RE.fullmatch(name) is not None
        and name == name.strip()
        and not name.startswith(".")
        and not name.endswith(".")
    )


def load_project():
    prompts, warning = [], None
    if PROMPTS_DIR.is_dir():
        for p in sorted(PROMPTS_DIR.glob("*.txt")):
            if valid_name(p.stem):
                prompts.append({"name": p.stem, "text": p.read_text(encoding="utf-8")})
    inputs = []
    if INPUTS_FILE.exists():
        try:
            inputs = json.loads(INPUTS_FILE.read_text(encoding="utf-8"))
            if not isinstance(inputs, list):
                raise ValueError
        except ValueError:
            inputs, warning = [], "inputs.json is not valid; it will be backed up to inputs.json.bak when you save."
    return {"prompts": prompts, "inputs": inputs, "warning": warning}


def _clean_case(case, index):
    if not isinstance(case, dict):
        raise ProjectError("Each test case must be an object")
    name = str(case.get("name") or f"case-{index + 1}")
    vars_ = case.get("vars") or {}
    checks = case.get("checks") or {}
    if not isinstance(vars_, dict) or not isinstance(checks, dict):
        raise ProjectError(f"Case '{name}': vars and checks must be objects")
    return {"name": name, "vars": {str(k): str(v) for k, v in vars_.items()}, "checks": checks}


def save_project(prompts, inputs):
    names = []
    for p in prompts:
        name, text = p.get("name"), p.get("text", "")
        if not valid_name(name):
            raise ProjectError(f"Invalid prompt name: {name!r}")
        if name.lower() in (n.lower() for n in names):
            raise ProjectError(f"Duplicate prompt name: {name}")
        if not isinstance(text, str):
            raise ProjectError("Prompt text must be text")
        names.append(name)
    cases = [_clean_case(c, i) for i, c in enumerate(inputs)]

    PROMPTS_DIR.mkdir(exist_ok=True)
    keep = set(names)
    for f in PROMPTS_DIR.glob("*.txt"):  # delete stale files first: a case-only rename (v1 -> V1) works on Windows too
        if valid_name(f.stem) and f.stem not in keep:
            f.unlink()
    for p in prompts:
        (PROMPTS_DIR / f"{p['name']}.txt").write_text(p["text"], encoding="utf-8")
    if INPUTS_FILE.exists():
        try:
            json.loads(INPUTS_FILE.read_text(encoding="utf-8"))
        except ValueError:
            shutil.copy(INPUTS_FILE, INPUTS_FILE.with_suffix(".json.bak"))
    INPUTS_FILE.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def init_examples(lang="en"):
    """Create example files that do not exist yet. Returns the list of created paths."""
    ex = EXAMPLES["tr" if lang == "tr" else "en"]
    PROMPTS_DIR.mkdir(exist_ok=True)
    files = {
        PROMPTS_DIR / "v1.txt": ex["v1"],
        PROMPTS_DIR / "v2.txt": ex["v2"],
        INPUTS_FILE: json.dumps(ex["inputs"], indent=2, ensure_ascii=False) + "\n",
    }
    created = []
    for path, content in files.items():
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            created.append(str(path))
    return created
