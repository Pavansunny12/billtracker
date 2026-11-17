#!/usr/bin/env python3
"""Migrate Bill Collect state.json (Jun-Sep 2026) to Supabase SQL.

Reads the local state.json, dedupes members by phone (last 10
digits — the same person has a different member id in every
month), and emits INSERT statements for members / months /
bill_lines / payments / audit_log matching db/schema.sql.

Usage:
    python3 scripts/migrate_state_json_to_sql.py [--in state.json] [--out db/migrated.sql]
    python3 scripts/migrate_state_json_to_sql.py --check-only   # verify only, write nothing

This script NEVER talks to a database. It only writes a .sql file.
Review the file, then run it in the Supabase SQL editor yourself.

The output file contains member names and phone numbers. It is
git-ignored (db/migrated.sql) — never commit it, never paste it
into chat, never upload it anywhere public.

Verification: per-month totals are checked against the known
totals from the tracker (Jun $647.58, Jul $669.18, Aug $747.38,
Sep $691.26). A mismatch, a member with no name, or any amount
outside 0..1000 exits non-zero and writes nothing.
"""

import argparse
import json
import re
import sys
import uuid

EXPECTED_TOTALS = {
    "June 2026": 647.58,
    "July 2026": 669.18,
    "August 2026": 747.38,
    "September 2026": 691.26,
}
METHODS = {"Zelle", "Venmo", "Cash", "Other"}
NS = uuid.NAMESPACE_URL


def last10(phone):
    digits = re.sub(r"\D", "", str(phone or ""))
    return digits[-10:] if len(digits) >= 10 else digits


