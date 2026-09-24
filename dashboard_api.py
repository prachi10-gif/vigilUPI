"""Compatibility import for the local dashboard API.

The dashboard is served by the same FastAPI application as the trained
model so its frontend always posts to the real /score endpoint.
Prefer `uvicorn realtime_scorer:app --host 127.0.0.1 --port 8000`.
"""

from realtime_scorer import app

__all__ = ["app"]
