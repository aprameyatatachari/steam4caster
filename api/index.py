"""Vercel entry point: every request is rewritten here (see vercel.json).

The container is built when the module loads instead of in the ASGI lifespan, so the
app works whether or not the platform runs lifespan events. Settings come from the
project's environment variables; see DEPLOYMENT.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.core.container import build_container  # noqa: E402
from app.main import create_app  # noqa: E402

settings = get_settings()
app = create_app(settings, build_container(settings))
