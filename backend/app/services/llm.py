"""OpenAI-compatible chat client (OpenAI, OpenRouter, any compatible gateway).

* transient failures (429, 5xx, network, empty answers) -> exponential backoff, then the fallback model
* permanent failures (bad key, no credits, bad request) -> PermanentError, no pointless retries
* `chat_json` parses structured answers robustly (code fences, leading prose, trailing commas)
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import get_config
from ..events import get_logger
from ..stages import PermanentError

log = get_logger("llm")


class TransientLLMError(Exception):
    pass


def is_configured() -> bool:
    return bool(get_config().llm_api_key)


def _headers() -> dict[str, str]:
    c = get_config()
    h = {"Authorization": f"Bearer {c.llm_api_key}", "Content-Type": "application/json"}
    if c.llm_http_referer:
        h["HTTP-Referer"] = c.llm_http_referer
    if c.llm_app_title:
        h["X-Title"] = c.llm_app_title
    return h


def _endpoint() -> str:
    return get_config().llm_base_url.rstrip("/") + "/chat/completions"


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message") or err)[:400]
        return str(err)[:400]
    except Exception:
        return resp.text[:400]


def _call_once(model: str, messages: list[dict], temperature: float, json_mode: bool,
               max_tokens: Optional[int], timeout: float) -> str:
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        resp = httpx.post(_endpoint(), headers=_headers(), json=payload, timeout=timeout)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise TransientLLMError(f"network error: {type(e).__name__}: {e}") from e

    if resp.status_code == 400 and json_mode and "response_format" in resp.text.lower():
        # model does not support JSON mode -> same call without it (the prompt still demands JSON)
        return _call_once(model, messages, temperature, False, max_tokens, timeout)
    if resp.status_code in (401, 403):
        raise PermanentError(f"LLM authentication failed ({resp.status_code}): {_error_text(resp)}")
    if resp.status_code == 402:
        raise PermanentError(f"LLM account has no credits: {_error_text(resp)}")
    if resp.status_code in (408, 409, 425, 429) or resp.status_code >= 500:
        raise TransientLLMError(f"HTTP {resp.status_code}: {_error_text(resp)}")
    if resp.status_code >= 400:
        raise PermanentError(f"LLM request rejected ({resp.status_code}) for model '{model}': {_error_text(resp)}")

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        raise TransientLLMError(f"malformed LLM response: {resp.text[:200]}") from e
    if not content or not str(content).strip():
        raise TransientLLMError("empty LLM answer")
    return str(content)


@retry(
    retry=retry_if_exception_type(TransientLLMError),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _call_with_retry(model: str, messages: list[dict], temperature: float, json_mode: bool,
                     max_tokens: Optional[int], timeout: float) -> str:
    return _call_once(model, messages, temperature, json_mode, max_tokens, timeout)


def chat(messages: list[dict], *, temperature: float = 0.8, json_mode: bool = False,
         max_tokens: Optional[int] = None, timeout: float = 120.0, model: Optional[str] = None) -> str:
    c = get_config()
    if not c.llm_api_key:
        raise PermanentError("LLM_API_KEY is not configured")
    main = model or c.llm_model
    try:
        return _call_with_retry(main, messages, temperature, json_mode, max_tokens, timeout)
    except TransientLLMError as e:
        if c.llm_fallback_model and c.llm_fallback_model != main:
            log.warning("model '%s' keeps failing (%s); using fallback '%s'", main, e, c.llm_fallback_model)
            return _call_with_retry(c.llm_fallback_model, messages, temperature, json_mode, max_tokens, timeout)
        raise


# --------------------------------------------------------------------------- JSON helpers
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def extract_json(text: str) -> Any:
    """Parse JSON out of an LLM answer. Raises ValueError if nothing parseable is found."""
    t = _FENCE.sub("", (text or "").strip()).strip()
    candidates = [t]
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = t.find(open_c), t.rfind(close_c)
        if a != -1 and b > a:
            candidates.append(t[a: b + 1])
    for cand in candidates:
        for attempt in (cand, _TRAILING_COMMA.sub(r"\1", cand)):
            try:
                return json.loads(attempt)
            except Exception:
                continue
    raise ValueError("no valid JSON in LLM answer")


def chat_json(messages: list[dict], *, temperature: float = 0.8, max_tokens: Optional[int] = None,
              timeout: float = 120.0, parse_attempts: int = 2) -> Any:
    """Chat and return parsed JSON; re-asks once if the answer wasn't valid JSON."""
    msgs = list(messages)
    last: Optional[Exception] = None
    for _ in range(parse_attempts):
        text = chat(msgs, temperature=temperature, json_mode=True, max_tokens=max_tokens, timeout=timeout)
        try:
            return extract_json(text)
        except ValueError as e:
            last = e
            msgs = msgs + [
                {"role": "assistant", "content": text[:2000]},
                {"role": "user", "content": "That was not valid JSON. Reply again with ONLY the JSON object, no prose."},
            ]
            temperature = max(0.2, temperature - 0.3)
    raise TransientLLMError(f"LLM did not return valid JSON: {last}")


def ping() -> tuple[bool, str]:
    """Cheap live check used by preflight."""
    try:
        out = chat([{"role": "user", "content": "Reply with the single word: ok"}], temperature=0, max_tokens=8,
                   timeout=30)
        return True, f"model '{get_config().llm_model}' answered ({out.strip()[:20]})"
    except PermanentError as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
