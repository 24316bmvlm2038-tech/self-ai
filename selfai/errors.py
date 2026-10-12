"""Turn API/network exceptions into messages a user can act on."""

from __future__ import annotations

import openai


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, openai.AuthenticationError):
        return "DeepSeek rejected the API key. Check DEEPSEEK_API_KEY in your .env file."
    if isinstance(exc, openai.PermissionDeniedError):
        return f"DeepSeek denied the request: {exc.message}"
    if isinstance(exc, openai.RateLimitError):
        return "DeepSeek rate limit reached. Wait a moment and try again."
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code == 402:
            return "Your DeepSeek account has insufficient balance. Top up at platform.deepseek.com."
        return f"DeepSeek API error {exc.status_code}: {exc.message}"
    if isinstance(exc, openai.APITimeoutError):
        return "The request to DeepSeek timed out."
    if isinstance(exc, openai.APIConnectionError):
        return "Could not connect to the DeepSeek API. Check your internet connection and DEEPSEEK_BASE_URL."
    return f"{type(exc).__name__}: {exc}"
