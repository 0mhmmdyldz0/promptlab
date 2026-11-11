"""Terminal, HTML and CSV views of a run."""
import csv
import html
import io
import shutil
import textwrap
from pathlib import Path

from .runner import group, rank_variants, stability, summarize


def status_text(r):
    if r["error"]:
        return "ERROR"
    if r.get("passed") is None:
        return ""
    return "PASS" if r["passed"] else "FAIL"


def _meta(r, multi):
    bits = [f"#{r['rep'] + 1}" if multi else "", f"{r['seconds']}s", f"{r['input_tokens']}+{r['output_tokens']} tok",
            status_text(r), f"score {r['score']}" if r.get("score") is not None else ""]
    return " ".join(b for b in bits if b)


def show_run(run, html_out=None, csv_out=None):
    rows, variants = group(run["results"])
    votes = run.get("votes", {})
    total_width = shutil.get_terminal_size((100, 20)).columns
    stacked = (total_width - 3 * len(variants)) // len(variants) < 34  # too narrow for side-by-side
    width = total_width - 2 if stacked else (total_width - 3 * len(variants)) // len(variants)
    for name, by_var in rows.items():
        print(f"== {name} " + "=" * max(0, (total_width if stacked else width * len(variants)) - len(name) - 4))
        cols = []
        for v in variants:
            rs = by_var.get(v, [])
            lines = textwrap.wrap(f"[{v}]" + (" *WINNER*" if votes.get(name) == v else ""), width) + ["-" * width]
            for r in rs:
                lines += textwrap.wrap(_meta(r, len(rs) > 1), width)
                lines += textwrap.wrap(f"ERROR: {r['error']}" if r["error"] else r["output"], width) or [""]
                for f in r.get("failed_checks", []):
                    lines += textwrap.wrap("! " + f, width)
                if len(rs) > 1:
                    lines.append("")
            cols.append(lines)
        if stacked:
            for lines in cols:
                print("\n".join(lines) + "\n")
        else:
            for i in range(max(len(c) for c in cols)):
                print(" | ".join((c[i] if i < len(c) else "").ljust(width) for c in cols))
            print()

    print("Summary")
    stats = summarize(run["results"], votes)
    stable = stability(run["results"])
    order, best = rank_variants(stats)
    for v in order:
        s = stats[v]
        checks = f"checks {s['passed']}/{s['checked']}" if s["checked"] else "no checks"
        score = f"   score {sum(s['scores']) / len(s['scores']):.1f}" if s["scores"] else ""
        wins = f"   wins {s['wins']}" if votes else ""
        stab = f"   stability {stable[v] * 100:.0f}%" if v in stable else ""
        star = "*" if v == best else " "
        print(f" {star}{v:<22} {checks:<14} avg {s['secs'] / s['n']:.1f}s   ~{s['words'] / s['n']:.0f} words{score}{wins}{stab}   errors {s['errors']}")
    if best:
        print(f"\n  * best so far: {best}")
    if html_out:
        Path(html_out).write_text(render_html(run), encoding="utf-8")
        print(f"\nHTML report: {html_out}")
    if csv_out:
        Path(csv_out).write_text(to_csv(run), encoding="utf-8-sig")
        print(f"CSV file: {csv_out}")


def to_csv(run):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["run", "input", "variant", "prompt", "model", "rep", "passed", "failed_checks",
                "score", "winner", "words", "seconds", "input_tokens", "output_tokens", "error", "output"])
    for r in run["results"]:
        w.writerow([run["id"], r["input"], r["variant"], r["prompt"], r["model"], r["rep"] + 1,
                    "" if r["passed"] is None else ("yes" if r["passed"] else "no"),
                    "; ".join(r.get("failed_checks", [])), "" if r["score"] is None else r["score"],
                    "yes" if run.get("votes", {}).get(r["input"]) == r["variant"] and r["rep"] == 0 else "",
                    r.get("words", ""), r["seconds"], r["input_tokens"], r["output_tokens"],
                    r["error"] or "", r["output"]])
    return buf.getvalue()


