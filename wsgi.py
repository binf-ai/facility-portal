"""WSGI entry point for native IIS hosting (FastCGI via wfastcgi, or ISAPI via isapi_wsgi).

FastAPI is ASGI; IIS's native Python handlers speak WSGI, so we bridge with
a2wsgi. The app uses no background tasks and no websockets, so the bridge is
safe. Set DATA_DIR to an absolute path the IIS worker process can write to.
"""
import os

# IIS worker processes run with an unpredictable cwd, so pin DATA_DIR to the
# repo's data/ folder unless it is set explicitly.
os.environ.setdefault("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))

from a2wsgi import ASGIMiddleware  # noqa: E402

from app.main import app  # noqa: E402

application = ASGIMiddleware(app)
