# Bill Collect — Deploying the agent-controlled rebuild (Option A)

Supabase Postgres is now the source of truth. GitHub is no longer
the database: there is no `state.json` sync, no `GITHUB_TOKEN`,
and no secret in `app.js`. The Muse cron (daily 09:49 AM,
`bill-collect-email-watch`) is the controller: it reads AT&T
bill-ready emails and BofA Zelle emails in Gmail, calls the API
below with the agent key, and reports new bills and payments.

Do the steps in order. Nothing here pushes code or touches the
live site until Step 6.

---

## Step 0: Before you start — save October / November

`state.json` in this repo stops at September 2026. If October or
November payments live only on your phone, export them first:

1. Open the Bill Collect app on your phone.
2. Import & Backup → Export backup, and keep the JSON file.
3. Send that file to Muse — the migration in Step 3 only covers
   June–September, and October/November must be added from the
   backup before the old app is switched off.

## Step 1: Create the Supabase project (free)

1. Go to https://supabase.com → New project (free tier is enough
   for ~17 members).
2. Note the project URL, e.g. `https://xyzcompany.supabase.co`.
3. In the Supabase SQL editor, run `db/schema.sql` from this repo.
   It creates `members`, `months`, `payments`, `bill_lines` and
   `audit_log`, plus indexes. It inserts no data.
4. Still in the SQL editor, run `db/rls.sql`. It turns on Row
   Level Security for those five tables and adds a policy that
   only authenticated users may read/write (anon gets nothing).
   The Vercel functions use the service key and bypass RLS; the
   browser never sees it. RLS is the second line of defence
   behind the API layer — do both.

## Step 2: Set Vercel environment variables

In the Vercel dashboard → project `billtracker-tsyw-rose` →
Settings → Environment Variables (Production + Preview):

| Key | Value | Notes |
| --- | --- | --- |
| `SUPABASE_URL` | your Supabase project URL | e.g. `https://fqapqmhxsgcogsvtwnnz.supabase.co` |
| `SUPABASE_ANON_KEY` | Supabase anon/public key | Safe for the browser (RLS still applies); also served to the app by `GET /api/config` so sign-in works |
| `SUPABASE_SERVICE_KEY` | Supabase service_role key | **Server-only. Never paste this in chat, in `app.js`, or in the repo.** |
| `SUPABASE_JWT_SECRET` | Supabase JWT secret | **Optional, legacy HS256 projects only.** When set, the API verifies user tokens locally. When unset (the default for newer ES256 projects), the API verifies tokens through Supabase Auth instead. Do NOT set it unless your project signs JWTs with HS256 — setting it on an ES256 project breaks sign-in. |
| `AGENT_SECRET` | a long random secret you generate | Shared only between Vercel and Muse's secure vault |
| `ALLOWED_ORIGINS` | `https://billtracker-tsyw-rose.vercel.app` | Optional; this is the default. No `*` anywhere. |

Give `AGENT_SECRET` to Muse through the secure vault form, never
in chat. Rotate it if it ever leaks: new value in Vercel + new
value in the vault, then redeploy.

Delete the old variables if they are still there: `GITHUB_TOKEN`,
`GITHUB_REPO`, `GITHUB_FILE_PATH`, `SYNC_SECRET`. The new code
does not read them. Revoke the old GitHub token at
GitHub → Settings → Developer settings → Personal Access Tokens.

## Step 3: Migrate June–September data (local only)

```bash
python3 scripts/migrate_state_json_to_sql.py --check-only
python3 scripts/migrate_state_json_to_sql.py --out db/migrated.sql
```

`--check-only` verifies the per-month totals first (Jun $647.58,
Jul $669.18, Aug $747.38, Sep $691.26) and writes nothing. The
full run writes `db/migrated.sql` — that file contains member
names and phone numbers and is git-ignored; review it, run it in
the Supabase SQL editor, then delete it. Never commit it.

October/November from the Step 0 backup are not in this script —
tell Muse, and they will be added from the backup file.

## Step 4: Make the repo private and remove state.json history

`state.json` still contains all member names and phone numbers,
and it is in the public repo's history. Making the repo private
hides future reads but does not erase the old public commits.

1. GitHub → repo Settings → Danger Zone → Change visibility → Private.
2. Purge `state.json` from history. From a fresh clone, using
   git-filter-repo (install it first):
   ```bash
   git filter-repo --path state.json --invert-paths --force
   git remote add origin https://github.com/Pavansunny12/billtracker.git
   git push origin --force --all
   git push origin --force --tags
   ```
   Alternative with BFG Repo-Cleaner:
   ```bash
   bfg --delete-files state.json
   git reflog expire --expire=now --all && git gc --prune=now --aggressive
   git push origin --force --all
   ```
   Do NOT run these until Step 3 is done and the migration file
   has been applied — after the purge, `state.json` is gone from
   history for good. Tell everyone who cloned the repo to
   re-clone; their old clones still contain the file.
3. Confirm on GitHub that `state.json` is gone from the repo
   (the file itself stays in this local working copy as the
   migration source — do not delete it locally until Step 5 is
   verified live).

## Step 5: Payment detection — Gmail only (no bank reads)

Plaid is OUT. Do not connect it: Sunny does not want Muse
reading his bank or financial data, so there is no Plaid link,
no bank-transaction reading, and no other financial access.

Payments are detected from email only:

1. Turn on BofA email alerts (BofA app → Alerts → Zelle →
   payment received → email on). Sunny does this himself.
