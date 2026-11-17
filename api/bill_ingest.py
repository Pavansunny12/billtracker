"""Bill Collect — POST /api/bill_ingest (agent only).

Takes AT&T bill text (from the PDF extracted by /api/extract_pdf,
or pasted by Sunny) and returns a DRAFT month. It never writes
anything to the database and never auto-applies amounts.

Uses the same "Total for <phone> $amount" parsing as app.js
(extractAttBillTotals, ported to api/_lib.py parse_bill_totals),
matches each phone to a member, and reports:
  * lines: per-member {member_id, name, phone, oldAmount,
    newAmount, confidence, source}
  * missingMembers: current members with no bill line
  * unknownPhones: bill lines whose phone matches no member
    (new lines Sunny needs to add as members first)
  * parsedTotal / billLineCount for a sanity check against the
    AT&T bill total in the email.

The Muse cron calls this when a new bill lands, shows Sunny the
draft, and only creates the month (POST /api/months) after Sunny
approves it in chat.

Auth: agent key (X-Agent-Key) only. A browser session cannot call
this endpoint.
"""

try:
    from _lib import (
        ApiError, JsonHandler, get_store, last10, month_view,
        new_id, parse_bill_totals, parse_body, require_auth,
    )
except ImportError:  # pragma: no cover - Vercel import fallback
    from api._lib import (
        ApiError, JsonHandler, get_store, last10, month_view,
        new_id, parse_bill_totals, parse_body, require_auth,
    )


def handle_bill_ingest(store, headers, body_bytes):
    actor, err = require_auth(headers, agent_only=True)
    if err:
        return 401, err
    data = parse_body(body_bytes)
    text = data.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return 400, {"ok": False, "error": "text is required"}
    label = data.get("label") or ""
    if label and not isinstance(label, str):
        return 400, {"ok": False, "error": "label must be a string"}

    totals = parse_bill_totals(text)
    members = store.list_members()
    by_phone = {last10(m.get("phone")): m for m in members if last10(m.get("phone"))}

    # Old amount per member = their due in the latest month, if any.
    months = sorted(store.list_months(), key=lambda m: m.get("created_at") or "")
    old_amounts = {}
    if months:
        latest = month_view(store, months[-1])
        for entry in latest["members"]:
            old_amounts[entry["id"]] = entry["due"]

    lines = []
    unknown_phones = []
    matched_ids = set()
    for phone10, parsed in totals.items():
        member = by_phone.get(phone10)
        if member is None:
            unknown_phones.append({
                "phone": phone10,
                "amount": parsed["amount"],
                "name": parsed["name"],
                "source": parsed["source"],
            })
            continue
        matched_ids.add(member["id"])
        lines.append({
            "member_id": member["id"],
            "name": member["name"],
            "phone": member["phone"],
            "oldAmount": old_amounts.get(member["id"], 0),
            "newAmount": parsed["amount"],
            "confidence": parsed["confidence"],
            "source": parsed["source"],
        })

    missing = [
        {"member_id": m["id"], "name": m["name"], "phone": m.get("phone", "")}
        for m in members
        if m.get("active", True) and m["id"] not in matched_ids
    ]
    parsed_total = round(sum(p["amount"] for p in totals.values()), 2)
    draft = {
        "label": label,
        "bill_total": data.get("bill_total") or parsed_total,
        "due_date": data.get("due_date"),
        "status": "draft",
        "lines": lines,
        "missingMembers": missing,
        "unknownPhones": unknown_phones,
        "parsedTotal": parsed_total,
        "billLineCount": len(totals),
        "matchedCount": len(lines),
    }
    return 200, {"ok": True, "draft": draft, "created_by": actor}


class handler(JsonHandler):
    def do_POST(self):
        try:
            store = get_store()
            status, payload = handle_bill_ingest(
                store, self._headers_dict(), self._read_body()
            )
            self._json(status, payload)
        except ApiError as exc:
            self._json(exc.status, exc.payload)
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace
            self._json(500, {"ok": False, "error": str(exc)[:200]})
