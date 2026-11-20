"""Hand a question the modules could not answer to a language model (standard library only).

Each adapter returns an escalate(text, history) callable for Assistant(escalate=...):
* anthropic(): the Anthropic Messages API, or a proxy that speaks it (KASKAD_ANTHROPIC_URL);
* openai_compatible(): any /v1/chat/completions server: llama.cpp server, Ollama, LM Studio, vLLM.

Before anything leaves the machine, redact() masks e-mail addresses, IBANs and phone numbers in the
question and in the history sent with it. It does not find names or street addresses: pseudonymise
those before a question reaches the assistant, or pass redactor=None to send the text unchanged.
"""
import os
import re

from .net import post_json

SYSTEM = ("You are the last level of a small assistant: its rule-based levels could not answer. "
          "Answer briefly and in the language of the question. If you are not sure, say so.")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")
_PHONE = re.compile(r"(?<![\w+])\+?\d[\d /()-]{5,}\d(?!\w)")


def _phone(match):
    """A run of digits counts as a phone number when it starts with + or 0 and has 7 to 15 digits,
    so postcodes, dates, amounts and case numbers are left alone."""
    value = match.group(0)
    digits = sum(char.isdigit() for char in value)
    return "[phone]" if value[0] in "+0" and 7 <= digits <= 15 else value


def redact(text):
    """Mask e-mail addresses, IBANs and phone numbers. Dates, amounts and § numbers stay."""
    text = _EMAIL.sub("[email]", text)
    text = _IBAN.sub("[iban]", text)
    return _PHONE.sub(_phone, text)


def _messages(text, history, turns, redactor):
    clean = redactor or (lambda value: value)
    messages = []
    for turn in history[-turns:] if turns else []:
        user, bot = turn.get("user"), turn.get("bot")
        if isinstance(user, str) and isinstance(bot, str) and user and bot:
            messages.append({"role": "user", "content": clean(user)})
            messages.append({"role": "assistant", "content": clean(bot)})
    messages.append({"role": "user", "content": clean(text)})
    return messages


def anthropic_request(text, history, model, api_key, base_url, max_tokens=400, system=SYSTEM,
                      history_turns=4, redactor=redact):
    """(url, headers, body) for one Messages API call."""
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if api_key:
        headers["x-api-key"] = api_key
    body = {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": _messages(text, history, history_turns, redactor)}
    return base_url.rstrip("/") + "/v1/messages", headers, body


def anthropic(model=None, api_key=None, base_url=None, timeout=20, **options):
    """Escalate to Claude. Reads ANTHROPIC_API_KEY, KASKAD_LLM_MODEL and KASKAD_ANTHROPIC_URL."""
    model = model or os.environ.get("KASKAD_LLM_MODEL", "claude-haiku-5-5")
    api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = base_url or os.environ.get("KASKAD_ANTHROPIC_URL", "https://api.anthropic.com")

    def escalate(text, history):
        url, headers, body = anthropic_request(text, history, model, api_key, base_url, **options)
        data = post_json(url, headers, body, timeout) or {}
        parts = [block.get("text", "") for block in data.get("content") or [] if block.get("type") == "text"]
        return "".join(parts).strip() or None

    return escalate


def openai_request(text, history, model, api_key, base_url, max_tokens=400, system=SYSTEM,
                   history_turns=4, redactor=redact):
    """(url, headers, body) for one /chat/completions call."""
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = "Bearer " + api_key
    messages = [{"role": "system", "content": system}] + _messages(text, history, history_turns, redactor)
    return base_url.rstrip("/") + "/chat/completions", headers, {"model": model, "max_tokens": max_tokens,
                                                                 "messages": messages}


def openai_compatible(base_url=None, model=None, api_key=None, timeout=60, **options):
    """Escalate to a local or hosted OpenAI-compatible server.

    Reads KASKAD_LLM_URL (default http://127.0.0.1:8080/v1, the llama.cpp server), KASKAD_LLM_MODEL
    and KASKAD_LLM_KEY. Ollama listens on http://127.0.0.1:11434/v1.
    """
    base_url = base_url or os.environ.get("KASKAD_LLM_URL", "http://127.0.0.1:8080/v1")
    model = model or os.environ.get("KASKAD_LLM_MODEL", "local")
    api_key = api_key if api_key is not None else os.environ.get("KASKAD_LLM_KEY", "")

    def escalate(text, history):
        url, headers, body = openai_request(text, history, model, api_key, base_url, **options)
        data = post_json(url, headers, body, timeout) or {}
        choices = data.get("choices") or []
        if not choices:
            return None
        return ((choices[0].get("message") or {}).get("content") or "").strip() or None

    return escalate
