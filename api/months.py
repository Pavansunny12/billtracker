"""Bill Collect — /api/months.

GET  /api/months            list months (light: label, totals, status)
GET  /api/months/<id>       one month with members, payments, totals,
                            and who still owes (see _lib.month_view)
POST /api/months            create a month from an approved bill-ingest
                            draft: {label, bill_total, due_date,
                            status, lines:[{member_id, phone, amount,
                            confidence, source_text, excluded}]}
                            Unknown phones are rejected — add them as
                            members first (bill_ingest reports them).
POST /api/months/<id>       rename / change due_date / status
                            {label?, due_date?, status?}
POST /api/months/<id>/lines update one member's line in a month
                            {member_id, due?, notes?, reminder_sent?,
                            excluded?} — this is how the app saves
                            Due / Notes / Reminder edits.

Auth: agent key (X-Agent-Key, Muse cron) or user Bearer token
(Sunny in the browser; see _lib.py user_present TODO).
"""

try:
    from _lib import (
        ApiError, JsonHandler, get_store, last10, month_view,
        new_id, parse_body, require_auth, valid_date,
    )
except ImportError:  # pragma: no cover - Vercel import fallback
    from api._lib import (
        ApiError, JsonHandler, get_store, last10, month_view,
        new_id, parse_body, require_auth, valid_date,
    )


def handle_list_months(store, headers):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    months = []
    for month in sorted(store.list_months(), key=lambda m: m.get("created_at") or ""):
        view = month_view(store, month)
        months.append({
            "id": view["id"],
            "label": view["label"],
            "bill_total": view["bill_total"],
            "due_date": view["due_date"],
            "status": view["status"],
            "totals": view["totals"],
        })
    return 200, {"ok": True, "months": months}


def handle_get_month(store, headers, month_id):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    month = store.get_month(month_id)
    if month is None:
        return 404, {"ok": False, "error": "month not found"}
    return 200, {"ok": True, "month": month_view(store, month)}


def handle_create_month(store, headers, body_bytes):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    data = parse_body(body_bytes)
    label = (data.get("label") or "").strip()
    if not label:
        return 400, {"ok": False, "error": "label is required"}
    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        return 400, {"ok": False, "error": "lines must be a non-empty array"}
    if len(lines) > 500:
        return 400, {"ok": False, "error": "too many lines"}
    status = data.get("status", "draft")
    if status not in ("draft", "active", "closed"):
        return 400, {"ok": False, "error": "status must be draft, active or closed"}
    due_date = data.get("due_date")
    if due_date and not valid_date(due_date):
        return 400, {"ok": False, "error": "due_date must be YYYY-MM-DD"}
    cleaned_lines = []
    for line in lines:
        if not isinstance(line, dict):
            return 400, {"ok": False, "error": "each line must be an object"}
        member_id = line.get("member_id")
        if not isinstance(member_id, str) or store.get_member(member_id) is None:
            return 400, {
                "ok": False,
                "error": "unknown member_id in lines: %s" % (member_id,),
            }
        try:
            amount = float(line.get("amount"))
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "line amount must be a number"}
        if amount < 0 or amount > 1000:
            return 400, {"ok": False, "error": "line amount out of range"}
        cleaned_lines.append({
            "member_id": member_id,
            "phone": line.get("phone") or "",
            "amount": round(amount, 2),
            "confidence": int(line.get("confidence") or 0),
            "source_text": line.get("source_text") or "",
            "excluded": bool(line.get("excluded")),
        })
    bill_total = data.get("bill_total")
    if bill_total is None:
        bill_total = round(sum(line["amount"] for line in cleaned_lines), 2)
    month = {
        "id": data.get("id") or new_id(),
        "label": label,
        "bill_total": round(float(bill_total), 2),
        "due_date": due_date,
        "status": status,
    }
    if store.get_month(month["id"]) is not None:
        return 409, {"ok": False, "error": "month already exists"}
    created = store.create_month(month)
    for line in cleaned_lines:
        store.upsert_bill_line({
            "id": new_id(),
            "month_id": created["id"],
            "member_id": line["member_id"],
            "phone": line["phone"],
            "amount": line["amount"],
            "confidence": line["confidence"],
            "source_text": line["source_text"],
            "notes": "",
            "reminder_sent": False,
            "excluded": line["excluded"],
        })
    store.audit(actor, "month.created", "month", created["id"], {
        "label": label, "status": status, "lines": len(cleaned_lines),
    })
    return 201, {"ok": True, "month": month_view(store, created)}


