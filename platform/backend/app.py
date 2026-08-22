# platform/backend/app.py
#
# Single process, single `mcp` object, two ASGI apps mounted side by side:
#   /mcp/*  -> the existing FastMCP streamable-http app (agents connect here,
#              exactly as server.py already runs it)
#   /api/*  -> the admin/user REST API (admin_api.py) that the Next.js
#              platform calls
#
# This is what makes "tool add/remove from the admin panel actually reaches
# the live MCP server" true: admin_api.py's register_tool/deregister_tool
# call tools_admin.register_tool(mcp, ...) on the SAME `mcp` object this
# file imports from server.py -- there is only one FastMCP instance in this
# process, not a second copy the admin panel edits in isolation.
#
# Run with:
#   uvicorn app:app --host 0.0.0.0 --port 8000
#
# VERIFY BEFORE RELYING ON THIS: `mcp.server.fastmcp.FastMCP` needs to
# expose an ASGI app for its streamable-http transport (commonly
# `mcp.streamable_http_app()` in recent SDK versions). Run:
#   python -c "from mcp.server.fastmcp import FastMCP; print([m for m in dir(FastMCP) if 'app' in m.lower()])"
# and send me the output -- same ask as the remove_tool check, this file
# uses the most common method name for mcp==1.29.0 but I want it confirmed
# against your actual installed version rather than assumed.

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for p in (_REPO_ROOT, _REPO_ROOT / "mcp_server"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from server import mcp  # the SAME FastMCP instance server.py builds -- this
                         # import executes server.py's module-level
                         # mcp.tool()(...) registrations, but NOT mcp.run()
                         # (guarded by `if __name__ == "__main__":` in server.py)
from admin_api import router as admin_router

app = FastAPI(title="Blue Horizon Platform Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # the Next.js dev server; tighten for prod
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)

# Mount the MCP server's own ASGI app under /mcp so agents connect to
# http://<host>:8000/mcp exactly like they would talk to server.py's
# streamable-http transport directly.
try:
    mcp_asgi_app = mcp.streamable_http_app()
    app.router.routes.append(Mount("/mcp", app=mcp_asgi_app))
except AttributeError:
    # Fallback name seen in some SDK versions -- flagged loudly rather than
    # silently skipped, since without this mount agents have nothing to
    # connect to in this combined process.
    try:
        mcp_asgi_app = mcp.sse_app()
        app.router.routes.append(Mount("/mcp", app=mcp_asgi_app))
    except AttributeError as exc:  # pragma: no cover
        raise RuntimeError(
            "Could not find an ASGI app method on this FastMCP instance "
            "(tried streamable_http_app() and sse_app()). Run the version "
            "check in this file's docstring and tell me the real method name."
        ) from exc
