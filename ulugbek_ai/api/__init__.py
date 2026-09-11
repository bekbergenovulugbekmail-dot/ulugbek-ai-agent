"""HTTP API.

Routes are thin: they validate input, call a manager or the engine, and shape
the response. All behaviour lives in the domain modules, so the same logic is
reachable from a future Telegram, CLI or webhook entrypoint without going
through HTTP.
"""

from ulugbek_ai.api.router import api_router

__all__ = ["api_router"]
