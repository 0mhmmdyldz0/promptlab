"""Runs prompts x inputs x models, and stores / loads runs."""
import json
import re
import time
from datetime import datetime
from pathlib import Path

from . import providers
from .core import evaluate, render, similarity

RUNS_DIR = Path(".promptlab") / "runs"


# ---------- running ----------

def unique_names(names):
    seen, out = {}, []
    for n in names:
        n = str(n)
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n} ({seen[n]})")
    return out


def make_variants(prompt_names, models):
    multi = len(models) > 1
    return [
        {"label": f"{p} · {m}" if multi else p, "prompt": p, "model": m}
        for m in models
        for p in prompt_names
    ]


def new_run_id():
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id, n = base, 1
    while (RUNS_DIR / f"{run_id}.json").exists():
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def judge_score(provider, model, rubric, prompt, output):
    """Ask a model to rate an answer 1-5. Small local models are rough judges: treat as a hint."""
    question = (
        "You are a strict evaluator. Rate the ANSWER on a scale of 1 to 5 using the criteria below. "
        "Reply with only one digit (1-5).\n\n"
        f"Criteria: {rubric}\n\nTask given to the assistant:\n{prompt}\n\nANSWER:\n{output}\n\nScore (1-5):"
    )
    res = providers.generate(provider, question, model, 8, temperature=0)
    match = re.search(r"[1-5]", res["output"])
    return int(match.group()) if match else None


def execute(templates, inputs, models, provider, max_tokens=512, temperature=None,
            system=None, repeat=1, judge=None, dry_run=False, judge_model=None):
    """Generator. First yields {"type": "start", ...}, then one {"type": "result", "result": {...}} per call.

    Raises providers.ProviderError before the first yield if the setup is not usable.
    """
    names = unique_names([i.get("name", "case") for i in inputs])
    variants = make_variants(list(templates), models)
    label_of = {(v["prompt"], v["model"]): v["label"] for v in variants}
    total = len(inputs) * len(variants) * repeat
    if not dry_run:
        for m in models:
            providers.preflight(provider, m)

    run_id = new_run_id()
    yield {
        "type": "start", "id": run_id, "created": datetime.now().isoformat(timespec="seconds"),
        "total": total, "variants": variants, "inputs": names,
    }

    for model in models:  # model-outer loop: avoids reloading models in Ollama
        for item, name in zip(inputs, names):
            for pname, template in templates.items():
                prompt = render(template, item.get("vars", {}))
                for rep in range(repeat):
                    start = time.time()
                    error = None
                    try:
                        if dry_run:
                            res = {"output": f"[dry-run] {prompt[:70].strip()}...", "input_tokens": 0, "output_tokens": 0}
                        else:
                            res = providers.generate(provider, prompt, model, max_tokens, temperature, system)
                    except Exception as exc:  # keep going; record the failure
                        res = {"output": "", "input_tokens": 0, "output_tokens": 0}
                        error = str(exc)
                    seconds = round(time.time() - start, 2)
                    checks = item.get("checks") or {}
                    live = not error and not dry_run
                    failed = evaluate(res["output"], checks) if checks and live else []
                    score = None
                    if judge and live:
                        try:
                            score = judge_score(provider, judge_model or models[0], judge, prompt, res["output"])
                        except Exception:
                            score = None
                    yield {"type": "result", "result": {
                        "input": name, "prompt": pname, "model": model,
                        "variant": label_of[(pname, model)], "rep": rep,
                        "prompt_text": template, "seconds": seconds, "error": error,
                        "passed": None if (not checks or not live) else not failed,
                        "failed_checks": failed, "score": score,
                        "words": len(res["output"].split()), **res,
                    }}


def build_run(start, results, settings, complete=True):
    return {
        "id": start["id"], "created": start["created"], "complete": complete,
        "settings": settings, "variants": [v["label"] for v in start["variants"]],
        "inputs": start["inputs"], "results": results, "votes": {},
    }


# ---------- storage ----------

