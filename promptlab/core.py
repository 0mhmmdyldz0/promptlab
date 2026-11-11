"""Pure helpers: template rendering and output checks."""
import difflib
import json
import re

VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")
FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def render(template, variables):
    """Fill {{name}} placeholders; unknown placeholders are left untouched."""
    return VAR.sub(lambda m: str(variables.get(m.group(1), m.group(0))), template)


def placeholders(template):
    seen = []
    for name in VAR.findall(template):
        if name not in seen:
            seen.append(name)
    return seen


def _as_list(value):
    items = value if isinstance(value, list) else [value]
    return [str(x) for x in items if str(x) != ""]


def evaluate(output, checks):
    """Return a list of failed-check messages (empty list = all passed).

    Supported checks:
      max_words, min_words, max_chars   numbers
      contains, not_contains            text or list of text (case-insensitive)
      starts_with                       text (case-insensitive)
      regex                             pattern or list of patterns (case-insensitive)
      is_json                           true = output must be valid JSON (code fences are ignored)
      min_chars                         number
      equals                            text; whole output must equal it (case/space-insensitive)
      any_of                            list of text; at least one must appear (case-insensitive)
      json_keys                         list of keys the JSON object must have (implies valid JSON)
      max_sentences                     number (counts . ! ? endings)
    """
    fails = []
    words = len(output.split())
    low = output.lower()
    if "max_words" in checks and words > checks["max_words"]:
        fails.append(f"too long: {words} words (max {checks['max_words']})")
    if "min_words" in checks and words < checks["min_words"]:
        fails.append(f"too short: {words} words (min {checks['min_words']})")
    if "max_chars" in checks and len(output) > checks["max_chars"]:
        fails.append(f"too long: {len(output)} characters (max {checks['max_chars']})")
    for s in _as_list(checks.get("contains", [])):
        if s.lower() not in low:
            fails.append(f"missing '{s}'")
    for s in _as_list(checks.get("not_contains", [])):
        if s.lower() in low:
            fails.append(f"should not contain '{s}'")
    if checks.get("starts_with") and not output.lstrip().lower().startswith(str(checks["starts_with"]).lower()):
        fails.append(f"should start with '{checks['starts_with']}'")
    for pat in _as_list(checks.get("regex", [])):
        try:
            if not re.search(pat, output, re.I | re.S):
                fails.append(f"no match for /{pat}/")
        except re.error:
            fails.append(f"invalid regex /{pat}/")
    if "min_chars" in checks and len(output) < checks["min_chars"]:
        fails.append(f"too short: {len(output)} characters (min {checks['min_chars']})")
    if checks.get("equals") and " ".join(output.lower().split()) != " ".join(str(checks["equals"]).lower().split()):
        fails.append(f"should equal '{checks['equals']}'")
    any_of = _as_list(checks.get("any_of", []))
    if any_of and not any(s.lower() in low for s in any_of):
        fails.append("none of: " + ", ".join(f"'{s}'" for s in any_of))
    if "max_sentences" in checks:
        n = len([x for x in re.split(r"[.!?]+(?:\s+|$)", output.strip()) if x.strip()])
        if n > checks["max_sentences"]:
            fails.append(f"too many sentences: {n} (max {checks['max_sentences']})")
    keys = _as_list(checks.get("json_keys", []))
    if checks.get("is_json") or keys:
        try:
            data = json.loads(FENCE.sub("", output.strip()))
        except ValueError:
            fails.append("not valid JSON")
        else:
            if keys:
                have = data if isinstance(data, dict) else {}
                missing = [k for k in keys if k not in have]
                if missing:
                    fails.append("JSON is missing keys: " + ", ".join(missing))
    return fails


def similarity(a, b):
    """0..1 text similarity (1 = identical). Used to measure how stable repeated runs are."""
    a, b = " ".join(a.split()), " ".join(b.split())
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()
