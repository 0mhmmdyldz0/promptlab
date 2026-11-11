"""Model backends. Ollama (local, free, no API key) and Anthropic (cloud, needs a key)."""
import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_MODELS = {"ollama": "qwen2.5:3b", "anthropic": "claude-sonnet-5-5"}
THINK = re.compile(r"<think>.*?</think>", re.S)


class ProviderError(Exception):
    pass


def ollama_url():
    url = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    return url if url.startswith("http") else "http://" + url


def pick_provider(requested="auto"):
    if requested != "auto":
        return requested
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "ollama"


def default_model(provider):
    return os.environ.get("PROMPTLAB_MODEL") or DEFAULT_MODELS[provider]


def _ollama_request(path, payload=None, timeout=600):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        ollama_url() + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", exc.reason)
        except Exception:
            detail = exc.reason
        raise ProviderError(f"Ollama error: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(
            f"Cannot reach Ollama at {ollama_url()} ({exc.reason}). "
            "Install it from https://ollama.com/download and make sure it is running."
        ) from exc
    except OSError as exc:  # timeouts etc.
        raise ProviderError(f"Network error talking to Ollama: {exc}") from exc


def ollama_models():
    return [m["name"] for m in _ollama_request("/api/tags", timeout=5).get("models", [])]


def preflight(provider, model):
    """Fail early with a helpful message instead of erroring on every request."""
    if provider == "ollama":
        installed = ollama_models()
        wanted = model if ":" in model else model + ":latest"
        if wanted not in installed:
            have = ", ".join(installed) or "none"
            raise ProviderError(
                f"Model '{model}' is not installed in Ollama (installed: {have}).\n"
                f"Run:  ollama pull {model}"
            )
    elif provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set. Set it, or use the free local option: --provider ollama"
            )
    else:
        raise ProviderError(f"Unknown provider: {provider}")


def generate(provider, prompt, model, max_tokens, temperature=None, system=None):
    if provider == "ollama":
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        options = {"num_predict": max_tokens}
        if temperature is not None:
            options["temperature"] = temperature
        res = _ollama_request(
            "/api/chat",
            {"model": model, "messages": messages, "stream": False, "keep_alive": "15m", "options": options},
        )
        return {
            "output": THINK.sub("", res.get("message", {}).get("content", "")).strip(),
            "input_tokens": res.get("prompt_eval_count", 0),
            "output_tokens": res.get("eval_count", 0),
        }
    if provider == "anthropic":
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError('Install the SDK first: pip install "promptlab[anthropic]"') from exc
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        msg = anthropic.Anthropic().messages.create(**kwargs)
        return {
            "output": "".join(b.text for b in msg.content if b.type == "text"),
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        }
    raise ProviderError(f"Unknown provider: {provider}")
