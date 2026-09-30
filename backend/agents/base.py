"""Shared LLM helpers for swarm agents."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from langchain_google_genai import ChatGoogleGenerativeAI

from config import Settings, get_settings

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_PY_FENCE_RE = re.compile(r"```(?:python)?\s*([\s\S]*?)```", re.IGNORECASE)


def get_gemini_flash(
    *,
    settings: Optional[Settings] = None,
    temperature: float = 0.2,
) -> ChatGoogleGenerativeAI:
    """Construct a Gemini Flash chat model via langchain-google-genai."""
    cfg = settings or get_settings()
    if not cfg.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to backend/.env before running the swarm."
        )
    return ChatGoogleGenerativeAI(
        model=cfg.gemini_model or "gemini-2.5-flash",
        google_api_key=cfg.gemini_api_key,
        temperature=temperature,
        # Avoid multi-minute exponential backoff when a model id is invalid/unavailable.
        max_retries=1,
        timeout=60,
    )


def invoke_text(llm: ChatGoogleGenerativeAI, prompt: str) -> str:
    """Invoke the chat model and return plain text content."""
    response = llm.invoke(prompt)
    content = response.content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "\n".join(parts).strip()
    return str(content).strip()


def extract_json_object(text: str) -> Dict[str, Any]:
    """Parse the first JSON object from model output (raw or fenced)."""
    candidates: list[str] = []
    for match in _JSON_FENCE_RE.finditer(text):
        candidates.append(match.group(1).strip())
    candidates.append(text.strip())

    # Also try slicing from first '{' to last '}'.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])

    last_error: Optional[Exception] = None
    for raw in candidates:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError) as exc:
            last_error = exc
            continue
    raise ValueError(f"failed to parse JSON object from model output: {last_error}\n{text[:500]}")


def extract_python_code(text: str) -> str:
    """Extract executable Python from a model reply (fenced block preferred)."""
    match = _PY_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    # Fallback: strip accidental fences / prose if the model returned bare code.
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _PY_FENCE_RE.sub(r"\1", stripped).strip()
    return stripped
