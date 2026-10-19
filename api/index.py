"""Vercel entry point: every request is rewritten here (see vercel.json).

The container is built when the module loads instead of in the ASGI lifespan, so the
app works whether or not the platform runs lifespan events. Settings come from the
project's environment variables; see DEPLOYMENT.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.container import build_container
from app.main import create_app

# vercel.json rewrites /<anything> to /api/index?__path=/<anything>; the function sees
# the rewritten path, so the visitor's original path is restored from that parameter.
PATH_PARAM = "__path"


def restore_original_path(inner: Any) -> Any:
    async def asgi(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            query = parse_qsl(
                scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True
            )
            original = next((value for key, value in query if key == PATH_PARAM), None)
            if original is not None:
                rest = urlencode([(k, v) for k, v in query if k != PATH_PARAM])
                path = "/" + original.lstrip("/")
                scope = {
                    **scope,
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": rest.encode(),
                }
        await inner(scope, receive, send)

    return asgi


settings = get_settings()
app = restore_original_path(create_app(settings, build_container(settings)))