def render_html(run):
    rows, variants = group(run["results"])
    votes = run.get("votes", {})
    esc = html.escape
    head = "".join(f"<th>{esc(v)}</th>" for v in variants)
    body = ""
    for name, by_var in rows.items():
        cells = ""
        for v in variants:
            rs = by_var.get(v, [])
            win = votes.get(name) == v
            inner = ""
            for r in rs:
                st = status_text(r)
                badge = f"<span class='b {st.lower()}'>{st}</span>" if st else ""
                score = f"<span class='b'>score {r['score']}</span>" if r.get("score") is not None else ""
                why = "".join(f"<div class='why'>! {esc(f)}</div>" for f in r.get("failed_checks", []))
                rep = f"<b>#{r['rep'] + 1}</b> " if len(rs) > 1 else ""
                text = f"ERROR: {r['error']}" if r["error"] else r["output"]
                inner += (f"<div class='o'>{rep}{badge}{score}<div>{esc(text)}</div>{why}"
                          f"<div class='meta'>{r['seconds']}s, {r['input_tokens']}+{r['output_tokens']} tok</div></div>")
            cells += f"<td class='{'win' if win else ''}'>{'🏆 ' if win else ''}{inner}</td>"
        body += f"<tr><th>{esc(name)}</th>{cells}</tr>"
    summary = ""
    stats = summarize(run["results"], votes)
    stable = stability(run["results"])
    order, best = rank_variants(stats)
    for v in order:
        s = stats[v]
        checks = f"{s['passed']}/{s['checked']} checks" if s["checked"] else "no checks"
        wins = f", {s['wins']} wins" if votes else ""
        stab = f", stability {stable[v] * 100:.0f}%" if v in stable else ""
        star = "★ " if v == best else ""
        summary += f"<li><b>{star}{esc(v)}</b>: {checks}{wins}{stab}, avg {s['secs'] / s['n']:.1f}s, {s['tokens']} tokens</li>"
    return (
        "<!doctype html><meta charset='utf-8'><title>promptlab " + esc(run["id"]) + "</title>"
        "<style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ccc;padding:.6rem;vertical-align:top;white-space:pre-wrap;text-align:left}"
        "th{background:#f4f4f4}.meta{color:#777;font-size:12px;margin-top:.3rem}.why{color:#b00;font-size:12px}"
        ".o{margin-bottom:.8rem}.win{background:#fff8dc}"
        ".b{display:inline-block;font-size:11px;font-weight:700;padding:1px 6px;border-radius:4px;margin:0 .3rem .3rem 0;background:#eee}"
        ".pass{background:#d6f5dc;color:#116329}.fail,.error{background:#ffd9d9;color:#a40000}</style>"
        f"<h1>promptlab run {esc(run['id'])}</h1><ul>{summary}</ul>"
        f"<table><tr><th>input</th>{head}</tr>{body}</table>"
    )


def compare_runs(old, new):
    """Text comparison of two saved runs: pass rate per variant, and cases that got better / worse."""
    a, b = summarize(old["results"], old.get("votes")), summarize(new["results"], new.get("votes"))
    lines = [f"Compare {old['id']} -> {new['id']}", ""]
    for v in dict.fromkeys(list(a) + list(b)):
        def rate(s):
            return f"{s['passed']}/{s['checked']}" if s and s["checked"] else "-"
        pa = a[v]["passed"] / a[v]["checked"] if v in a and a[v]["checked"] else None
        pb = b[v]["passed"] / b[v]["checked"] if v in b and b[v]["checked"] else None
        arrow = ""
        if pa is not None and pb is not None:
            arrow = "  (better)" if pb > pa else "  (worse)" if pb < pa else "  (same)"
        lines.append(f"  {v:<24} {rate(a.get(v)):>7} -> {rate(b.get(v)):<7}{arrow}")
    def verdicts(run):
        out = {}
        for r in run["results"]:
            if r.get("passed") is not None:
                out.setdefault((r["variant"], r["input"]), []).append(r["passed"])
        return {k: all(x) for k, x in out.items()}
    va, vb = verdicts(old), verdicts(new)
    fixed = sorted(k for k in vb if va.get(k) is False and vb[k])
    broke = sorted(k for k in vb if va.get(k) is True and not vb[k])
    if fixed:
        lines += ["", "Now passing:"] + [f"  + {v} / {i}" for v, i in fixed]
    if broke:
        lines += ["", "Now failing (regressions):"] + [f"  - {v} / {i}" for v, i in broke]
    return "\n".join(lines)