def handle_update_month(store, headers, month_id, body_bytes):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    month = store.get_month(month_id)
    if month is None:
        return 404, {"ok": False, "error": "month not found"}
    data = parse_body(body_bytes)
    fields = {}
    if "label" in data:
        label = str(data["label"]).strip()
        if not label:
            return 400, {"ok": False, "error": "label must not be empty"}
        fields["label"] = label
    if "due_date" in data:
        if data["due_date"] and not valid_date(data["due_date"]):
            return 400, {"ok": False, "error": "due_date must be YYYY-MM-DD"}
        fields["due_date"] = data["due_date"]
    if "status" in data:
        if data["status"] not in ("draft", "active", "closed"):
            return 400, {"ok": False, "error": "status must be draft, active or closed"}
        fields["status"] = data["status"]
    if not fields:
        return 400, {"ok": False, "error": "nothing to update"}
    updated = store.update_month(month_id, fields)
    store.audit(actor, "month.updated", "month", month_id, fields)
    return 200, {"ok": True, "month": month_view(store, updated)}


def handle_update_line(store, headers, month_id, body_bytes):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    if store.get_month(month_id) is None:
        return 404, {"ok": False, "error": "month not found"}
    data = parse_body(body_bytes)
    member_id = data.get("member_id")
    if not isinstance(member_id, str) or not member_id:
        return 400, {"ok": False, "error": "member_id is required"}
    if data.get("remove") is True:
        line = store.get_bill_line(month_id, member_id)
        if line is None:
            return 404, {"ok": False, "error": "no line for this member in this month"}
        for payment in store.list_payments(month_id=month_id, member_id=member_id):
            if payment.get("status") != "void":
                store.update_payment(payment["id"], {"status": "void"})
        store.delete_bill_line(month_id, member_id)
        store.audit(actor, "bill_line.removed", "bill_line", line["id"], {
            "member_id": member_id,
        })
        return 200, {"ok": True, "removed": True}
    line = store.get_bill_line(month_id, member_id)
    if line is None:
        # Upsert: adding a member to a month from the app creates
        # their line here (the member itself must already exist).
        if store.get_member(member_id) is None:
            return 404, {"ok": False, "error": "member not found"}
        member = store.get_member(member_id)
        line = {
            "id": new_id(),
            "month_id": month_id,
            "member_id": member_id,
            "phone": member.get("phone", ""),
            "amount": 0,
            "confidence": 0,
            "source_text": "",
            "notes": "",
            "reminder_sent": False,
            "excluded": False,
        }
    fields = {}
    if "due" in data:
        try:
            due = float(data["due"])
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "due must be a number"}
        if due < 0 or due > 1000:
            return 400, {"ok": False, "error": "due out of range"}
        fields["amount"] = round(due, 2)
    if "notes" in data:
        if not isinstance(data["notes"], str) or len(data["notes"]) > 80:
            return 400, {"ok": False, "error": "notes must be a string up to 80 chars"}
        fields["notes"] = data["notes"]
    if "reminder_sent" in data:
        if not isinstance(data["reminder_sent"], bool):
            return 400, {"ok": False, "error": "reminder_sent must be a boolean"}
        fields["reminder_sent"] = data["reminder_sent"]
    if "excluded" in data:
        if not isinstance(data["excluded"], bool):
            return 400, {"ok": False, "error": "excluded must be a boolean"}
        fields["excluded"] = data["excluded"]
    if not fields:
        return 400, {"ok": False, "error": "nothing to update"}
    updated = store.upsert_bill_line({**line, **fields})
    store.audit(actor, "bill_line.updated", "bill_line", line["id"], fields)
    return 200, {"ok": True, "line": updated}


class handler(JsonHandler):
    def do_GET(self):
        try:
            store = get_store()
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            if len(parts) == 2:
                status, payload = handle_list_months(store, self._headers_dict())
            elif len(parts) == 3:
                status, payload = handle_get_month(store, self._headers_dict(), parts[2])
            else:
                status, payload = 404, {"ok": False, "error": "not found"}
            self._json(status, payload)
        except ApiError as exc:
            self._json(exc.status, exc.payload)
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace
            self._json(500, {"ok": False, "error": str(exc)[:200]})

    def do_POST(self):
        try:
            store = get_store()
            body = self._read_body()
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            if len(parts) == 2:
                status, payload = handle_create_month(store, self._headers_dict(), body)
            elif len(parts) == 3:
                status, payload = handle_update_month(
                    store, self._headers_dict(), parts[2], body
                )
            elif len(parts) == 4 and parts[3] == "lines":
                status, payload = handle_update_line(
                    store, self._headers_dict(), parts[2], body
                )
            else:
                status, payload = 404, {"ok": False, "error": "not found"}
            self._json(status, payload)
        except ApiError as exc:
            self._json(exc.status, exc.payload)
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace
            self._json(500, {"ok": False, "error": str(exc)[:200]})
