-- Bill Collect — Row Level Security (Supabase Postgres)
-- ============================================================
-- Run this ONCE in the Supabase SQL Editor AFTER db/schema.sql.
-- It does NOT create tables or insert data; it only turns on
-- Row Level Security and adds the access policies.
--
-- DO NOT run this against a live database until the migration
-- (scripts/migrate_state_json_to_sql.py) has been applied and
-- verified — RLS changes who can read/write, not the data.
--
-- Policy model (single-household tracker, one user — Sunny):
--   * authenticated users (signed in via Supabase Auth) may
--     read and write every row in all five tables.
--   * anon (not signed in) gets NO policy, so it can read and
--     write NOTHING, even though the anon key is public.
--   * service_role (the key the Vercel functions use, server-side
--     only) bypasses RLS by design, so the API keeps working.
-- The Vercel API layer (agent key / verified user JWT in
-- api/_lib.py) is the first gate; RLS is the second line of
-- defence, so a leaked anon key alone opens nothing.

alter table members enable row level security;
alter table months enable row level security;
alter table payments enable row level security;
alter table bill_lines enable row level security;
alter table audit_log enable row level security;

-- Authenticated users only: full read/write on every table.
-- (No policy is created for anon, so anon access is denied.)
drop policy if exists "authenticated full access" on members;
create policy "authenticated full access" on members
  for all to authenticated using (true) with check (true);

drop policy if exists "authenticated full access" on months;
create policy "authenticated full access" on months
  for all to authenticated using (true) with check (true);

drop policy if exists "authenticated full access" on payments;
create policy "authenticated full access" on payments
  for all to authenticated using (true) with check (true);

drop policy if exists "authenticated full access" on bill_lines;
create policy "authenticated full access" on bill_lines
  for all to authenticated using (true) with check (true);

drop policy if exists "authenticated full access" on audit_log;
create policy "authenticated full access" on audit_log
  for all to authenticated using (true) with check (true);