def sql_str(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def sql_bool(value):
    return "TRUE" if value else "FALSE"


def sql_num(value):
    return "%.2f" % float(value)


def as_uuid(value, fallback_key):
    """Keep a valid uuid, else derive a stable one from the key."""
    try:
        return str(uuid.UUID(str(value)))
    except Exception:
        return str(uuid.uuid5(NS, fallback_key))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_path", default="state.json")
    parser.add_argument("--out", dest="out_path", default="db/migrated.sql")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    with open(args.in_path, encoding="utf-8") as fh:
        data = json.load(fh)

    months_in = data.get("months")
    if not isinstance(months_in, list) or not months_in:
        print("ERROR: state.json has no months", file=sys.stderr)
        return 1

    warnings = []
    errors = []

    # --- dedupe members: same person, one row, first-seen id wins ---
    members = {}  # key -> member dict
    member_key_by_old_id = {}
    for month in months_in:
        for entry in month.get("members", []):
            name = str(entry.get("name") or "").strip()
            phone = str(entry.get("phone") or "").strip()
            key = last10(phone) or ("name:" + re.sub(r"[^a-z0-9]", "", name.lower()))
            if not name:
                errors.append(
                    "month %s: member id %s has no name"
                    % (month.get("label"), entry.get("id"))
                )
                continue
            if not phone:
                warnings.append(
                    "member %s (%s) has no phone number"
                    % (name, month.get("label"))
                )
            if key in members:
                prev = members[key]
                if prev["name"] != name:
                    warnings.append(
                        "same phone %s has two names: %r vs %r (kept first)"
                        % (phone, prev["name"], name)
                    )
                if entry.get("attName") and not prev.get("att_name"):
                    prev["att_name"] = entry["attName"]
            else:
                members[key] = {
                    "id": as_uuid(entry.get("id"), "member:" + key),
                    "name": name,
                    "phone": phone,
                    "att_name": str(entry.get("attName") or ""),
                    "active": True,
                }
            member_key_by_old_id[entry.get("id")] = key

    # --- verify per-month totals and amounts ---
    statements = []
    statements.append("-- Bill Collect migration from state.json (generated file).")
    statements.append("-- CONTAINS MEMBER NAMES AND PHONE NUMBERS — never commit this file.")
    statements.append("-- Generated locally; review before running in Supabase.")
    statements.append("begin;")
    for member in members.values():
        statements.append(
            "insert into members (id, name, phone, att_name, active) values (%s, %s, %s, %s, %s) "
            "on conflict (id) do nothing;"
            % (
                sql_str(member["id"]),
                sql_str(member["name"]),
                sql_str(member["phone"]),
                sql_str(member["att_name"]),
                sql_bool(member["active"]),
            )
        )

    n_lines = 0
    n_payments = 0
    for month in months_in:
        label = month.get("label") or ""
        month_id = as_uuid(month.get("id"), "month:" + label)
        entries = month.get("members", [])
        total = 0.0
        for entry in entries:
            due = float(entry.get("due") or 0)
            received = float(entry.get("received") or 0)
            total += due
            if not (0 <= due <= 1000):
                errors.append("%s: %s due %.2f out of range" % (label, entry.get("name"), due))
            if not (0 <= received <= 1000):
                errors.append("%s: %s received %.2f out of range" % (label, entry.get("name"), received))
            if received > due:
                warnings.append(
                    "%s: %s received %.2f > due %.2f" % (label, entry.get("name"), received, due)
                )
            if entry.get("paid") and received == 0:
                warnings.append(
                    "%s: %s marked paid but received is 0 — no payment row created"
                    % (label, entry.get("name"))
                )
        expected = EXPECTED_TOTALS.get(label)
        if expected is not None and abs(total - expected) > 0.005:
            errors.append(
                "%s total %.2f does not match known total %.2f" % (label, total, expected)
            )
        print("%-16s members=%2d total=$%7.2f expected=$%s %s"
              % (label, len(entries), total,
                 ("%.2f" % expected) if expected is not None else "n/a",
                 "OK" if expected is None or abs(total - expected) <= 0.005 else "MISMATCH"))

        statements.append(
            "insert into months (id, label, bill_total, due_date, status) values (%s, %s, %s, NULL, 'closed') "
            "on conflict (id) do nothing;"
            % (sql_str(month_id), sql_str(label), sql_num(total))
        )
        for entry in entries:
            key = member_key_by_old_id.get(entry.get("id"))
            if key is None:
                continue  # already reported as an error (no name)
            member = members[key]
            due = float(entry.get("due") or 0)
            received = float(entry.get("received") or 0)
            method = str(entry.get("method") or "")
            if method and method not in METHODS:
                warnings.append(
                    "%s: %s method %r is not Zelle/Venmo/Cash/Other — stored as Other"
                    % (label, entry.get("name"), method)
                )
                method = "Other"
            statements.append(
                "insert into bill_lines (month_id, member_id, phone, amount, confidence, source_text, notes, reminder_sent, excluded) "
                "values (%s, %s, %s, %s, 0, 'migration from state.json', %s, %s, %s) "
                "on conflict (month_id, member_id) do nothing;"
                % (
                    sql_str(month_id),
                    sql_str(member["id"]),
                    sql_str(entry.get("phone") or ""),
                    sql_num(due),
                    sql_str(str(entry.get("notes") or "")),
                    sql_bool(bool(entry.get("reminderSent"))),
                    sql_bool(bool(entry.get("excluded"))),
                )
            )
            n_lines += 1
            if received > 0:
                payment_id = str(uuid.uuid5(NS, "migration:%s:%s" % (month_id, member["id"])))
                source_id = "migration:%s:%s" % (month_id, member["id"])
                date_paid = entry.get("datePaid") or None
                statements.append(
                    "insert into payments (id, member_id, month_id, amount, date_paid, method, source, source_id, status, created_by) "
                    "values (%s, %s, %s, %s, %s, %s, 'migration', %s, 'confirmed', 'migration') "
                    "on conflict (source_id) do nothing;"
                    % (
                        sql_str(payment_id),
                        sql_str(member["id"]),
                        sql_str(month_id),
                        sql_num(received),
                        sql_str(date_paid),
                        sql_str(method or "Other"),
                        sql_str(source_id),
                    )
                )
                n_payments += 1
        statements.append(
            "insert into audit_log (actor, action, entity, entity_id, details) values "
            "('migration', 'month.migrated', 'month', %s, %s);"
            % (sql_str(month_id), sql_str(json.dumps({"label": label, "members": len(entries), "total": round(total, 2)})))
        )
    statements.append("commit;")

    for warning in warnings:
        print("WARNING: " + warning)
    for error in errors:
        print("ERROR: " + error, file=sys.stderr)
    print("members=%d months=%d bill_lines=%d payments=%d warnings=%d errors=%d"
          % (len(members), len(months_in), n_lines, n_payments, len(warnings), len(errors)))

    if errors:
        print("Migration aborted: fix the errors above; nothing was written.", file=sys.stderr)
        return 1
    if args.check_only:
        print("Check only: totals verified, no file written.")
        return 0
    with open(args.out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(statements) + "\n")
    print("Wrote %s (%d statements). Review it, then run it in Supabase. Do NOT commit it."
          % (args.out_path, len(statements)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
