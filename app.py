"""Entry point for the always-on web version on Vercel (see README, "Always-on website").

Vercel runs this ASGI app as a serverless function. Settings come from the project's
environment variables (SELFAI_SUPABASE_URL, SELFAI_SUPABASE_KEY, SELFAI_DB_SECRET,
SELFAI_CRON_SECRET, SELFAI_PASSWORD). To run Self-AI on your own device, use start.sh instead.
"""

import logging

from selfai.server import create_app

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

app = create_app()
