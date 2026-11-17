"""Bill Collect — POST /api/payments (and /api/payments/<id>/void|confirm).

One payment = one row. Idempotency is on `source_id` (e.g. a Gmail
message id, a Plaid transaction id, or an app-generated key):
  * same source_id + same amount/member/month -> the existing
    payment is returned, nothing is counted twice (200, duplicate).
  * same source_id + different amount from an agent source
    (gmail/plaid/zelle-email) -> 409 conflict; a retried email
    must never silently change an amount.
  * same source_id + different amount from the app (Sunny editing
    a manual entry in the UI) -> the payment is corrected in
    place, with an audit row (200, updated).

A payment is 'confirmed' by default. Pass status='pending' when
Muse matched the sender by name only or the amount does not match
the due — pending payments are recorded and shown with a draft
badge, but do NOT count toward collected totals until confirmed
(POST .../confirm). A partial payment (amount < due) is confirmed
but the member stays unpaid until the confirmed total reaches due.

Auth: agent key (X-Agent-Key, for the Muse cron) or user Bearer
token (Sunny in the browser; see _lib.py user_present TODO).
"""

try:
    from _lib import (
        ApiError, JsonHandler, get_store, member_derived,
        new_id, parse_body, require_auth, validate_payment,
    )
except ImportError:  # pragma: no cover - Vercel import fallback
    from api._lib import (
        ApiError, JsonHandler, get_store, member_derived,
        new_id, parse_body, require_auth, validate_payment,
    )


def handle_create_payment(store, headers, body_bytes):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    data = parse_body(body_bytes)
    cleaned, reason = validate_payment(data, store)
    if reason:
        return 400, {"ok": False, "error": "Invalid payment: " + reason}

    status = data.get("status", "confirmed")
    if status not in ("pending", "confirmed"):
        return 400, {"ok": False, "error": "status must be pending or confirmed"}

    existing = store.get_payment_by_source(cleaned["source_id"])
    if existing is not None:
        same = (
            float(existing["amount"]) == cleaned["amount"]
            and existing["member_id"] == cleaned["member_id"]
            and existing["month_id"] == cleaned["month_id"]
            and (existing.get("method") or "") == cleaned["method"]
            and (existing.get("date_paid") or "") == (cleaned["date_paid"] or "")
        )
        if same:
            return 200, {"ok": True, "payment": existing, "duplicate": True}
        if cleaned["source"] != "app":
            return 409, {
                "ok": False,
                "error": "conflict: source_id already used with a different amount",
                "payment": existing,
            }
        # Sunny correcting a manual entry in the app: update in place.
        updated = store.update_payment(existing["id"], {
            "amount": cleaned["amount"],
            "date_paid": cleaned["date_paid"],
            "method": cleaned["method"],
            "status": status,
        })
        store.audit(actor, "payment.updated", "payment", existing["id"], {
            "amount": cleaned["amount"],
            "method": cleaned["method"],
            "source_id": cleaned["source_id"],
        })
        return 200, {"ok": True, "payment": updated, "updated": True}

    payment = {
        "id": new_id(),
        "member_id": cleaned["member_id"],
        "month_id": cleaned["month_id"],
        "amount": cleaned["amount"],
        "date_paid": cleaned["date_paid"],
        "method": cleaned["method"],
        "source": cleaned["source"],
        "source_id": cleaned["source_id"],
        "status": status,
        "created_by": actor,
    }
    created = store.create_payment(payment)
    store.audit(actor, "payment.created", "payment", created["id"], {
        "amount": cleaned["amount"],
        "method": cleaned["method"],
        "source": cleaned["source"],
        "source_id": cleaned["source_id"],
        "status": status,
    })
    line = store.get_bill_line(cleaned["month_id"], cleaned["member_id"])
    derived = member_derived(
        store, cleaned["month_id"], cleaned["member_id"],
        float(line["amount"]) if line else 0,
    )
    return 201, {
        "ok": True,
        "payment": created,
        "member_paid": derived["paid"],
        "member_received": derived["received"],
    }


def handle_void_payment(store, headers, payment_id):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    payment = store.get_payment(payment_id)
    if payment is None:
        return 404, {"ok": False, "error": "payment not found"}
    if payment.get("status") == "void":
        return 200, {"ok": True, "payment": payment, "duplicate": True}
    updated = store.update_payment(payment_id, {"status": "void"})
    store.audit(actor, "payment.voided", "payment", payment_id, {
        "amount": payment["amount"],
        "reason": "undone by " + actor,
    })
    return 200, {"ok": True, "payment": updated}


def handle_confirm_payment(store, headers, payment_id):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    payment = store.get_payment(payment_id)
    if payment is None:
        return 404, {"ok": False, "error": "payment not found"}
    if payment.get("status") == "void":
        return 409, {"ok": False, "error": "cannot confirm a void payment"}
    if payment.get("status") == "confirmed":
        return 200, {"ok": True, "payment": payment, "duplicate": True}
    updated = store.update_payment(payment_id, {"status": "confirmed"})
    store.audit(actor, "payment.confirmed", "payment", payment_id, {
        "amount": payment["amount"],
    })
    return 200, {"ok": True, "payment": updated}


class handler(JsonHandler):
    def do_POST(self):
        try:
            store = get_store()
            body = self._read_body()
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            # parts: ['api', 'payments'] or ['api', 'payments', '<id>', 'void'|'confirm']
            if len(parts) == 4 and parts[3] in ("void", "confirm"):
                fn = handle_void_payment if parts[3] == "void" else handle_confirm_payment
                status, payload = fn(store, self._headers_dict(), parts[2])
            elif len(parts) == 2:
                status, payload = handle_create_payment(
                    store, self._headers_dict(), body
                )
            else:
                status, payload = 404, {"ok": False, "error": "not found"}
            self._json(status, payload)
        except ApiError as exc:
            self._json(exc.status, exc.payload)
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace
            self._json(500, {"ok": False, "error": str(exc)[:200]})
