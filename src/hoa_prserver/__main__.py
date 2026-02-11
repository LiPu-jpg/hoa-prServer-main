"""CLI entrypoint.

Runs Uvicorn to serve the FastAPI app.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("hoa_prserver.app:app", host=host, port=port)
