"""Errors from the model backends, and turning any exception into a message a user can act on."""

from __future__ import annotations

import httpx


class LLMError(Exception):
    """A model backend refused or failed a request. The message is meant for the user."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def api_error_message(status: int, detail: str) -> str:
    """Explain an error response from the DeepSeek (OpenAI-compatible) API."""
    if status == 401:
        return "DeepSeek rejected the API key. Check DEEPSEEK_API_KEY in your .env file."
    if status == 402:
        return "Your DeepSeek account has insufficient balance. Top up at platform.deepseek.com, or switch to a free local model."
    if status == 429:
        return "The AI service's rate limit was reached. Wait a moment and try again."
    return f"AI API error {status}: {detail}"


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, LLMError):
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return "The AI model took too long to respond."
    if isinstance(exc, httpx.HTTPError):
        return f"Could not reach the AI model: {type(exc).__name__}: {exc}"
    return f"{type(exc).__name__}: {exc}"
