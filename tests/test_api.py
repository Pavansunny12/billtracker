"""Tests for the Bill Collect agent-controlled API (Option A).

Stdlib unittest only, no external deps, no network, no Supabase.
Handlers are exercised through InMemoryStore with a small seeded
month (2 members, dues $37.65 + $30.00).

Run from the repo root:
    python3 -m unittest discover -s tests -v
"""

import importlib.util
import json
import os
import sys
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

AGENT_HEADERS = {"X-Agent-Key": "test-agent-secret"}
USER_HEADERS = {"Authorization": "Bearer test-user-token"}


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
        self._old_secret = os.environ.get("AGENT_SECRET")
        os.environ["AGENT_SECRET"] = "test-agent-secret"
        self.store = seed_store()

    def tearDown(self):
        if self._old_secret is None:
            os.environ.pop("AGENT_SECRET", None)
        else:
            os.environ["AGENT_SECRET"] = self._old_secret

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
            self.store, USER_HEADERS, payment_body())
        self.assertEqual(status, 201)

    def test_bill_ingest_is_agent_only(self):
        status, _ = bill_ingest.handle_bill_ingest(
            self.store, USER_HEADERS,
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


if __name__ == "__main__":
    unittest.main()
