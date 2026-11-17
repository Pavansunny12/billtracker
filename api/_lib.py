"""Bill Collect — shared server library (Vercel Python functions).

Stdlib only. Every handler in api/ imports from here so auth, CORS,
validation and the Supabase store behave the same everywhere.

Storage
-------
Supabase Postgres is the source of truth (see db/schema.sql). The
functions talk to it through PostgREST over HTTPS with the
*service* key, which lives only in Vercel env vars
(SUPABASE_URL, SUPABASE_SERVICE_KEY) and is never sent to the
browser. No GitHub token, no SYNC_SECRET, no state.json anywhere.

For tests and local development without Supabase, set
BILL_COLLECT_DEV_MEMORY=1 to use an in-memory store. That store
lives only in the serverless process and resets on cold start —
it is a dev/test aid, never production storage.

Auth
----
Two separate credentials, never in client JS:
  * Agent (Muse): header ``X-Agent-Key`` compared with the
    AGENT_SECRET env var (hmac, fail-closed when unset). This is
    what the daily Gmail-watch cron uses to record payments and
    ingest bills.
  * User (Sunny, in the browser): header ``Authorization: Bearer
    <Supabase access token>``. The token is verified by
    ``verify_user_token`` before any user request is honoured:
    its expiry is checked, then either its HS256 signature is
    verified locally against SUPABASE_JWT_SECRET (legacy
    HS256-signed projects only), or it is checked against
    Supabase Auth itself (GET <SUPABASE_URL>/auth/v1/user with
    the anon key — works for HS256 and ES256 projects alike).
    Any failure — missing token, bad signature, expired token,
    Auth returning non-200, network error, or no Supabase
    config at all — rejects the request (401). There is no
    "token was presented, good enough" path any more.

CORS
----
Allowed origins come from ALLOWED_ORIGINS (comma-separated).
Default is the production Vercel domain only. There is no ``*``.
A request from any other Origin gets no Access-Control-Allow-Origin
header at all.
"""

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid
from datetime import date

MAX_BODY_BYTES = 2 * 1024 * 1024
METHODS = ("Zelle", "Venmo", "Cash", "Other")
DEFAULT_ALLOWED_ORIGINS = ("https://billtracker-tsyw-rose.vercel.app",)


# ---------------------------------------------------------------------------
# Errors / small helpers
# ---------------------------------------------------------------------------

