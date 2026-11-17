-- Bill Collect — Supabase (Postgres) schema
-- ============================================================
-- Run this once in the Supabase SQL editor for a NEW, free-tier
-- project. It creates the tables only; it inserts no data.
-- Data comes from scripts/migrate_state_json_to_sql.py (local only).
--
-- No real credentials belong in this repo. The Vercel functions
-- connect with SUPABASE_URL + SUPABASE_SERVICE_KEY (server-only
-- env vars). The browser app uses SUPABASE_ANON_KEY and never sees
-- the service key.
--
-- Row-level security intent (enable RLS and add policies in the
-- Supabase dashboard after running this file):
--   * members / months / payments / bill_lines / audit_log:
--     - Authenticated users (Sunny, via Supabase Auth) may
--       SELECT/INSERT/UPDATE on rows they own. This is a
--       single-household tracker, so the policy is: any
--       authenticated user may read/write. Do NOT open these
--       tables to the anon role.
--     - The `service_role` key bypasses RLS and is used ONLY by
--       the Vercel server functions. It is never shipped in
--       app.js or any client bundle.
--   * If RLS is left off, the tables are still protected by the
--     Vercel API layer (AGENT_SECRET / user Bearer checks in
--     api/_lib.py), but turning RLS on is the second line of
--     defence. Do both.
--
-- Amounts are NUMERIC(10,2) USD. Dates are DATE. All ids are UUIDs
-- so the existing state.json ids can be preserved by migration.

create extension if not exists "pgcrypto";

-- One row per person on the AT&T family plan. A person appears
-- once here even though they appear in every month in state.json.
create table if not exists members (
  id uuid primary key,
  name text not null,
  phone text not null default '',
  att_name text not null default '',
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create index if not exists members_phone_idx on members (phone);
create index if not exists members_name_idx on members (name);

-- One row per bill month. bill_total is the AT&T total for the
-- month (sum of bill_lines, verified by migration).
-- status: 'draft' (created by Muse from a bill, awaiting Sunny's
-- review), 'active' (the month being collected now), 'closed'
-- (fully collected / superseded).
create table if not exists months (
  id uuid primary key,
  label text not null,
  bill_total numeric(10,2) not null default 0,
  due_date date,
  status text not null default 'draft'
    check (status in ('draft', 'active', 'closed')),
  created_at timestamptz not null default now()
);
create index if not exists months_label_idx on months (label);
create index if not exists months_status_idx on months (status);

-- One row per payment. A member's "received" for a month is the
-- sum of their confirmed, non-void payments for that month; a
-- member is "paid" when that sum >= the bill_lines amount.
-- source_id is UNIQUE so the same Zelle email / Plaid transaction
-- / WhatsApp line can never be counted twice, no matter how many
-- times the Muse cron retries it.
-- status: 'pending' (recorded by Muse, needs Sunny's review —
-- e.g. amount does not match the due, or sender matched by name
-- only), 'confirmed' (accepted), 'void' (undone, excluded from
-- all totals).
create table if not exists payments (
  id uuid primary key,
  member_id uuid not null references members (id),
  month_id uuid not null references months (id),
  amount numeric(10,2) not null check (amount > 0 and amount <= 1000),
  date_paid date,
  method text not null default 'Other'
    check (method in ('Zelle', 'Venmo', 'Cash', 'Other')),
  source text not null default 'app',
  source_id text not null unique,
  status text not null default 'confirmed'
    check (status in ('pending', 'confirmed', 'void')),
  created_by text not null default 'user',
  created_at timestamptz not null default now()
);
create index if not exists payments_member_month_idx
  on payments (member_id, month_id);
create index if not exists payments_month_idx on payments (month_id);
create index if not exists payments_status_idx on payments (status);
create index if not exists payments_source_idx on payments (source, source_id);

-- One row per member per month: what that member owes that month.
-- This is the due roster. bill_lines.amount is the "due".
-- confidence 0-99 is the bill parser's confidence (99 = exact
-- "Total for <phone> $x" line, 96 = phone+amount row fallback).
-- source_text is the bill line the amount came from, for auditing.
create table if not exists bill_lines (
  id uuid primary key default gen_random_uuid(),
  month_id uuid not null references months (id),
  member_id uuid references members (id),
  phone text not null default '',
  amount numeric(10,2) not null check (amount >= 0 and amount <= 1000),
  confidence integer not null default 0 check (confidence >= 0 and confidence <= 99),
  source_text text not null default '',
  -- Fields the app UI edits per member per month. `excluded` marks
  -- the owner's own lines: their due counts as owner-paid, not
  -- collectible (see v_month_totals / api/_lib.py month_view).
  notes text not null default '',
  reminder_sent boolean not null default false,
  excluded boolean not null default false,
  created_at timestamptz not null default now(),
  unique (month_id, member_id)
);
create index if not exists bill_lines_month_idx on bill_lines (month_id);
create index if not exists bill_lines_phone_idx on bill_lines (phone);

-- Append-only audit trail. Every payment create/confirm/void,
-- month create/rename, and bill-line change writes one row here.
-- details is free-form JSON, e.g. {"amount": 37.65, "method":
-- "Zelle", "source_id": "gmail:<message-id>", "reason": "..."}.
create table if not exists audit_log (
  id uuid primary key default gen_random_uuid(),
  actor text not null,
  action text not null,
  entity text not null,
  entity_id text not null default '',
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists audit_log_entity_idx on audit_log (entity, entity_id);
create index if not exists audit_log_created_idx on audit_log (created_at);

-- Helpful read views (optional; the API computes the same totals,
-- these are for ad-hoc checks in the Supabase SQL editor).
create or replace view v_month_totals as
  select
    m.id as month_id,
    m.label,
    m.bill_total,
    coalesce(sum(bl.amount), 0) as lines_total,
    coalesce(sum(p.amount), 0) as collected
  from months m
  left join bill_lines bl on bl.month_id = m.id
  left join payments p
    on p.month_id = m.id
    and p.status = 'confirmed'
  group by m.id, m.label, m.bill_total;