2. The daily cron reads Gmail for BofA "Zelle payment received"
   emails and for the AT&T bill-ready email, matches the sender
   + amount to a member, and records the payment via
   `POST /api/payments` with the Gmail message id as
   `source_id`.
3. New bills come from the AT&T bill PDF (Sunny sends it, or
   pastes the bill text): Muse calls `POST /api/bill_ingest`
   and shows a draft month for approval.
4. Anything email cannot see — a payment with no email, a
   WhatsApp-only note — is reported only when Sunny forwards
   it or pastes a WhatsApp export into the app.

## Step 5b: Create your sign-in user (Supabase Auth)

The app's sign-in screen is sign-in only — there is no
sign-up in the app. Create the one user first, in Supabase:

1. Supabase dashboard → Authentication → Users → Add user.
2. Email + a password, tick "Auto Confirm User" (or confirm
   via the email link), then Create user.
3. That email + password is what you type in the app's
   Sign in dialog. Password reset is also done from the
   Supabase dashboard (or the Auth "forgot password" email),
   not from the app.

## Step 6: Deploy

1. The `bill-collect-agent-control` branch is already on
   GitHub. Open a pull request, or merge it to `main` — nothing
   is merged yet, and the live site still runs the old code.
2. Vercel → Deployments → Redeploy (or push to `main` if Vercel
   auto-deploys from GitHub).
3. Open the app. The Sign in dialog appears: sign in with the
   user from Step 5b, and the months load from Supabase. If it
   shows "Sign in required" after signing in, the API is
   rejecting the token — check the Vercel env vars in Step 2
   (SUPABASE_URL + SUPABASE_ANON_KEY must match the project).
4. Spot-check one month against the old app: totals, who paid,
   who still owes. Then the old GitHub sync is done — the old
   `/api/state` endpoint no longer exists (it returns 404).

## Step 7: How Muse controls tracking from here

- Daily 09:49 AM cron (`bill-collect-email-watch`): checks Gmail
  for the AT&T bill-ready email and for BofA Zelle received
  emails, then reports new bills/payments in chat.
- New bill: Muse calls `POST /api/bill_ingest` with the bill
  text, shows you the draft (per-person amounts, changes vs last
  month, missing members, unknown phones), and creates the month
  only after you approve it.
- New payment: Muse calls `POST /api/payments` with the sender,
  amount, date, method, and the Gmail message id as `source_id`. The same payment can never be
  counted twice. Partial payments are recorded but the member
  stays unpaid until the confirmed total reaches the due.
- Undo: any payment Muse recorded can be undone from the app
  (Undo button on the member card) or via
  `POST /api/payments/<id>/void`. Every change is in `audit_log`.

## API reference (for Muse / debugging)

All endpoints need auth: `X-Agent-Key: <AGENT_SECRET>` (Muse) or
`Authorization: Bearer <Supabase session token>` (browser). No
auth → 401. Bodies are capped at 2 MB.

| Endpoint | Method | What it does |
| --- | --- | --- |
| `/api/config` | GET | Publishable Supabase config for the browser (URL + anon key). No auth; never returns the service key |
| `/api/months` | GET | List months with totals |
| `/api/months/<id>` | GET | One month: members, payments, who owes |
| `/api/months` | POST | Create a month from an approved draft |
| `/api/months/<id>` | POST | Rename / due date / status |
| `/api/months/<id>/lines` | POST | Update (or add) one member's line; `{remove:true}` removes it |
| `/api/members` | POST | Create a member (reuses by phone) |
| `/api/members/<id>` | POST | Rename / phone / att_name / active |
| `/api/payments` | POST | Record one payment (idempotent on `source_id`) |
| `/api/payments/<id>/void` | POST | Undo a payment |
| `/api/payments/<id>/confirm` | POST | Confirm a pending payment |
| `/api/bill_ingest` | POST | Agent-only: bill text → draft month (no writes) |
| `/api/summary` | GET | Collected vs bill vs still owed |
| `/api/extract_pdf` | POST | PDF → text (now requires auth) |

## Known gaps in this build (do not paper over)

1. **Sign-in is email + password only, no sign-up or password
   reset in the app.** The one user is created in the Supabase
   dashboard (Step 5b). Anyone else who needs access needs
   their own Supabase Auth user the same way.
2. **Backup restore is local-only.** Importing a JSON backup
   restores the app's offline cache; it does not write to
   Supabase. Use the migration script for server restores.
3. **October/November data is not migrated yet.** See Step 0.
4. **User tokens are verified, but the /user check adds one
   Supabase Auth call per token per minute** (cached 60s).
   If Supabase Auth is down, user requests get 401 while the
   agent key keeps working — the app falls back to its offline
   cache, and the cron is unaffected.

Done since the last version of this file: the Supabase Auth
login screen is built (Sign in dialog, sign-out, session
refresh), user Bearer tokens are verified as JWTs server-side
(`api/_lib.py verify_user_token`: HS256 locally when
SUPABASE_JWT_SECRET is set, else via Supabase Auth), and
`db/rls.sql` enables RLS with authenticated-only policies.

## 📱 How to Install on iPhone / Android
1. Open the production URL in Safari (iPhone) or Chrome (Android).
2. Tap the **Share** button (iOS) or the menu icon (Android).
3. Tap **Add to Home Screen**.
4. Open the app from your home screen. It will load instantly in full-screen native standalone mode!
