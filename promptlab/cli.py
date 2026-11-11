"""promptlab: compare prompt versions (and models) on your own test cases."""
import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

from . import __version__, project, providers, report, runner, server


def cmd_init(args):
    for line in project.init_examples("tr" if args.tr else "en") or ["Nothing to do: example files already exist."]:
        print(f"create {line}" if not line.startswith("Nothing") else line)
    print("\nNext:  promptlab        (opens the web UI)\n   or: promptlab run")


def cmd_doctor(args):
    print(f"promptlab {__version__}")
    print(f"ANTHROPIC_API_KEY: {'set' if os.environ.get('ANTHROPIC_API_KEY') else 'not set'}")
    print(f"'auto' provider would use: {providers.pick_provider('auto')}")
    try:
        models = providers.ollama_models()
        print(f"Ollama: running at {providers.ollama_url()}")
        print("Installed models: " + (", ".join(models) if models else "none"))
        if not models:
            print(f"Next:  ollama pull {providers.DEFAULT_MODELS['ollama']}")
        else:
            print("You are ready. Start the UI with:  promptlab")
    except providers.ProviderError as exc:
        print(f"Ollama: NOT reachable\n  {exc}")


def cmd_serve(args):
    if args.dir:
        Path(args.dir).mkdir(parents=True, exist_ok=True)
        os.chdir(args.dir)
    server.serve(port=args.port, open_browser=not args.no_browser)


def cmd_run(args):
    files = [Path(p) for p in args.prompts] or sorted(project.PROMPTS_DIR.glob("*.txt"))
    if not files:
        sys.exit("No prompt files found. Run `promptlab init` first, or pass files: promptlab run a.txt b.txt")
    templates = {f.stem: f.read_text(encoding="utf-8") for f in files}
    try:
        inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.exit(f"Cannot read {args.inputs}: {exc}")
    provider = providers.pick_provider(args.provider)
    models = [m.strip() for m in (args.models or args.model or "").split(",") if m.strip()] or [providers.default_model(provider)]
    settings = {"provider": "dry-run" if args.dry_run else provider, "models": models, "temperature": args.temperature,
                "repeat": args.repeat, "max_tokens": args.max_tokens, "system": args.system, "judge": args.judge,
                "judge_model": args.judge_model, "dry_run": args.dry_run}
    print(f"provider: {settings['provider']}   models: {', '.join(models)}   repeat: {args.repeat}\n")

    start, results = None, []
    try:
        for event in runner.execute(templates, inputs, models, provider, args.max_tokens, args.temperature,
                                    args.system, args.repeat, args.judge, args.dry_run, args.judge_model):
            if event["type"] == "start":
                start = event
                continue
            r = event["result"]
            results.append(r)
            rep = f" #{r['rep'] + 1}" if args.repeat > 1 else ""
            line = f"[{len(results)}/{start['total']}] {r['input']} x {r['variant']}{rep}  {r['seconds']}s  {report.status_text(r)}"
            print(line.rstrip())
    except providers.ProviderError as exc:
        sys.exit(f"Error: {exc}")
    except KeyboardInterrupt:
        print("\nInterrupted: saving what finished so far.")
    if not start or not results:
        return
    run = runner.build_run(start, results, settings, complete=len(results) == start["total"])
    runner.write_run(run)
    print(f"\nsaved {runner.RUNS_DIR / (run['id'] + '.json')}\n")
    report.show_run(run, html_out=args.html, csv_out=args.csv)
    if args.fail_under is not None:
        rates = [s["passed"] / s["checked"] for s in runner.summarize(results).values() if s["checked"]]
        worst = min(rates) if rates else 1.0
        if worst < args.fail_under:
            sys.exit(f"\nFAILED: lowest check pass rate {worst * 100:.0f}% is under {args.fail_under * 100:.0f}%")


def _load(ref):
    try:
        return runner.load_run(ref)
    except FileNotFoundError as exc:
        sys.exit(str(exc))


def cmd_show(args):
    report.show_run(_load(args.run), html_out=args.html, csv_out=args.csv)


def cmd_export(args):
    run = _load(args.run)
    if not (args.csv or args.html):
        args.csv = f"promptlab-{run['id']}.csv"
    if args.csv:
        Path(args.csv).write_text(report.to_csv(run), encoding="utf-8-sig")
        print(f"CSV file: {args.csv}")
    if args.html:
        Path(args.html).write_text(report.render_html(run), encoding="utf-8")
        print(f"HTML report: {args.html}")


def cmd_compare(args):
    print(report.compare_runs(_load(args.old), _load(args.new)))


