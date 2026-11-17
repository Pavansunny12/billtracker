"""Bill Collect — /api/members.

POST /api/members          create a member, or reuse the existing
                           member with the same phone (last 10 digits).
                           {name, phone, att_name?}
POST /api/members/<id>     rename / change phone / att_name / active.
                           {name?, phone?, att_name?, active?}

A member is global (one row per person); their per-month due lives
in bill_lines. The app calls POST /api/members when Sunny adds a
person, then POST /api/months/<id>/lines to put them in a month.

Auth: agent key (X-Agent-Key, Muse cron) or user Bearer token
(Sunny in the browser; see _lib.py user_present TODO).
"""

try:
    from _lib import (
        ApiError, JsonHandler, get_store, last10,
        new_id, parse_body, require_auth,
    )
except ImportError:  # pragma: no cover - Vercel import fallback
    from api._lib import (
        ApiError, JsonHandler, get_store, last10,
        new_id, parse_body, require_auth,
    )


def handle_create_member(store, headers, body_bytes):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    data = parse_body(body_bytes)
    name = str(data.get("name") or "").strip()
    if not name:
        return 400, {"ok": False, "error": "name is required"}
    phone = str(data.get("phone") or "").strip()
    phone10 = last10(phone)
    if phone and phone10 and len(phone10) != 10:
        return 400, {"ok": False, "error": "phone must have 10 digits"}
    existing = store.get_member_by_phone(phone10) if phone10 else None
    if existing is not None:
        return 200, {"ok": True, "member": existing, "duplicate": True}
    member = {
        "id": new_id(),
        "name": name,
        "phone": phone,
        "att_name": str(data.get("att_name") or ""),
        "active": True,
    }
    created = store.upsert_member(member)
    store.audit(actor, "member.created", "member", created["id"], {"name": name})
    return 201, {"ok": True, "member": created}


def handle_update_member(store, headers, member_id, body_bytes):
    actor, err = require_auth(headers)
    if err:
        return 401, err
    member = store.get_member(member_id)
    if member is None:
        return 404, {"ok": False, "error": "member not found"}
    data = parse_body(body_bytes)
    fields = {}
    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            return 400, {"ok": False, "error": "name must not be empty"}
        fields["name"] = name
    if "phone" in data:
        phone = str(data["phone"] or "").strip()
        if phone and len(last10(phone)) != 10:
            return 400, {"ok": False, "error": "phone must have 10 digits"}
        fields["phone"] = phone
    if "att_name" in data:
        fields["att_name"] = str(data["att_name"] or "")
    if "active" in data:
        if not isinstance(data["active"], bool):
            return 400, {"ok": False, "error": "active must be a boolean"}
        fields["active"] = data["active"]
    if not fields:
        return 400, {"ok": False, "error": "nothing to update"}
    updated = store.upsert_member({**member, **fields})
    store.audit(actor, "member.updated", "member", member_id, fields)
    return 200, {"ok": True, "member": updated}


class handler(JsonHandler):
    def do_POST(self):
        try:
            store = get_store()
            body = self._read_body()
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            if len(parts) == 2:
                status, payload = handle_create_member(store, self._headers_dict(), body)
            elif len(parts) == 3:
                status, payload = handle_update_member(
                    store, self._headers_dict(), parts[2], body
                )
            else:
                status, payload = 404, {"ok": False, "error": "not found"}
            self._json(status, payload)
        except ApiError as exc:
            self._json(exc.status, exc.payload)
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace
            self._json(500, {"ok": False, "error": str(exc)[:200]})
