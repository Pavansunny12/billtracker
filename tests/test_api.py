"""Tests for the Bill Collect agent-controlled API (Option A).

Stdlib unittest only, no external deps, no network, no Supabase.
Handlers are exercised through InMemoryStore with a small seeded
month (2 members, dues $37.65 + $30.00).

Run from the repo root:
    python3 -m unittest discover -s tests -v
"""

import base64
import hashlib
import hmac as hmac_mod
import importlib.util
import json
import os
import sys
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(REPO, "api")
sys.path.insert(0, API_DIR)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(API_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_lib = load("_lib", "_lib.py")
payments = load("payments", "payments.py")
bill_ingest = load("bill_ingest", "bill_ingest.py")
months = load("months", "months.py")
summary = load("summary", "summary.py")
config = load("config", "config.py")

AGENT_HEADERS = {"X-Agent-Key": "test-agent-secret"}
TEST_JWT_SECRET = "test-jwt-secret"
TEST_SUPABASE_URL = "https://fqapqmhxsgcogsvtwnnz.supabase.co"


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_token(secret=TEST_JWT_SECRET, exp_offset=3600, aud="authenticated",
               iss=TEST_SUPABASE_URL + "/auth/v1", sub="user-1"):
    """Mint a real HS256 JWT the way Supabase would (test secret only)."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = {"sub": sub, "aud": aud, "iss": iss,
               "exp": now + exp_offset, "iat": now}
    payload_b64 = _b64url(json.dumps(payload).encode())
    sig = hmac_mod.new(
        secret.encode(), ("%s.%s" % (header, payload_b64)).encode(),
        hashlib.sha256,
    ).digest()
    return "%s.%s.%s" % (header, payload_b64, _b64url(sig))


def user_headers(token=None):
    return {"Authorization": "Bearer " + (token or mint_token())}


USER_HEADERS = user_headers()


def seed_store():
    store = _lib.InMemoryStore()
    store.upsert_member({"id": "m1", "name": "Test One", "phone": "1234567890",
                         "att_name": "", "active": True})
    store.upsert_member({"id": "m2", "name": "Test Two", "phone": "5551234567",
                         "att_name": "", "active": True})
    store.create_month({"id": "mo1", "label": "October 2026", "bill_total": 67.65,
                        "due_date": "2026-11-11", "status": "active"})
    for line in (
        {"id": "l1", "month_id": "mo1", "member_id": "m1", "phone": "1234567890",
         "amount": 37.65, "confidence": 99, "source_text": "", "notes": "",
         "reminder_sent": False, "excluded": False},
        {"id": "l2", "month_id": "mo1", "member_id": "m2", "phone": "5551234567",
         "amount": 30.00, "confidence": 99, "source_text": "", "notes": "",
         "reminder_sent": False, "excluded": False},
    ):
        store.upsert_bill_line(line)
    return store


def payment_body(**overrides):
    body = {
        "member_id": "m1",
        "month_id": "mo1",
        "amount": 37.65,
        "date_paid": "2026-11-05",
        "method": "Zelle",
        "source": "gmail",
        "source_id": "gmail:msg-1",
    }
    body.update(overrides)
    return json.dumps(body).encode("utf-8")


class AgentApiTest(unittest.TestCase):
    def setUp(self):
        self._old_env = {}
        for key in ("AGENT_SECRET", "SUPABASE_URL", "SUPABASE_ANON_KEY",
                    "SUPABASE_JWT_SECRET"):
            self._old_env[key] = os.environ.get(key)
        os.environ["AGENT_SECRET"] = "test-agent-secret"
        os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
        os.environ["SUPABASE_JWT_SECRET"] = TEST_JWT_SECRET
        os.environ.pop("SUPABASE_ANON_KEY", None)
        _lib._AUTH_USER_CACHE.clear()
        self.store = seed_store()
        self.user_headers = user_headers()

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _lib._AUTH_USER_CACHE.clear()

    # -- auth ---------------------------------------------------------
    def test_payment_requires_auth(self):
        status, payload = payments.handle_create_payment(
            self.store, {}, payment_body())
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_fail_closed_when_agent_secret_unset(self):
        os.environ.pop("AGENT_SECRET", None)
        self.assertFalse(_lib.is_agent(AGENT_HEADERS))
        status, payload = payments.handle_create_payment(
            self.store, AGENT_HEADERS, payment_body())
        self.assertEqual(status, 401)

    def test_user_bearer_accepted_for_payments(self):
        status, payload = payments.handle_create_payment(
            self.store, self.user_headers, payment_body())
        self.assertEqual(status, 201)

    def test_bill_ingest_is_agent_only(self):
        status, _ = bill_ingest.handle_bill_ingest(
            self.store, self.user_headers,
            json.dumps({"text": "Total for 123.456.7890 $37.65"}).encode())
        self.assertEqual(status, 401)

    # -- validation ---------------------------------------------------
    def test_payment_validation_rejections(self):
        bad_bodies = [
            payment_body(amount=0),
            payment_body(amount=1000.01),
            payment_body(amount="lots"),
            payment_body(method="Bitcoin"),
            payment_body(member_id="nope"),
            payment_body(month_id="nope"),
            payment_body(date_paid="05/11/2026"),
            payment_body(source_id=""),
        ]
        for body in bad_bodies:
            status, payload = payments.handle_create_payment(
                self.store, AGENT_HEADERS, body)
            self.assertEqual(status, 400, payload)
            self.assertFalse(payload["ok"])

    def test_payment_for_member_with_no_due_rejected(self):
        store = _lib.InMemoryStore()
        store.upsert_member({"id": "m9", "name": "No Due", "phone": "9999999999",
                             "att_name": "", "active": True})
        store.create_month({"id": "mo9", "label": "October 2026", "bill_total": 0,
                            "due_date": None, "status": "active"})
        status, _ = payments.handle_create_payment(
            store, AGENT_HEADERS,
            payment_body(member_id="m9", month_id="mo9"))
        self.assertEqual(status, 400)

    # -- idempotency / partial / void ---------------------------------
    def test_idempotent_duplicate_returns_same_payment(self):
        status1, first = payments.handle_create_payment(
            self.store, AGENT_HEADERS, payment_body())
        status2, second = payments.handle_create_payment(
            self.store, AGENT_HEADERS, payment_body())
        self.assertEqual((status1, status2), (201, 200))
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["payment"]["id"], second["payment"]["id"])
        self.assertEqual(len(self.store.list_payments(month_id="mo1")), 1)
        view = _lib.month_view(self.store, self.store.get_month("mo1"))
        member = next(m for m in view["members"] if m["id"] == "m1")
        self.assertTrue(member["paid"])
        self.assertEqual(member["received"], 37.65)  # counted once, not twice

    def test_partial_payment_does_not_mark_paid(self):
        status, payload = payments.handle_create_payment(
            self.store, AGENT_HEADERS, payment_body(amount=10.00))
        self.assertEqual(status, 201)
        self.assertFalse(payload["member_paid"])
        self.assertEqual(payload["member_received"], 10.00)
        status, payload = payments.handle_create_payment(
            self.store, AGENT_HEADERS,
            payment_body(amount=27.65, source_id="gmail:msg-2"))
        self.assertTrue(payload["member_paid"])
        self.assertEqual(payload["member_received"], 37.65)

    def test_agent_source_conflict_on_different_amount(self):
        payments.handle_create_payment(self.store, AGENT_HEADERS, payment_body())
        status, payload = payments.handle_create_payment(
            self.store, AGENT_HEADERS, payment_body(amount=20.00))
        self.assertEqual(status, 409)
        self.assertEqual(len(self.store.list_payments(month_id="mo1")), 1)

    def test_app_source_correction_updates_in_place(self):
        body = payment_body(source="app", source_id="app-manual:mo1:m1")
        payments.handle_create_payment(self.store, AGENT_HEADERS, body)
        status, payload = payments.handle_create_payment(
            self.store, AGENT_HEADERS,
            payment_body(source="app", source_id="app-manual:mo1:m1", amount=30.00))
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("updated"))
        self.assertEqual(len(self.store.list_payments(month_id="mo1")), 1)
        self.assertEqual(float(payload["payment"]["amount"]), 30.00)

    def test_void_undoes_payment(self):
        _, created = payments.handle_create_payment(
            self.store, AGENT_HEADERS, payment_body())
        pid = created["payment"]["id"]
        status, payload = payments.handle_void_payment(
            self.store, AGENT_HEADERS, pid)
        self.assertEqual(status, 200)
        self.assertEqual(payload["payment"]["status"], "void")
        view = _lib.month_view(self.store, self.store.get_month("mo1"))
        member = next(m for m in view["members"] if m["id"] == "m1")
        self.assertFalse(member["paid"])
        self.assertEqual(member["received"], 0)

    def test_pending_payment_does_not_count_toward_collected(self):
        payments.handle_create_payment(
            self.store, AGENT_HEADERS,
            payment_body(status="pending"))
        view = _lib.month_view(self.store, self.store.get_month("mo1"))
        self.assertEqual(view["totals"]["collected"], 0)
        member = next(m for m in view["members"] if m["id"] == "m1")
        self.assertEqual(member["paymentStatus"], "pending")
        pid = self.store.list_payments(month_id="mo1")[0]["id"]
        payments.handle_confirm_payment(self.store, AGENT_HEADERS, pid)
        view = _lib.month_view(self.store, self.store.get_month("mo1"))
        self.assertEqual(view["totals"]["collected"], 37.65)

    # -- bill ingest --------------------------------------------------
    def test_bill_ingest_parses_total_for_lines(self):
        text = (
            "AT&T Bill\n"
            "Total for 123.456.7890 $37.65\n"
            "Total for 999.888.7777 $25.00\n"
        )
        status, payload = bill_ingest.handle_bill_ingest(
            self.store, AGENT_HEADERS,
            json.dumps({"text": text, "label": "November 2026"}).encode())
        self.assertEqual(status, 200)
        draft = payload["draft"]
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["parsedTotal"], 62.65)
        line = next(l for l in draft["lines"] if l["member_id"] == "m1")
        self.assertEqual(line["newAmount"], 37.65)
        self.assertEqual(line["oldAmount"], 37.65)
        self.assertEqual(line["confidence"], 99)
        self.assertEqual([u["phone"] for u in draft["unknownPhones"]], ["9998887777"])
        self.assertEqual([m["member_id"] for m in draft["missingMembers"]], ["m2"])
        # Draft only: nothing was written to the store.
        self.assertEqual(len(self.store.list_months()), 1)

    def test_parse_bill_totals_row_fallback(self):
        totals = _lib.parse_bill_totals("123.456.7890 Some Name $25.00\n")
        self.assertIn("1234567890", totals)
        self.assertEqual(totals["1234567890"]["amount"], 25.00)
        self.assertEqual(totals["1234567890"]["confidence"], 96)

    # -- months / summary / CORS --------------------------------------
    def test_month_view_totals_and_who_owes(self):
        payments.handle_create_payment(self.store, AGENT_HEADERS, payment_body())
        status, payload = months.handle_get_month(self.store, AGENT_HEADERS, "mo1")
        self.assertEqual(status, 200)
        totals = payload["month"]["totals"]
        self.assertEqual(totals["collected"], 37.65)
        self.assertEqual(totals["remaining"], 30.00)
        self.assertEqual([w["member_id"] for w in payload["month"]["whoOwes"]], ["m2"])

    def test_summary_requires_auth(self):
        status, _ = summary.handle_summary(self.store, {}, {})
        self.assertEqual(status, 401)

    def test_cors_restricted_no_wildcard(self):
        self.assertEqual(_lib.cors_headers("https://evil.example"), {})
        allowed = _lib.cors_headers("https://billtracker-tsyw-rose.vercel.app")
        self.assertEqual(
            allowed.get("Access-Control-Allow-Origin"),
            "https://billtracker-tsyw-rose.vercel.app",
        )
        self.assertNotIn("*", json.dumps(allowed))


class JwtVerificationTest(unittest.TestCase):
    """User Bearer tokens must be verified JWTs, not just present.

    HS256 path is exercised with a test secret; the Supabase Auth
    /user path is exercised with an injected fake fetcher, so no
    test touches the network.
    """

    def setUp(self):
        self._old_env = {}
        for key in ("AGENT_SECRET", "SUPABASE_URL", "SUPABASE_ANON_KEY",
                    "SUPABASE_JWT_SECRET"):
            self._old_env[key] = os.environ.get(key)
        os.environ["AGENT_SECRET"] = "test-agent-secret"
        os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
        os.environ["SUPABASE_JWT_SECRET"] = TEST_JWT_SECRET
        os.environ.pop("SUPABASE_ANON_KEY", None)
        _lib._AUTH_USER_CACHE.clear()
        self.store = seed_store()

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _lib._AUTH_USER_CACHE.clear()

    # -- HS256 path (SUPABASE_JWT_SECRET) ------------------------------
    def test_valid_token_verifies_and_authenticates(self):
        token = mint_token()
        payload = _lib.verify_user_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "user-1")
        actor, err = _lib.require_auth(user_headers(token))
        self.assertEqual((actor, err), ("user", None))

    def test_expired_token_rejected(self):
        token = mint_token(exp_offset=-3600)
        self.assertIsNone(_lib.verify_user_token(token))
        status, _ = payments.handle_create_payment(
            self.store, user_headers(token), payment_body())
        self.assertEqual(status, 401)

    def test_wrong_signature_rejected(self):
        token = mint_token(secret="a-different-secret")
        self.assertIsNone(_lib.verify_user_token(token))
        actor, err = _lib.require_auth(user_headers(token))
        self.assertIsNone(actor)
        self.assertEqual(err["error"], "unauthorized")

    def test_tampered_payload_rejected(self):
        token = mint_token()
        head, payload_b64, sig = token.split(".")
        tampered = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B")
        self.assertIsNone(_lib.verify_user_token("%s.%s.%s" % (head, tampered, sig)))

    def test_wrong_audience_rejected(self):
        self.assertIsNone(_lib.verify_user_token(mint_token(aud="someone-else")))

    def test_malformed_and_missing_tokens_rejected(self):
        for bad in ("", "not-a-jwt", "two.parts",
                    mint_token() + ".extra", "Bearer "):
            self.assertIsNone(_lib.verify_user_token(bad), bad)
        actor, err = _lib.require_auth({})
        self.assertIsNone(actor)
        self.assertEqual(err["error"], "unauthorized")
        actor, err = _lib.require_auth({"Authorization": "Bearer "})
        self.assertIsNone(actor)

    def test_agent_key_still_works_alongside_jwt(self):
        actor, err = _lib.require_auth(AGENT_HEADERS)
        self.assertEqual((actor, err), ("muse", None))
        actor, err = _lib.require_auth(AGENT_HEADERS, agent_only=True)
        self.assertEqual(actor, "muse")

    def test_user_auth_fails_closed_without_supabase_config(self):
        os.environ.pop("SUPABASE_JWT_SECRET", None)
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_ANON_KEY", None)
        self.assertIsNone(_lib.verify_user_token(mint_token()))
        status, _ = payments.handle_create_payment(
            self.store, user_headers(), payment_body())
        self.assertEqual(status, 401)

    # -- Supabase Auth /user path (no JWT secret) ----------------------
    def _auth_env(self):
        os.environ.pop("SUPABASE_JWT_SECRET", None)
        os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
        os.environ["SUPABASE_ANON_KEY"] = "test-anon-key"

    def test_auth_endpoint_accepts_valid_token(self):
        self._auth_env()
        seen = {}

        def fake_fetch(url, headers):
            seen["url"] = url
            seen["headers"] = headers
            return 200, '{"id": "user-1", "email": "sunny@example.com"}'

        user = _lib.verify_user_token(mint_token(), fetcher=fake_fetch)
        self.assertEqual(user["email"], "sunny@example.com")
        self.assertEqual(seen["url"], TEST_SUPABASE_URL + "/auth/v1/user")
        self.assertEqual(seen["headers"]["apikey"], "test-anon-key")

    def test_auth_endpoint_rejects_on_401(self):
        self._auth_env()
        user = _lib.verify_user_token(
            mint_token(), fetcher=lambda u, h: (401, '{"msg": "bad token"}'))
        self.assertIsNone(user)

    def test_auth_endpoint_fails_closed_on_network_error(self):
        self._auth_env()

        def boom(url, headers):
            raise RuntimeError("network down")

        self.assertIsNone(_lib.verify_user_token(mint_token(), fetcher=boom))


class ConfigEndpointTest(unittest.TestCase):
    def setUp(self):
        self._old_env = {}
        for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY"):
            self._old_env[key] = os.environ.get(key)

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_config_returns_publishable_values_only(self):
        os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
        os.environ["SUPABASE_ANON_KEY"] = "test-anon-key"
        os.environ["SUPABASE_SERVICE_KEY"] = "super-secret-service-key"
        status, payload = config.handle_config()
        self.assertEqual(status, 200)
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["supabase_url"], TEST_SUPABASE_URL)
        self.assertEqual(payload["anon_key"], "test-anon-key")
        self.assertNotIn("super-secret-service-key", json.dumps(payload))
        self.assertNotIn("service", json.dumps(payload).lower())

    def test_config_unconfigured_when_env_missing(self):
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_ANON_KEY", None)
        status, payload = config.handle_config()
        self.assertEqual(status, 200)
        self.assertFalse(payload["configured"])


if __name__ == "__main__":
    unittest.main()
