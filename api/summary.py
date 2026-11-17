"""Bill Collect — GET /api/summary.

Collected vs bill total vs still owed for one month (default: the
latest month). Read-only. Used by the app hero card and by the
Muse cron when it reports "who still owes".

Auth: agent key (X-Agent-Key, Muse cron) or user Bearer token
(Sunny in the browser; verified as a Supabase JWT in _lib.py).
"""

try:
    from _lib import ApiError, JsonHandler, get_store, month_view, require_auth
except ImportError:  # pragma: no cover - Vercel import fallback
    from api._lib import ApiError, JsonHandler, get_store, month_view, require_auth
import urllib.parse


def handle_summary(store, headers, query):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    month_id = query.get("month_id", [None])[0]
    months = sorted(store.list_months(), key=lambda m: m.get("created_at") or "")
    if not months:
        return 200, {"ok": True, "summary": None, "message": "No months yet."}
    month = None
    if month_id:
        month = store.get_month(month_id)
        if month is None:
            return 404, {"ok": False, "error": "month not found"}
    else:
        # Latest active month, else latest month of any status.
        active = [m for m in months if m.get("status") == "active"]
        month = active[-1] if active else months[-1]
    view = month_view(store, month)
    return 200, {
        "ok": True,
        "summary": {
            "month_id": view["id"],
            "label": view["label"],
            "status": view["status"],
            "bill_total": view["bill_total"],
            "due_date": view["due_date"],
            "collectible": view["totals"]["collectible"],
            "owner_paid": view["totals"]["ownerPaid"],
            "collected": view["totals"]["collected"],
            "remaining": view["totals"]["remaining"],
            "paid_count": view["totals"]["paid"],
            "pending_count": view["totals"]["pending"],
            "percent": view["totals"]["percent"],
            "who_owes": view["whoOwes"],
            "last_updated_by": next(
                (m.get("lastUpdatedBy") for m in view["members"] if m.get("lastUpdatedBy")),
                "",
            ),
        },
    }


class handler(JsonHandler):
    def do_GET(self):
        try:
            store = get_store()
            query = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query
            )
            status, payload = handle_summary(store, self._headers_dict(), query)
            self._json(status, payload)
        except ApiError as exc:
            self._json(exc.status, exc.payload)
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace
            self._json(500, {"ok": False, "error": str(exc)[:200]})
