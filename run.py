"""Entry point for running the portal as a Windows service (or anywhere).

Behind IIS, IIS itself cannot host an ASGI app -- run this as a Windows
service (e.g. via NSSM) and let IIS reverse-proxy to it. See
deploy/windows-iis.md.
"""
import os

import uvicorn

from app.main import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("PORTAL_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8590")),
    )
