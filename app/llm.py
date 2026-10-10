"""OpenAI client wrapper. The only file that talks to OpenAI.

Key: OPENAI_API_KEY env var, else apikey.txt in the project root (git-ignored).
Model: OPENAI_MODEL env var, else DEFAULT_MODEL.
Every failure (no key, network, timeout, bad JSON) becomes AIUnavailable, and
the caller falls back to rules. Error messages never include the key.
"""
import json
import os

from app import config

DEFAULT_MODEL = "gpt-5.4-mini"
TIMEOUT_SECONDS = 20


class AIUnavailable(Exception):
    pass


def load_api_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    path = config.ROOT / "apikey.txt"
    try:
        key = path.read_text().strip()
    except OSError:
        return None
    return key or None


def model_name() -> str:
    return os.environ.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL


def complete_json(system: str, user: str, schema_name: str, schema: dict) -> dict:
    """One chat call with a strict JSON schema. Returns the parsed object."""
    key = load_api_key()
    if not key:
        raise AIUnavailable("No OpenAI API key (set OPENAI_API_KEY or create apikey.txt).")
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, timeout=TIMEOUT_SECONDS, max_retries=0)
        response = client.chat.completions.create(
            model=model_name(),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
        )
        content = response.choices[0].message.content or ""
    except Exception as exc:  # network, auth, rate limit, timeout, unknown model...
        status = getattr(exc, "status_code", None)
        raise AIUnavailable(f"OpenAI call failed: {type(exc).__name__}" + (f" (HTTP {status})" if status else ""))
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise AIUnavailable("OpenAI returned invalid JSON.")
