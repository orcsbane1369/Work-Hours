"""Production launcher (Waitress). Run behind nginx, never directly on the internet.
    python serve.py
Settings come from environment variables; see DEPLOY.md."""
import os

from waitress import serve

from app import app

if __name__ == "__main__":
    host = os.environ.get("WH_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    # One process on purpose: the login lockout counter lives in memory. Threads handle concurrency.
    serve(app, host=host, port=port, threads=int(os.environ.get("WH_THREADS", "4")))
