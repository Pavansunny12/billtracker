"""Bill Collect — GET /api/config.

Returns the publishable Supabase config the browser needs to sign
in: SUPABASE_URL and SUPABASE_ANON_KEY from Vercel env vars.

The anon (publishable) key is safe to send to the browser by
design — it only identifies the project, and data access is gated
by Row Level Security plus the server-side JWT check in api/_lib.py.
The service_role key is NEVER returned here (or anywhere else to
a browser). When the env vars are not set, the app reports
"Sign-in not configured" and keeps working from its offline cache.
"""

import os

try:
    from _lib import JsonHandler
except ImportError:  # pragma: no cover - Vercel import fallback
    from api._lib import JsonHandler


def handle_config():
    """Return (status, payload) for GET /api/config. No store needed."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not anon_key:
        return 200, {
            "ok": True,
            "configured": False,
            "supabase_url": "",
            "anon_key": "",
        }
    return 200, {
        "ok": True,
        "configured": True,
        "supabase_url": url.rstrip("/"),
        "anon_key": anon_key,
    }


class handler(JsonHandler):
    def do_GET(self):
        status, payload = handle_config()
        self._json(status, payload)
