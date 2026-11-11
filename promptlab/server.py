"""Local web UI (standard library only). Listens on 127.0.0.1 only."""
import json
import os
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, project, providers, report, runner

UI_FILE = Path(__file__).parent / "ui.html"
RUN_LOCK = threading.Lock()
RUN_RE = re.compile(r"^/api/runs/([\w\-]+)(?:/(vote|export))?$")
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _clamp(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


class Handler(BaseHTTPRequestHandler):
    server_version = "promptlab"

    def log_message(self, *args):  # keep the terminal quiet
        pass

    # ----- helpers -----
    def _send(self, data, ctype, code=200, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

    def _error(self, message, code=400):
        self._json({"error": str(message)}, code)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 5_000_000:
            raise ValueError("Request too large")
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _guard(self):
        """Block other websites from talking to this local server (DNS rebinding / CSRF)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            self._error("Forbidden host", 403)
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in LOCAL_HOSTS:
            self._error("Forbidden origin", 403)
            return False
        return True

    def _dispatch(self, fn):
        if not self._guard():
            return
        try:
            fn(urlparse(self.path))
        except FileNotFoundError as exc:
            self._error(exc, 404)
        except (project.ProjectError, ValueError) as exc:
            self._error(exc, 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:  # never crash the server thread
            self._error(f"Server error: {exc}", 500)

    def do_GET(self):
        self._dispatch(self._get)

    def do_POST(self):
        self._dispatch(self._post)

    def do_PUT(self):
        self._dispatch(self._put)

    # ----- GET -----
    def _get(self, url):
        path = url.path
        if path == "/":
            self._send(UI_FILE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/status":
            ollama = {"ok": False, "models": [], "error": None}
            try:
                ollama["models"], ollama["ok"] = providers.ollama_models(), True
            except providers.ProviderError as exc:
                ollama["error"] = str(exc)
            self._json({
                "version": __version__, "cwd": str(Path.cwd()), "ollama": ollama,
                "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "auto_provider": providers.pick_provider("auto"),
                "default_models": {k: providers.default_model(k) for k in providers.DEFAULT_MODELS},
            })
        elif path == "/api/project":
            self._json(project.load_project())
        elif path == "/api/runs":
            self._json(runner.list_runs())
        else:
            m = RUN_RE.match(path)
            if not m:
                return self._error("Not found", 404)
            run = runner.load_run(m.group(1))
            if m.group(2) == "export":
                fmt = (parse_qs(url.query).get("fmt") or ["csv"])[0]
                if fmt == "html":
                    data, ctype = report.render_html(run).encode("utf-8"), "text/html; charset=utf-8"
                else:
                    fmt, data, ctype = "csv", ("\ufeff" + report.to_csv(run)).encode("utf-8"), "text/csv; charset=utf-8"
                self._send(data, ctype, headers={"Content-Disposition": f'attachment; filename="promptlab-{run["id"]}.{fmt}"'})
            elif m.group(2):
                self._error("Use POST", 405)
            else:
                self._json(run)

    # ----- PUT -----
    def _put(self, url):
        if url.path != "/api/project":
            return self._error("Not found", 404)
        body = self._body()
        project.save_project(body.get("prompts") or [], body.get("inputs") or [])
        self._json({"ok": True})

    # ----- POST -----
    def _post(self, url):
        if url.path == "/api/init":
            lang = self._body().get("lang", "en")
            self._json({"created": project.init_examples(lang)})
        elif url.path == "/api/run":
            self._run(self._body())
        else:
            m = RUN_RE.match(url.path)
            if not (m and m.group(2) == "vote"):
                return self._error("Not found", 404)
            body, run = self._body(), runner.load_run(m.group(1))
            name, variant = body.get("input"), body.get("variant")
            known_inputs = run.get("inputs") or sorted({r["input"] for r in run["results"]})
            known_variants = run.get("variants") or sorted({r["variant"] for r in run["results"]})
            if name not in known_inputs:
                return self._error("Unknown input")
            if variant is None:
                run["votes"].pop(name, None)
            elif variant in known_variants:
                run["votes"][name] = variant
            else:
                return self._error("Unknown variant")
            runner.write_run(run)
            self._json({"ok": True})

    def _emit(self, event):
        self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _run(self, body):
        proj = project.load_project()
        by_name = {p["name"]: p["text"] for p in proj["prompts"]}
        names = [n for n in (body.get("prompts") or []) if isinstance(n, str)]
        missing = [n for n in names if n not in by_name]
        if not names or missing:
            return self._error(f"Unknown or missing prompts: {missing or 'none selected'}")
        if not proj["inputs"]:
            return self._error("No test cases yet")
        provider = providers.pick_provider(body.get("provider") or "auto")
        if provider not in providers.DEFAULT_MODELS:
            return self._error(f"Unknown provider: {provider}")
        models = [m.strip() for m in (body.get("models") or []) if isinstance(m, str) and m.strip()]
        models = models or [providers.default_model(provider)]
        repeat = _clamp(body.get("repeat"), 1, 20, 1)
        if len(proj["inputs"]) * len(names) * len(models) * repeat > 2000:
            return self._error("Too many calls in one run (max 2000)")
        try:
            temperature = None if body.get("temperature") is None else float(body["temperature"])
        except (TypeError, ValueError):
            temperature = None
        settings = {
            "provider": provider, "models": models, "temperature": temperature, "repeat": repeat,
            "max_tokens": _clamp(body.get("max_tokens"), 16, 8192, 512),
            "system": body.get("system") or None, "judge": body.get("judge") or None,
            "judge_model": body.get("judge_model") or None,
            "dry_run": bool(body.get("dry_run")),
        }
        if not RUN_LOCK.acquire(blocking=False):
            return self._error("Another run is in progress", 409)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        start, results, complete = None, [], False
        try:
            gen = runner.execute(
                {n: by_name[n] for n in names}, proj["inputs"], models, provider,
                max_tokens=settings["max_tokens"], temperature=temperature, system=settings["system"],
                repeat=repeat, judge=settings["judge"], dry_run=settings["dry_run"],
                judge_model=settings["judge_model"],
            )
            for event in gen:
                if event["type"] == "start":
                    start = event
                else:
                    results.append(event["result"])
                self._emit(event)
            complete = True
            self._emit({"type": "done", "id": start["id"]})
        except providers.ProviderError as exc:
            try:
                self._emit({"type": "error", "message": str(exc)})
            except OSError:
                pass
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the browser pressed Stop; keep what we have
        finally:
            if start and results:
                runner.write_run(runner.build_run(start, results, settings, complete))
            RUN_LOCK.release()


def make_server(port=8765, host="127.0.0.1"):
    """Bind to the first free port starting at `port` (0 = any free port)."""
    for p in ([0] if port == 0 else range(port, port + 20)):
        try:
            server = ThreadingHTTPServer((host, p), Handler)
            server.daemon_threads = True
            return server
        except OSError:
            continue
    raise OSError(f"No free port found starting at {port}")


def serve(port=8765, open_browser=True):
    server = make_server(port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"promptlab is running at {url}\nProject folder: {Path.cwd()}\nPress Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
