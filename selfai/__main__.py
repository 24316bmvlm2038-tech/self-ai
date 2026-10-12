"""Entry point: `python -m selfai`."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .config import Settings
from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-AI: autonomous DeepSeek agent with a chat UI")
    parser.add_argument("--host", help="Interface to listen on (default from SELFAI_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port to listen on (default from SELFAI_PORT or 8000)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port

    log = logging.getLogger("selfai")
    if not settings.api_key_configured:
        log.warning("DEEPSEEK_API_KEY is not set - copy .env.example to .env and add your key.")
    if settings.host not in ("127.0.0.1", "localhost", "::1") and not settings.password:
        log.warning("Listening on %s without SELFAI_PASSWORD: anyone on your network can use the AI.", settings.host)
    log.info("Model: %s via %s", settings.model, settings.base_url)
    log.info("Workspace: %s", settings.workspace_dir.resolve())
    log.info(
        "Open http://%s:%d in your browser", "localhost" if settings.host == "0.0.0.0" else settings.host, settings.port
    )

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