class ApiError(Exception):
    """An error that maps directly onto an HTTP response."""

    def __init__(self, status, message, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {"ok": False, "error": message}


def allowed_origins():
    raw = os.environ.get("ALLOWED_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return tuple(origins) if origins else DEFAULT_ALLOWED_ORIGINS


def cors_headers(origin):
    """CORS headers for a request Origin, or {} when not allowed."""
    if origin and origin in allowed_origins():
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Agent-Key",
            "Vary": "Origin",
        }
    return {}


def parse_body(body_bytes):
    """Parse a request body, enforcing the 2 MB cap. Raises ApiError."""
    if body_bytes is None:
        raise ApiError(400, "empty body")
    if len(body_bytes) > MAX_BODY_BYTES:
        raise ApiError(413, "body too large")
    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        raise ApiError(400, "invalid JSON body")
    if not isinstance(data, dict):
        raise ApiError(400, "body must be a JSON object")
    return data


def last10(phone):
    digits = re.sub(r"\D", "", str(phone or ""))
    return digits[-10:] if len(digits) >= 10 else digits


def normalize_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def valid_date(value):
    """True for a real YYYY-MM-DD date string (or None/empty)."""
    if value in (None, ""):
        return True
    if not isinstance(value, str):
        return False
    try:
        year, month, day = (int(p) for p in value.split("-"))
        date(year, month, day)
    except Exception:
        return False
    return len(value) == 10


def new_id():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def agent_secret():
    return os.environ.get("AGENT_SECRET", "")


def is_agent(headers):
    """True only when X-Agent-Key matches AGENT_SECRET. Fail-closed."""
    secret = agent_secret()
    if not secret:
        return False
    provided = headers.get("X-Agent-Key") or headers.get("x-agent-key") or ""
    return hmac.compare_digest(str(provided), secret)


def user_present(headers):
    """True when a non-empty Bearer token was presented.

    Presence only — this is NOT authentication. Use
    ``verify_user_token`` / ``require_auth`` to decide whether a
    request is allowed.
    """
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    return isinstance(auth, str) and auth.startswith("Bearer ") and len(auth) > 7


def _b64url_decode(segment):
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _jwt_payload_unchecked(token):
    """Decode a JWT payload WITHOUT verifying it (expiry pre-check only)."""
    try:
        parts = str(token).split(".")
        if len(parts) != 3:
            return None
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _jwt_expired(payload, leeway_seconds=30):
    """True when the payload has no usable exp or exp is in the past."""
    exp = payload.get("exp") if isinstance(payload, dict) else None
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return True
    return time.time() > exp + leeway_seconds


def _verify_hs256(token, secret):
    """Verify an HS256 JWT locally. Returns the payload or None.

    Used only when SUPABASE_JWT_SECRET is set (legacy HS256-signed
    Supabase projects). Checks alg, HMAC-SHA256 signature, expiry,
    aud == "authenticated" when present, and iss against
    SUPABASE_URL when both are present.
    """
    try:
        header_b64, payload_b64, sig_b64 = str(token).split(".")
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        if header.get("alg") != "HS256":
            return None
        signing_input = (header_b64 + "." + payload_b64).encode("ascii")
        expected = hmac.new(
            secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        if not isinstance(payload, dict) or _jwt_expired(payload):
            return None
        if payload.get("aud") not in (None, "authenticated"):
            return None
        iss = payload.get("iss")
        url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        if iss and url and not str(iss).startswith(url):
            return None
        return payload
    except Exception:
        return None


# Short-lived cache for Supabase Auth /user checks: one HTTPS call
# per token per minute at most. Keyed by the token's hash, never by
# the token itself in a log.
_AUTH_USER_CACHE = {}
_AUTH_USER_CACHE_TTL_SECONDS = 60


def _default_auth_user_fetcher(url, headers):
    """GET url -> (status, body). Returns (0, "") on network error."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception:
            return exc.code, ""
    except Exception:
        return 0, ""


def _verify_via_auth(token, fetcher=None):
    """Verify a token with Supabase Auth itself.

    GET <SUPABASE_URL>/auth/v1/user with the anon key: Supabase
    validates the signature and expiry for its own signing scheme
    (HS256 or ES256) and returns the user, or 401. Returns the
    user dict or None. Fail-closed on any error or missing config.
    ``fetcher`` is an injectable (url, headers) -> (status, body)
    used by tests; production uses the real HTTPS fetcher.
    """
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon = os.environ.get("SUPABASE_ANON_KEY", "")
    if not url or not anon:
        return None
    cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cached = _AUTH_USER_CACHE.get(cache_key)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    fetch = fetcher or _default_auth_user_fetcher
    try:
        status, body = fetch(
            url + "/auth/v1/user",
            {"apikey": anon, "Authorization": "Bearer " + token},
        )
    except Exception:
        return None
    if status != 200:
        return None
    try:
        user = json.loads(body)
    except Exception:
        return None
    if not isinstance(user, dict) or not user.get("id"):
        return None
    _AUTH_USER_CACHE[cache_key] = (
        user, time.monotonic() + _AUTH_USER_CACHE_TTL_SECONDS
    )
    return user


def verify_user_token(token, fetcher=None):
    """Verify a Supabase user access token. Returns user/payload or None.

    Order: reject malformed/expired tokens first (cheap, no
    network), then verify the signature — locally with
    SUPABASE_JWT_SECRET when set (HS256 legacy projects), else
    through Supabase Auth's /user endpoint. Anything unverifiable
    returns None: callers treat that as 401.
    """
    if not isinstance(token, str) or not token:
        return None
    payload = _jwt_payload_unchecked(token)
    if payload is None or _jwt_expired(payload):
        return None
    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if secret:
        return _verify_hs256(token, secret)
    return _verify_via_auth(token, fetcher=fetcher)


def require_auth(headers, agent_only=False, fetcher=None):
    """Return (actor, None) on success or (None, error-payload) on 401.

    actor is "muse" for the agent key, "user" for a verified
    Supabase Bearer token. When AGENT_SECRET is unset, agent auth
    always fails closed; when no Supabase config is present, user
    auth always fails closed. ``fetcher`` is passed through to
    token verification (tests only).
    """
    if is_agent(headers):
        return "muse", None
    if agent_only:
        return None, {"ok": False, "error": "unauthorized"}
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    token = auth[7:] if isinstance(auth, str) and auth.startswith("Bearer ") else ""
    if token and verify_user_token(token, fetcher=fetcher) is not None:
        return "user", None
    return None, {"ok": False, "error": "unauthorized"}


# ---------------------------------------------------------------------------
# Bill parsing (ported from app.js extractAttBillTotals)
# ---------------------------------------------------------------------------

_BILL_KEYWORDS = re.compile(
    r"^(total|page|bill|date|account|at&t|summary|activity|talk|text|data|"
    r"group|plan|shared|device|service|monthly|charges|fees|taxes|credits|"
    r"adjustments|payments|previous|balance|due|autopay|scheduled)",
    re.IGNORECASE,
)


def _find_name_near(lines, phone_idx):
    for offset in (1, 2, 3):
        idx = phone_idx - offset
        if idx < 0:
            break
        candidate = lines[idx].strip()
        if 2 < len(candidate) < 40 and not _BILL_KEYWORDS.match(candidate):
            if re.match(r"^[a-zA-Z]+[a-zA-Z\s.'\-]*$", candidate):
                return candidate
    return ""


def parse_bill_totals(text):
    """Parse AT&T bill text into {phone10: {amount, confidence, name, source}}.

    Matches the app.js parser: first the exact
    ``Total for <phone> [name] $amount`` lines (confidence 99),
    then ``<phone> <name> $amount`` rows (confidence 96).
    """
    totals = {}
    all_text = str(text or "")
    lines = all_text.splitlines()

    total_for = re.compile(
        r"Total\s+for\s+(?:1[.\-\s]?)?(\d{3})[.\-\s]?(\d{3})[.\-\s]?(\d{4})"
        r"\s*(.*?)\s*\$?\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2}))",
        re.IGNORECASE,
    )
    for match in total_for.finditer(all_text):
        digits = match.group(1) + match.group(2) + match.group(3)
        middle = match.group(4).strip()
        try:
            amount = float(match.group(5).replace(",", ""))
        except ValueError:
            continue
        name = ""
        if 2 < len(middle) < 40 and not _BILL_KEYWORDS.match(middle):
            if re.match(r"^[a-zA-Z\s.'\-]+$", middle):
                name = middle
        if not name:
            line_idx = next(
                (i for i, line in enumerate(lines) if match.group(0) in line), -1
            )
            if line_idx >= 0:
                name = _find_name_near(lines, line_idx)
        totals[digits] = {
            "amount": amount,
            "confidence": 99,
            "name": name,
            "source": "Total for %s.%s.%s $%.2f"
            % (match.group(1), match.group(2), match.group(3), amount),
        }

    row_re = re.compile(
        r"^(?:1[.\-\s]?)?(\d{3})[.\-\s](\d{3})[.\-\s](\d{4})\s+(.+?)"
        r"\s+\$?\s*(-?\d{1,4}(?:,\d{3})*(?:\.\d{2}))\s*$"
    )
    for index, line in enumerate(lines):
        row = row_re.match(line)
        if not row:
            continue
        digits = row.group(1) + row.group(2) + row.group(3)
        if digits in totals:
            continue
        middle = row.group(4).strip()
        try:
            amount = float(row.group(5).replace(",", ""))
        except ValueError:
            continue
        if not (0 < amount < 300):
            continue
        name = ""
        if 2 < len(middle) < 40 and not _BILL_KEYWORDS.match(middle):
            if re.match(r"^[a-zA-Z\s.'\-]+$", middle):
                name = middle
        if not name:
            name = _find_name_near(lines, index)
        totals[digits] = {
            "amount": amount,
            "confidence": 96,
            "name": name,
            "source": line.strip(),
        }
    return totals


# ---------------------------------------------------------------------------
# Payment validation + derived member state
# ---------------------------------------------------------------------------

def validate_payment(data, store):
    """Validate a POST /api/payments body.

    Returns (cleaned_dict, None) or (None, error_message).
    `store` is used to check the member and month exist and that a
    bill line (a due) exists for that member in that month.
    """
    if not isinstance(data, dict):
        return None, "body must be a JSON object"
    member_id = data.get("member_id")
    month_id = data.get("month_id")
    if not isinstance(member_id, str) or not member_id:
        return None, "member_id is required"
    if not isinstance(month_id, str) or not month_id:
        return None, "month_id is required"
    if store.get_member(member_id) is None:
        return None, "member not found"
    if store.get_month(month_id) is None:
        return None, "month not found"
    if store.get_bill_line(month_id, member_id) is None:
        return None, "no due for this member in this month"
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        return None, "amount must be a number"
    if amount != amount or amount <= 0 or amount > 1000:
        return None, "amount must be > 0 and <= 1000"
    method = data.get("method", "Other")
    if method not in METHODS:
        return None, "method must be one of Zelle, Venmo, Cash, Other"
    date_paid = data.get("date_paid") or ""
    if date_paid and not valid_date(date_paid):
        return None, "date_paid must be YYYY-MM-DD"
    source = data.get("source") or "app"
    if not isinstance(source, str) or not source:
        return None, "source is required"
    source_id = data.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        return None, "source_id is required (idempotency key)"
    return {
        "member_id": member_id,
        "month_id": month_id,
        "amount": round(amount, 2),
        "date_paid": date_paid or None,
        "method": method,
        "source": source,
        "source_id": source_id,
    }, None


def member_derived(store, month_id, member_id, due):
    """Derived paid state for one member in one month.

    received = sum of confirmed, non-void payments.
    paid = received >= due (and due > 0).
    pending = sum of pending payments (recorded, awaiting review).
    """
    payments = store.list_payments(month_id=month_id, member_id=member_id)
    received = sum(
        float(p["amount"]) for p in payments if p.get("status") == "confirmed"
    )
    pending = sum(
        float(p["amount"]) for p in payments if p.get("status") == "pending"
    )
    return {
        "received": round(received, 2),
        "pending_amount": round(pending, 2),
        "paid": bool(due > 0 and received >= due),
        "payments": payments,
    }


def month_view(store, month):
    """Full month payload for the app: members + totals + who owes."""
    lines = store.list_bill_lines(month["id"])
    members = []
    collected = 0.0
    collectible = 0.0
    owner_paid = 0.0
    paid_count = 0
    tracked_count = 0
    who_owes = []
    for line in lines:
        member = store.get_member(line["member_id"]) if line.get("member_id") else None
        due = float(line["amount"])
        excluded = bool(line.get("excluded"))
        derived = member_derived(store, month["id"], line["member_id"], due)
        if excluded:
            # Owner's own line: due counts as owner-paid, never collectible.
            owner_paid += due
        else:
            tracked_count += 1
            collected += derived["received"]
            collectible += due
            if derived["paid"]:
                paid_count += 1
        pending_amount = round(max(due - derived["received"], 0), 2)
        if pending_amount > 0 and not excluded:
            who_owes.append(
                {
                    "member_id": line["member_id"],
                    "name": (member or {}).get("name") or line.get("phone") or "Unknown",
                    "phone": line.get("phone", ""),
                    "due": due,
                    "received": derived["received"],
                    "pending": pending_amount,
                }
            )
        last_payment = derived["payments"][-1] if derived["payments"] else None
        members.append(
            {
                "id": line["member_id"],
                "name": (member or {}).get("name") or "",
                "phone": line.get("phone", ""),
                "attName": (member or {}).get("att_name") or "",
                "due": due,
                "received": derived["received"],
                "paid": derived["paid"],
                "datePaid": (last_payment or {}).get("date_paid") or "",
                "method": (last_payment or {}).get("method") or "",
                "notes": line.get("notes") or "",
                "reminderSent": bool(line.get("reminder_sent")),
                "excluded": excluded,
                "paymentStatus": (
                    "pending"
                    if any(p.get("status") == "pending" for p in derived["payments"])
                    and not derived["paid"]
                    else ("confirmed" if derived["paid"] else "")
                ),
                "lastUpdatedBy": (last_payment or {}).get("created_by") or "",
                "lastPaymentId": (last_payment or {}).get("id") or "",
                "draft": month.get("status") == "draft",
            }
        )
    total = {
        "totalDue": round(collectible + owner_paid, 2),
        "ownerPaid": round(owner_paid, 2),
        "collectible": round(collectible, 2),
        "collected": round(collected, 2),
        "remaining": round(max(collectible - collected, 0), 2),
        "paid": paid_count,
        "pending": tracked_count - paid_count,
        "percent": round(100 * paid_count / tracked_count) if tracked_count else 0,
    }
    return {
        "id": month["id"],
        "label": month.get("label", ""),
        "bill_total": float(month.get("bill_total") or collectible),
        "due_date": month.get("due_date"),
        "status": month.get("status", "active"),
        "members": members,
        "totals": total,
        "whoOwes": who_owes,
    }


# ---------------------------------------------------------------------------
# Store interface
# ---------------------------------------------------------------------------

class InMemoryStore:
    """In-memory store: tests + local dev (BILL_COLLECT_DEV_MEMORY=1).

    Same method surface as SupabaseStore. Data lives only in this
    process; a serverless cold start wipes it. Never production.
    """

    def __init__(self):
        self.members = {}
        self.months = {}
        self.bill_lines = {}  # (month_id, member_id) -> line dict
        self.payments = {}  # id -> payment dict
        self.audit_entries = []

    # members
    def list_members(self):
        return [copy.deepcopy(m) for m in self.members.values()]

    def get_member(self, member_id):
        m = self.members.get(member_id)
        return copy.deepcopy(m) if m else None

    def get_member_by_phone(self, phone10):
        for m in self.members.values():
            if last10(m.get("phone")) == phone10 and phone10:
                return copy.deepcopy(m)
        return None

    def upsert_member(self, member):
        self.members[member["id"]] = copy.deepcopy(member)
        return copy.deepcopy(member)

    # months
    def list_months(self):
        return [copy.deepcopy(m) for m in self.months.values()]

    def get_month(self, month_id):
        m = self.months.get(month_id)
        return copy.deepcopy(m) if m else None

    def create_month(self, month):
        self.months[month["id"]] = copy.deepcopy(month)
        return copy.deepcopy(month)

    def update_month(self, month_id, fields):
        if month_id not in self.months:
            return None
        self.months[month_id].update(fields)
        return copy.deepcopy(self.months[month_id])

    # bill lines
    def list_bill_lines(self, month_id):
        return [
            copy.deepcopy(v)
            for (mid, _), v in sorted(self.bill_lines.items())
            if mid == month_id
        ]

    def get_bill_line(self, month_id, member_id):
        line = self.bill_lines.get((month_id, member_id))
        return copy.deepcopy(line) if line else None

    def upsert_bill_line(self, line):
        key = (line["month_id"], line["member_id"])
        existing = self.bill_lines.get(key)
        if existing:
            existing.update(line)
            return copy.deepcopy(existing)
        line = dict(line)
        line.setdefault("id", new_id())
        self.bill_lines[key] = copy.deepcopy(line)
        return copy.deepcopy(line)

    def delete_bill_line(self, month_id, member_id):
        return self.bill_lines.pop((month_id, member_id), None) is not None

    # payments
    def list_payments(self, month_id=None, member_id=None):
        out = []
        for p in sorted(self.payments.values(), key=lambda x: x.get("created_at") or ""):
            if month_id and p.get("month_id") != month_id:
                continue
            if member_id and p.get("member_id") != member_id:
                continue
            out.append(copy.deepcopy(p))
        return out

    def get_payment(self, payment_id):
        p = self.payments.get(payment_id)
        return copy.deepcopy(p) if p else None

    def get_payment_by_source(self, source_id):
        for p in self.payments.values():
            if p.get("source_id") == source_id:
                return copy.deepcopy(p)
        return None

    def create_payment(self, payment):
        self.payments[payment["id"]] = copy.deepcopy(payment)
        return copy.deepcopy(payment)

    def update_payment(self, payment_id, fields):
        if payment_id not in self.payments:
            return None
        self.payments[payment_id].update(fields)
        return copy.deepcopy(self.payments[payment_id])

    # audit
    def audit(self, actor, action, entity, entity_id, details):
        entry = {
            "id": new_id(),
            "actor": actor,
            "action": action,
            "entity": entity,
            "entity_id": entity_id,
            "details": details,
        }
        self.audit_entries.append(entry)
        return entry


class SupabaseStore:
    """Supabase PostgREST store (stdlib urllib only, no SDK).

    Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from env. The
    service key bypasses RLS — it must only ever exist as a
    server-side env var, never in the browser bundle.
    """

    def __init__(self, url=None, service_key=None):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = service_key or os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not self.url or not self.key:
            raise ApiError(500, "Supabase not configured")

    # -- low-level REST --
    def _request(self, method, path, params=None, body=None):
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        req = urllib.request.Request(
            self.url + "/rest/v1/" + path + query,
            method=method,
            headers={
                "apikey": self.key,
                "Authorization": "Bearer " + self.key,
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        data = json.dumps(body).encode("utf-8") if body is not None else None
        try:
            with urllib.request.urlopen(req, data=data, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else []
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = exc.reason
            raise ApiError(502, "Supabase error: %s" % detail[:200])

    @staticmethod
    def _one(rows):
        return copy.deepcopy(rows[0]) if rows else None

    @staticmethod
    def _many(rows):
        return [copy.deepcopy(r) for r in (rows or [])]

    # members
    def list_members(self):
        return self._many(self._request("GET", "members", {"select": "*", "order": "name"}))

    def get_member(self, member_id):
        return self._one(
            self._request("GET", "members", {"select": "*", "id": "eq." + member_id})
        )

    def get_member_by_phone(self, phone10):
        if not phone10:
            return None
        for member in self.list_members():
            if last10(member.get("phone")) == phone10:
                return member
        return None

    def upsert_member(self, member):
        # PostgREST upsert needs a merge-duplicates Prefer header, so
        # do it explicitly: PATCH when the member exists, POST when new.
        if self.get_member(member["id"]) is not None:
            rows = self._request(
                "PATCH", "members", {"id": "eq." + member["id"]}, member
            )
            return self._one(rows) or copy.deepcopy(member)
        rows = self._request("POST", "members", None, member)
        return self._one(rows) or copy.deepcopy(member)

    # months
    def list_months(self):
        return self._many(
            self._request("GET", "months", {"select": "*", "order": "created_at"})
        )

    def get_month(self, month_id):
        return self._one(
            self._request("GET", "months", {"select": "*", "id": "eq." + month_id})
        )

    def create_month(self, month):
        rows = self._request("POST", "months", None, month)
        return self._one(rows) or copy.deepcopy(month)

    def update_month(self, month_id, fields):
        rows = self._request(
            "PATCH", "months", {"id": "eq." + month_id}, fields
        )
        return self._one(rows)

    # bill lines
    def list_bill_lines(self, month_id):
        return self._many(
            self._request(
                "GET", "bill_lines",
                {"select": "*", "month_id": "eq." + month_id, "order": "created_at"},
            )
        )

    def get_bill_line(self, month_id, member_id):
        return self._one(
            self._request(
                "GET", "bill_lines",
                {
                    "select": "*",
                    "month_id": "eq." + month_id,
                    "member_id": "eq." + member_id,
                },
            )
        )

    def upsert_bill_line(self, line):
        existing = self.get_bill_line(line["month_id"], line["member_id"])
        if existing:
            rows = self._request(
                "PATCH", "bill_lines", {"id": "eq." + existing["id"]}, line
            )
            return self._one(rows) or copy.deepcopy(line)
        rows = self._request("POST", "bill_lines", None, line)
        return self._one(rows) or copy.deepcopy(line)

    def delete_bill_line(self, month_id, member_id):
        line = self.get_bill_line(month_id, member_id)
        if line is None:
            return False
        self._request("DELETE", "bill_lines", {"id": "eq." + line["id"]})
        return True

    # payments
    def list_payments(self, month_id=None, member_id=None):
        params = {"select": "*", "order": "created_at"}
        if month_id:
            params["month_id"] = "eq." + month_id
        if member_id:
            params["member_id"] = "eq." + member_id
        return self._many(self._request("GET", "payments", params))

    def get_payment(self, payment_id):
        return self._one(
            self._request("GET", "payments", {"select": "*", "id": "eq." + payment_id})
        )

    def get_payment_by_source(self, source_id):
        return self._one(
            self._request(
                "GET", "payments",
                {"select": "*", "source_id": "eq." + urllib.parse.quote(source_id, safe="")},
            )
        )

    def create_payment(self, payment):
        rows = self._request("POST", "payments", None, payment)
        return self._one(rows) or copy.deepcopy(payment)

    def update_payment(self, payment_id, fields):
        rows = self._request(
            "PATCH", "payments", {"id": "eq." + payment_id}, fields
        )
        return self._one(rows)

    # audit
    def audit(self, actor, action, entity, entity_id, details):
        entry = {
            "actor": actor,
            "action": action,
            "entity": entity,
            "entity_id": str(entity_id),
            "details": details or {},
        }
        rows = self._request("POST", "audit_log", None, entry)
        return self._one(rows) or entry


def get_store():
    """Store for a live Vercel invocation (Supabase, or dev memory)."""
    if os.environ.get("BILL_COLLECT_DEV_MEMORY") == "1":
        global _DEV_STORE
        try:
            return _DEV_STORE
        except NameError:
            _DEV_STORE = InMemoryStore()
            return _DEV_STORE
    return SupabaseStore()


# ---------------------------------------------------------------------------
# HTTP plumbing for the Vercel function classes
# ---------------------------------------------------------------------------

from http.server import BaseHTTPRequestHandler


class JsonHandler(BaseHTTPRequestHandler):
    """Base class for the Bill Collect Vercel functions.

    Subclasses implement do_GET / do_POST and call
    ``self._json(status, payload)``. CORS is restricted (see
    cors_headers); preflight answers with the same rules.
    """

    def _origin(self):
        return self.headers.get("Origin") or ""

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        for key, value in cors_headers(self._origin()).items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "body too large")
        return self.rfile.read(length) if length > 0 else b""

    def _headers_dict(self):
        return {k: v for k, v in self.headers.items()}

    def do_OPTIONS(self):
        self.send_response(204)
        for key, value in cors_headers(self._origin()).items():
            self.send_header(key, value)
        self.end_headers()

    def log_message(self, *args):  # keep Vercel logs quiet
        pass