def cmd_import(args):
    """Turn a CSV (e.g. from Excel) into inputs.json. Columns: name, checks (optional), then one column per {{variable}}."""
    try:
        text = Path(args.csv).read_text(encoding="utf-8-sig")
        first = text.splitlines()[0] if text.strip() else ""
        rows = list(csv.DictReader(io.StringIO(text), delimiter=";" if first.count(";") > first.count(",") else ","))
    except OSError as exc:
        sys.exit(f"Cannot read {args.csv}: {exc}")
    cases = []
    for i, row in enumerate(rows):
        row = {(k or "").strip(): (v or "") for k, v in row.items()}
        checks = {}
        for part in filter(None, (x.strip() for x in row.pop("checks", "").split("|"))):
            key, _, val = part.partition("=")
            key, val = key.strip(), val.strip()
            if key in ("max_words", "min_words", "max_chars", "min_chars", "max_sentences"):
                checks[key] = int(val)
            elif key in ("contains", "not_contains", "any_of", "regex", "json_keys"):
                checks[key] = [x.strip() for x in val.split(",") if x.strip()]
            elif key == "is_json":
                checks[key] = val.lower() not in ("0", "false", "no", "")
            elif key in ("equals", "starts_with"):
                checks[key] = val
        name = row.pop("name", "") or f"case-{i + 1}"
        cases.append({"name": name, "vars": row, "checks": checks})
    out = Path(args.out)
    out.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(cases)} test cases written to {out}")


def cmd_list(args):
    runs = runner.list_runs()
    if not runs:
        print("No runs yet.")
    for r in runs:
        checks = f"checks {r['passed']}/{r['checked']}" if r["checked"] else "no checks"
        print(f"{r['id']}  {r['count']:>3} results  {checks:<16} {', '.join(r['variants'])}")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Turkish characters on Windows
        except Exception:
            pass

    parser = argparse.ArgumentParser(prog="promptlab", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")

    ui = sub.add_parser("serve", help="open the web UI (default when you type just `promptlab`)")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--dir", help="project folder (default: current folder)")
    ui.add_argument("--no-browser", action="store_true")
    ui.set_defaults(fn=cmd_serve)

    init = sub.add_parser("init", help="create example prompts and test cases")
    init.add_argument("--tr", action="store_true", help="Turkish examples")
    init.set_defaults(fn=cmd_init)

    sub.add_parser("doctor", help="check your setup (Ollama, models, API key)").set_defaults(fn=cmd_doctor)

    run = sub.add_parser("run", help="run prompts against test cases from the terminal")
    run.add_argument("prompts", nargs="*", help="prompt files (default: everything in prompts/)")
    run.add_argument("--inputs", default="inputs.json", help="test cases file (default: inputs.json)")
    run.add_argument("--provider", choices=["auto", "ollama", "anthropic"], default="auto",
                     help="auto = anthropic if ANTHROPIC_API_KEY is set, else local Ollama")
    run.add_argument("--model", help="one model name")
    run.add_argument("--models", help="comma-separated models to compare, e.g. qwen2.5:3b,llama3.2:3b")
    run.add_argument("--repeat", type=int, default=1, help="run every case N times to see how stable the output is")
    run.add_argument("--temperature", type=float, help="sampling temperature (default: model default)")
    run.add_argument("--system", help="system prompt")
    run.add_argument("--judge", metavar="CRITERIA", help="let the model rate each output 1-5 against these criteria")
    run.add_argument("--judge-model", help="model used for --judge (default: the first model)")
    run.add_argument("--fail-under", type=float, metavar="RATE",
                     help="exit with an error if any version passes fewer checks than this (0-1), e.g. 0.8; for CI")
    run.add_argument("--max-tokens", type=int, default=512)
    run.add_argument("--dry-run", action="store_true", help="skip model calls (test the file setup)")
    run.add_argument("--html", help="write an HTML report")
    run.add_argument("--csv", help="write a CSV file (opens in Excel)")
    run.set_defaults(fn=cmd_run)

    show = sub.add_parser("show", help="show a saved run side by side")
    show.add_argument("run", nargs="?", default="latest")
    show.add_argument("--html")
    show.add_argument("--csv")
    show.set_defaults(fn=cmd_show)

    exp = sub.add_parser("export", help="export a saved run to CSV / HTML")
    exp.add_argument("run", nargs="?", default="latest")
    exp.add_argument("--csv")
    exp.add_argument("--html")
    exp.set_defaults(fn=cmd_export)

    cmp_ = sub.add_parser("compare", help="compare two saved runs (did my change help or break something?)")
    cmp_.add_argument("old", help="run id (see `promptlab list`)")
    cmp_.add_argument("new", nargs="?", default="latest")
    cmp_.set_defaults(fn=cmd_compare)

    imp = sub.add_parser("import", help="make inputs.json from a CSV file (columns: name, checks, <variables>)")
    imp.add_argument("csv")
    imp.add_argument("--out", default="inputs.json")
    imp.set_defaults(fn=cmd_import)

    sub.add_parser("list", help="list saved runs").set_defaults(fn=cmd_list)

    args = parser.parse_args(argv)
    if args.cmd is None:
        args = parser.parse_args(["serve"])
    args.fn(args)


if __name__ == "__main__":
    main()
