# Alluvi Console — Web

The tenant + super-admin control panel for the open-source Alluvi pipeline
(RunPod GPUs + ComfyUI, orchestrated by `orchestrator/`, backed by Supabase).
React 18 + Vite 6 + `@supabase/supabase-js` v2 + lucide-react, hand-rolled CSS.

The web app and the pipeline **never call each other's code** — they meet only
through the Supabase database, Storage, and a few Edge Functions. The browser
uses the **anon/publishable key with RLS**; the **service key lives only inside
the Edge Functions** (never in the bundle).

## What each screen writes

| Screen | Writes |
|---|---|
| Signup | `tenants` + `tenant_members` (via `provision-tenant`); JWT `app_metadata.tenant_id` |
| Setup / Settings | `tenants` (brand/script jsonb, GPU); Anthropic key → **Vault** (via `store-tenant-secret`) |
| Product | `products` (1/tenant) + `media_assets` + photo → `products` bucket |
| Accounts | `tiktok_accounts` (+ optional voice → `audio` bucket + `media_assets`) |
| Run settings | `tenant_pipeline_config` |
| Run button | enqueues a `jobs` row (`job_type='pipeline_run'`) via `trigger-pipeline` |
| Rate | `asset_ratings` (one row per `output_id`; 25-id rubric contract) |
| Publishing / Analytics / Engine | read-only |
| Super-admin | all cross-tenant data via `admin-data` (service role) |

## Setup

1. **Apply the migrations** (in order) on your Supabase project — the base
   schema plus the additive web-support migrations:
   - `db/migrations/001_alluvi_schema.sql` (base)
   - `db/migrations/017_storage_policies.sql` (Storage RLS, per-tenant folder)
   - `db/migrations/018_impersonation_events.sql` (super-admin audit log)
   - `db/migrations/019_set_secret_rpc.sql` (Vault write helper)
2. **Deploy the Edge Functions** (from repo root, `supabase/functions/`):
   ```bash
   supabase functions deploy provision-tenant   --no-verify-jwt
   supabase functions deploy store-tenant-secret --no-verify-jwt
   supabase functions deploy trigger-pipeline    --no-verify-jwt
   supabase functions deploy admin-data          --no-verify-jwt
   supabase secrets set ADMIN_SECRET='Alluvi@admin@1512'   # must equal ADMIN_PASS in src/lib/constants.js
   ```
   (`SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` are injected automatically.)
3. **Turn OFF** Supabase Auth "Confirm email" so signup → immediate session.
4. **Configure the web env:** `cp .env.example .env.local` and set
   `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY` (the publishable key).
5. Run it:
   ```bash
   npm install
   npm run dev      # http://localhost:5173
   npm run build    # production build to dist/
   ```

## Auth

- **Members** sign up / sign in with Supabase Auth. On first session the app
  calls `provision-tenant` (creates the tenant + owner membership, stamps
  `app_metadata.tenant_id`) and then **refreshes the session** so RLS reads work.
- **Super-admin** uses hard-coded credentials in `src/lib/constants.js`
  (`admin` / `Alluvi@admin@1512`) — **MVP only; rotate / move to real auth before
  launch.** Cross-tenant data is fetched via the `admin-data` Edge Function,
  gated by `ADMIN_SECRET`.

## Backend dependency for the Run button

`trigger-pipeline` enqueues a `jobs` row with `job_type='pipeline_run'`. **A
consumer must exist orchestrator-side** — a small daemon that claims that job
(via the `claim_next_job` RPC) and runs `python orchestrator/run_pipeline.py
--tenant <slug>`. Until that exists, the button enqueues correctly but nothing
actions the run; the orchestrator can still be run manually from the CLI.

## Known limitations (inherited / MVP)

- Super-admin **impersonation** ("Page") renders the tenant Dashboard, but with
  RLS enabled the anon session cannot read another tenant's rows, so data may be
  empty under impersonation. A real elevated/member session for the tenant would
  be needed for full live data.
- Hard-coded super-admin credentials and the publishable key in the bundle are
  MVP-security items preserved from the reference build.
- Cost figures are estimates (`COST_RATES`), not metered.
