"""Errors from the model backends, and turning any exception into a message a user can act on."""

from __future__ import annotations

import httpx


class LLMError(Exception):
    """A model backend refused or failed a request. The message is meant for the user."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def api_error_message(status: int, detail: str, name: str = "DeepSeek") -> str:
    """Explain an error response from a cloud AI API (DeepSeek, Groq, Gemini...)."""
    if status in (401, 403):
        return f"{name} rejected the API key. Check the key in Models."
    if status == 402:
        return f"Your {name} account has insufficient balance. Top it up, or switch to a free AI in Models."
    if status in (413, 429):
        return (
            f"{name}'s free limit was reached (free plans allow a limited amount per minute and per day). "
            "Wait a minute, or switch to another AI in Models."
        )
    return f"{name} error {status}: {detail}"


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, LLMError):
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return "The AI model took too long to respond."
    if isinstance(exc, httpx.HTTPError):
        return f"Could not reach the AI model: {type(exc).__name__}: {exc}"
    return f"{type(exc).__name__}: {exc}"
