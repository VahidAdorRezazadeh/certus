#!/usr/bin/env python3
"""
llm.py - one door to the language model, whichever model it is.

Certus uses a language model ONLY at the language edges:
    1. read the user's request into a part specification   (cad_agent)
    2. write build123d code for that specification          (cad_agent)
    3. an advisory look at the drawing                      (cad_agent)
    4. map a phrase like "the pin hole" to a face group id  (geometry_features)
    5. read the user's prompt into a draft intent form      (intent.py)
Every one of those outputs is measured, validated against a catalogue, or
shown to the user for confirmation. None of them sets an element type, a mesh
size or a verdict.

Two providers:
    anthropic   the Claude API (needs ANTHROPIC_API_KEY)
    openai      any OpenAI-compatible server: Ollama, LM Studio, llama.cpp
                server, vLLM. Talks plain HTTP, no extra package needed.
                    Ollama     http://localhost:11434/v1
                    LM Studio  http://localhost:1234/v1

Configuration, in order of precedence: configure(...) at run time (the GUI
does this), then environment variables:
    CERTUS_LLM_PROVIDER   anthropic | openai          (default anthropic)
    CERTUS_LLM_BASE_URL   for openai                  (default Ollama)
    CERTUS_LLM_MODEL      model name
    CERTUS_LLM_API_KEY    for openai servers that want one (Ollama ignores it)

Content blocks use the Anthropic shape everywhere in the code base:
    {"type": "text", "text": ...}
    {"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}
and are converted here for the OpenAI shape. A text-only local model given an
image will usually return an error from the server. That error is raised, not
swallowed, so the user sees that the model cannot read images.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import json
import os
import re
import urllib.request
import urllib.error

DEFAULT_BASE = {"ollama": "http://localhost:11434/v1",
                "lmstudio": "http://localhost:1234/v1"}


@dataclass
class LLMConfig:
    provider: str = field(default_factory=lambda: os.environ.get(
        "CERTUS_LLM_PROVIDER", "anthropic"))
    base_url: str = field(default_factory=lambda: os.environ.get(
        "CERTUS_LLM_BASE_URL", DEFAULT_BASE["ollama"]))
    model: str = field(default_factory=lambda: os.environ.get(
        "CERTUS_LLM_MODEL", os.environ.get("CAD_AGENT_MODEL",
                                           "claude-sonnet-5")))
    api_key: Optional[str] = field(default_factory=lambda: os.environ.get(
        "CERTUS_LLM_API_KEY"))
    timeout_s: float = 600.0

    def label(self) -> str:
        if self.provider == "anthropic":
            return f"Claude API, model {self.model}"
        return f"OpenAI-compatible server at {self.base_url}, model {self.model}"


CONFIG = LLMConfig()
# every call is logged here, so the report can say which model wrote what
CALL_LOG: List[dict] = []


def configure(**kw) -> LLMConfig:
    for k, v in kw.items():
        if not hasattr(CONFIG, k):
            raise AttributeError(f"unknown LLM setting {k!r}")
        setattr(CONFIG, k, v)
    return CONFIG


def strip_fences(t: str) -> str:
    t = t.strip()
    m = re.match(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$", t, re.S)
    return m.group(1) if m else t


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

def _to_openai_content(content: list) -> list:
    out = []
    for b in content:
        t = b.get("type")
        if t == "text":
            out.append({"type": "text", "text": b["text"]})
        elif t == "image":
            src = b["source"]
            out.append({"type": "image_url", "image_url": {
                "url": f"data:{src['media_type']};base64,{src['data']}"}})
        else:
            raise ValueError(f"unsupported content block {t!r}")
    # a text-only prompt is sent as a plain string: some local servers
    # reject the list form when there is no image in it
    if all(b["type"] == "text" for b in out):
        return "\n\n".join(b["text"] for b in out)
    return out


def _ask_openai(system: str, content: list, max_tokens: int
                ) -> Tuple[str, Optional[str]]:
    url = CONFIG.base_url.rstrip("/") + "/chat/completions"
    body = {"model": CONFIG.model, "max_tokens": max_tokens,
            "temperature": 0.2, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user",
                          "content": _to_openai_content(content)}]}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {CONFIG.api_key or 'none'}"})
    try:
        with urllib.request.urlopen(req, timeout=CONFIG.timeout_s) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"LLM server at {url} returned HTTP {e.code}: "
                           f"{detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach the LLM server at {url} "
                           f"({e.reason}). Is Ollama or LM Studio running, "
                           f"and is the server started?") from None
    ch = (data.get("choices") or [{}])[0]
    text = (ch.get("message") or {}).get("content") or ""
    stop = ch.get("finish_reason")
    return text, ("max_tokens" if stop == "length" else stop)


def _ask_anthropic(system: str, content: list, max_tokens: int
                   ) -> Tuple[str, Optional[str]]:
    from anthropic import Anthropic
    with Anthropic().messages.stream(
            model=CONFIG.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}]) as stream:
        msg = stream.get_final_message()
    text = "".join(b.text for b in msg.content
                   if getattr(b, "type", "") == "text")
    return text, getattr(msg, "stop_reason", None)


def ask_raw(system: str, content: list, max_tokens: int = 2000,
            role: str = "unnamed") -> Tuple[str, Optional[str]]:
    """Returns (text without code fences, stop_reason). stop_reason is
    'max_tokens' when the reply was cut off, for both providers."""
    fn = _ask_anthropic if CONFIG.provider == "anthropic" else _ask_openai
    text, stop = fn(system, content, max_tokens)
    CALL_LOG.append({"role": role, "provider": CONFIG.provider,
                     "model": CONFIG.model, "stop": stop,
                     "chars": len(text)})
    return strip_fences(text), stop


def ask(system: str, content: list, max_tokens: int = 2000,
        role: str = "unnamed") -> str:
    return ask_raw(system, content, max_tokens, role)[0]


def json_or_none(text: str):
    """Best effort JSON recovery: fences, surrounding prose, // comments,
    trailing commas. Local models need this more than frontier ones."""
    cands = []
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        cands.append(m.group(1))
    cands.append(text.strip())
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        cands.append(m.group(0))
    for c in cands:
        for attempt in (c, re.sub(r"//[^\n]*", "", c)):
            for t in (attempt, re.sub(r",(\s*[}\]])", r"\1", attempt)):
                try:
                    obj = json.loads(t)
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    pass
    return None


def list_models() -> List[str]:
    """Models the configured OpenAI-compatible server offers. Empty list if
    the server is not reachable. Used by the GUI to fill a dropdown."""
    if CONFIG.provider == "anthropic":
        return []
    url = CONFIG.base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
        return sorted(m.get("id", "") for m in data.get("data", []))
    except Exception:
        return []


def ping() -> Tuple[bool, str]:
    """A cheap reachability test the GUI shows as a green or red light."""
    try:
        t = ask("Reply with the single word OK.",
                [{"type": "text", "text": "ping"}], max_tokens=20,
                role="ping")
        return True, f"{CONFIG.label()} answered: {t.strip()[:40]!r}"
    except Exception as e:
        return False, f"{CONFIG.label()}: {type(e).__name__}: {e}"