def write_run(run):
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{run['id']}.json").write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_run(run):
    """Make runs saved by older versions look like current ones."""
    run.setdefault("votes", {})
    run.setdefault("settings", {})
    for r in run["results"]:
        r.setdefault("variant", r["prompt"])
        r.setdefault("rep", 0)
        r.setdefault("score", None)
        r.setdefault("words", len(r.get("output", "").split()))
        r.setdefault("failed_checks", [])
        r.setdefault("passed", None)
    return run


def load_run(ref=None):
    runs = sorted(RUNS_DIR.glob("*.json"))
    if ref in (None, "latest"):
        if not runs:
            raise FileNotFoundError("No runs yet")
        path = runs[-1]
    else:
        path = RUNS_DIR / f"{ref}.json"
        if not path.exists():
            raise FileNotFoundError(f"Run not found: {ref}")
    return normalize_run(json.loads(path.read_text(encoding="utf-8")))


def group(results):
    """-> ({input: {variant: [results by rep]}}, [variants in order])"""
    rows, variants = {}, []
    for r in results:
        rows.setdefault(r["input"], {}).setdefault(r["variant"], []).append(r)
        if r["variant"] not in variants:
            variants.append(r["variant"])
    for by_var in rows.values():
        for lst in by_var.values():
            lst.sort(key=lambda r: r["rep"])
    return rows, variants


def summarize(results, votes=None):
    stats = {}
    for r in results:
        s = stats.setdefault(r["variant"], {"n": 0, "checked": 0, "passed": 0, "secs": 0.0, "words": 0,
                                            "tokens": 0, "errors": 0, "scores": [], "wins": 0})
        s["n"] += 1
        s["secs"] += r["seconds"]
        s["words"] += r.get("words", 0)
        s["tokens"] += r["input_tokens"] + r["output_tokens"]
        s["errors"] += 1 if r["error"] else 0
        if r.get("passed") is not None:
            s["checked"] += 1
            s["passed"] += 1 if r["passed"] else 0
        if r.get("score") is not None:
            s["scores"].append(r["score"])
    for variant in (votes or {}).values():
        if variant in stats:
            stats[variant]["wins"] += 1
    return stats


def stability(results):
    """-> {variant: 0..1} average similarity between repeated outputs of the same case (needs repeat > 1)."""
    by = {}
    for r in results:
        if not r["error"]:
            by.setdefault((r["variant"], r["input"]), []).append(r["output"])
    acc = {}
    for (variant, _), outs in by.items():
        if len(outs) < 2:
            continue
        pairs = [similarity(outs[i], outs[j]) for i in range(len(outs)) for j in range(i + 1, len(outs))]
        acc.setdefault(variant, []).extend(pairs)
    return {v: sum(x) / len(x) for v, x in acc.items()}


def rank_variants(stats):
    """Order variants best-first: your picks, then check pass rate, model score, fewer errors, speed.

    Returns (ordered names, best name or None). `best` is None when there is no real difference.
    """
    def key(item):
        s = item[1]
        rate = s["passed"] / s["checked"] if s["checked"] else 0
        score = sum(s["scores"]) / len(s["scores"]) if s["scores"] else 0
        return (-s["wins"], -rate, -score, s["errors"], s["secs"] / max(s["n"], 1))
    ordered = sorted(stats.items(), key=key)
    names = [n for n, _ in ordered]
    if len(ordered) < 2:
        return names, None
    top, second = key(ordered[0])[:3], key(ordered[1])[:3]
    return names, (names[0] if top != second and top != (0, 0, 0) else None)


def list_runs():
    out = []
    for p in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            run = normalize_run(json.loads(p.read_text(encoding="utf-8")))
            stats = summarize(run["results"], run["votes"])
        except Exception:
            continue
        out.append({
            "id": run["id"], "created": run.get("created"), "complete": run.get("complete", True),
            "variants": list(stats), "count": len(run["results"]),
            "checked": sum(s["checked"] for s in stats.values()),
            "passed": sum(s["passed"] for s in stats.values()),
            "votes": len(run["votes"]),
        })
    return out
