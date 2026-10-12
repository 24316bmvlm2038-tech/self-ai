"""Client for a local Ollama server (https://ollama.com), which downloads and runs free models
on this device - including Android phones via Termux."""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .errors import LLMError


def normalize_model_name(name: str) -> str:
    """Ollama lists `llama3.2` as `llama3.2:latest`."""
    name = name.strip()
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


class OllamaClient:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._capabilities: dict[str, set[str]] = {}

    def _client(self, timeout: httpx.Timeout | float) -> httpx.AsyncClient:
        # trust_env=False: never send local traffic through an HTTP proxy from the environment.
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self._transport, trust_env=False)

    def _unreachable(self) -> LLMError:
        return LLMError(
            f"Can't reach Ollama at {self.base_url}. Start it with `ollama serve` "
            "(start.sh does this for you), or install it from https://ollama.com/download."
        )

    async def _request(self, method: str, path: str, timeout: float = 15.0, **kwargs) -> dict:
        try:
            async with self._client(timeout) as client:
                resp = await client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise self._unreachable() from exc
        if resp.status_code >= 400:
            raise LLMError(f"Ollama error: {_error_text(resp.text)}", resp.status_code)
        return resp.json() if resp.content else {}

    async def list_models(self) -> list[dict]:
        data = await self._request("GET", "/api/tags", timeout=5.0)
        models = []
        for m in data.get("models") or []:
            details = m.get("details") or {}
            models.append(
                {
                    "name": m.get("name") or m.get("model"),
                    "size": m.get("size") or 0,
                    "parameter_size": details.get("parameter_size"),
                    "quantization": details.get("quantization_level"),
                    "family": details.get("family"),
                }
            )
        return models

    async def capabilities(self, model: str) -> set[str] | None:
        """What the model supports ("tools", "thinking", "vision"...), or None if unknown."""
        model = normalize_model_name(model)
        if model in self._capabilities:
            return self._capabilities[model]
        try:
            data = await self._request("POST", "/api/show", json={"model": model, "name": model})
        except LLMError:
            return None
        caps = data.get("capabilities")
        if not isinstance(caps, list):
            return None  # older Ollama versions don't report capabilities
        self._capabilities[model] = set(caps)
        return self._capabilities[model]

    def forget(self, model: str) -> None:
        self._capabilities.pop(normalize_model_name(model), None)

    async def delete(self, model: str) -> None:
        self.forget(model)
        await self._request("DELETE", "/api/delete", json={"model": model, "name": model})

    async def _stream(self, path: str, body: dict) -> AsyncIterator[dict]:
        # Phones can take minutes to process a long prompt before the first token arrives.
        timeout = httpx.Timeout(900.0, connect=10.0)
        try:
            async with self._client(timeout) as client:
                async with client.stream("POST", path, json=body) as resp:
                    if resp.status_code >= 400:
                        text = _error_text((await resp.aread()).decode("utf-8", "replace"))
                        if resp.status_code == 404 and "model" in body:
                            raise LLMError(
                                f"The model '{body['model']}' isn't downloaded yet. Open Models to download it.", 404
                            )
                        raise LLMError(f"Ollama error: {text}", resp.status_code)
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            raise LLMError(f"Ollama error: {chunk['error']}")
                        yield chunk
        except httpx.TransportError as exc:
            raise self._unreachable() from exc

    def chat(self, body: dict) -> AsyncIterator[dict]:
        return self._stream("/api/chat", body)

    def pull(self, model: str) -> AsyncIterator[dict]:
        return self._stream("/api/pull", {"model": model, "name": model, "stream": True})


def _error_text(text: str) -> str:
    try:
        return str(json.loads(text).get("error") or text)
    except (ValueError, AttributeError):
        return text[:500]
