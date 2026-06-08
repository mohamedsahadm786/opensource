# update.md — Alluvi Console Web Build (handoff / context doc)

> **Purpose:** read this first when resuming work on the web app. It captures what
> was built, why, how it wires to the backend, the full file map, how to run it,
> how it was deployed, current status, and known issues. The pipeline backend
> (`orchestrator/`, schema `db/migrations/001`) already existed; this effort added
> the **web UI layer** plus the additive backend glue (Storage RLS, an audit
> table, a Vault RPC, and 4 Edge Functions).

---

## 1. What this is

A React web console (in `web/`) — the human control panel in front of the
open-source Alluvi pipeline (RunPod GPUs + ComfyUI, orchestrated by `orchestrator/`,
backed by Supabase Postgres + Storage + Vault).

It is a faithful re-skin of a previous **FAL-API + n8n** console
(reference repo: `mohamedsahadm786/Add_automation_web`, cloned alongside as
`../reference-web`). The **design + navigation were cloned 1:1**; only the data
layer was rewired to point at OUR schema, Storage, and Vault instead of FAL/n8n.

**Hard rule:** the web and the pipeline never call each other's code — they meet
only through the Supabase DB + a few Edge Functions. Browser uses the
**anon/publishable key with RLS**; the **service key lives only inside the Edge
Functions** (verified absent from the built bundle).

Tech: React 18.3, Vite 6, `@supabase/supabase-js` v2, lucide-react, hand-rolled
CSS (one `src/index.css`, ported verbatim from the reference). No Tailwind.

---

## 2. The Supabase project

- Project ref: **`ylmtphqqhhgfjurqjujs`** (dashboard tab name: `alluvi-prod`).
- `web/.env.local` (NOT committed — `.local` is gitignored):
  ```
  VITE_SUPABASE_URL=https://ylmtphqqhhgfjurqjujs.supabase.co
  VITE_SUPABASE_ANON_KEY=sb_publishable_X2tdejX42qgwFchECQmF3A_aYjd7OCA
  ```
  (Publishable/anon key — safe in the browser. NEVER put the service/secret key here.)

---

## 3. How the web connects to the schema (the contract)

- **RLS everywhere.** Every table is scoped by `tenant_id = public.current_tenant_id()`,
  which reads `app_metadata.tenant_id` from the logged-in user's JWT.
- **A member's JWT carries `tenant_id`** — set server-side by the `provision-tenant`
  Edge Function right after signup, then the client refreshes the session.
- **Only 4 operations need elevation** (service role, via Edge Functions);
  everything else the browser does directly with the anon key (RLS already allows it):
  direct-write tables = `products`, `tiktok_accounts`, `tenant_pipeline_config`,
  `asset_ratings`, `media_assets`, `jobs`; direct-read = `outputs`, `videos`,
  `scenarios`, engine tables, the tenant's own `tenants` row.
- **Files → private Storage buckets** (`products/portraits/images/audio/videos`),
  path prefix `'<tenant_id>/...'`. DB stores only a `media_assets` row (bucket+path);
  the browser mints short-lived **signed URLs** to display them.
- **Secrets → Vault.** `tenants.anthropic_secret_name` holds only the ref.

### Write-map (which screen writes what)
| Screen | Writes |
|---|---|
| Signup | `tenants` + `tenant_members` (via `provision-tenant`); JWT `app_metadata.tenant_id` |
| Tenant Setup / Settings | `tenants` (brand_config/script_company_info/script_directives/gpu_*); Anthropic key → Vault (via `store-tenant-secret`) |
| Product | `products` (1/tenant) + `media_assets` + photo → `products` bucket |
| Accounts | `tiktok_accounts` (+ optional voice → `audio` bucket + `media_assets`) |
| Run settings | `tenant_pipeline_config` |
| Run button | enqueues a `jobs` row (`job_type='pipeline_run'`) via `trigger-pipeline` |
| Rate generation | `asset_ratings` (one row per `output_id`; the 25-id rubric contract) |
| Publishing / Analytics / Engine | read-only |
| Super-admin | all cross-tenant data via `admin-data` (service role) |

### Rating contract (DO NOT change the keys — the learning engine reads them)
Upsert one `asset_ratings` row per `output_id`:
```jsonc
{ tenant_id, output_id, decision: 'accept'|'reject'|'flag',
  image_rated, video_rated, rated_by,
  image: { gates:{<id>:{result, [auto], [disputed]}}, scores:{<id>:1..5}, [notes] },
  video: { gates:{<id>:{result}},                     scores:{<id>:1..5}, [notes] } }
```
Scores omitted when unrated. IDs (verbatim): image gates `img_product_present,
img_color_fidelity, img_shape_dimensions, img_brand_text, img_productname_text,
img_grip_logic, img_persona_identity, img_scene_logic`; image scores
`img_scene_adherence, img_aesthetic, img_detail_realism, img_lighting,
img_ad_worthiness`; video gates `vid_product_identity, vid_persona_identity,
vid_no_artifacts, vid_grip_maintained, vid_brand_text_motion`; video scores
`vid_motion_smoothness, vid_temporal_stability, vid_dynamic_degree, vid_camera_motion,
vid_physical_plausibility, vid_imaging_quality, vid_hook_strength`. Auto image gates
(dispute toggle, store `auto:true`+`disputed`): `img_product_present, img_color_fidelity,
img_brand_text, img_productname_text, img_persona_identity`. Source of truth:
`web/src/lib/ratingConfig.js`.

---

## 4. What was added to the backend (ADDITIVE — nothing existing was altered)

### New migrations (`db/migrations/`)
- **`017_storage_policies.sql`** — per-tenant `storage.objects` RLS for the 5 buckets,
  scoped to the caller's `<tenant_id>/` folder.
  ⚠️ Do NOT include `alter table storage.objects enable row level security;` — the SQL
  editor role isn't the table owner (errors `42501`); RLS is already on by default.
- **`018_impersonation_events.sql`** — super-admin audit table (RLS on, no anon policy;
  only the service-role `admin-data` function touches it).
- **`019_set_secret_rpc.sql`** — security-definer `public.set_tenant_anthropic_key(tenant_id, key)`
  that creates/rotates the Vault secret + sets `tenants.anthropic_secret_name`.

### New Edge Functions (`supabase/functions/`, Deno, service role)
- **`provision-tenant`** — after signup: create `tenants` + owner `tenant_members`,
  stamp `app_metadata.tenant_id`. Client then refreshes the session.
- **`store-tenant-secret`** — calls the `019` RPC to put the Anthropic key in Vault.
- **`trigger-pipeline`** — inserts a `jobs` row (`job_type='pipeline_run'`).
- **`admin-data`** — gated by `ADMIN_SECRET`; powers the super-admin console
  (actions: `overview`, `set_status`, `set_profile`, `log_event`, `list_events`).
- `_shared/cors.ts` — shared CORS/JSON helpers.

---

## 5. Frontend file map (`web/src/`)

- **lib/**: `supabase.js` (anon client), `constants.js` (env + hard-coded super-admin
  creds + dropdowns), `ratingConfig.js` (25-id rubric), `assets.js` (signed URLs +
  download), `adminApi.js` (calls `admin-data`), `audit.js` + `tenantAdmin.js`
  (super-admin actions via `admin-data`; status maps suspend→`paused`, remove→`disabled`),
  `cost.js`, `utils.js`, `jsonField.js` + `productForm.js` (jsonb form helpers).
- **hooks/**: `useAuth` (super-admin flag + member auth + auto-provision + session
  refresh), `useTenant` (tenants + members + Vault key + onboarded flag in
  `tenants.settings.onboarded`), `useProduct`, `useAccounts` (DB-cascade delete),
  `usePipelineConfig`, `usePipelineRun` + `useRunProgress` (durable-queue job),
  `usePublishing` (signed URLs from `media_assets`), `useAssetRating` (contract shape),
  `useSettings` (Vault key + GPU), `useEngine` (read-only), `useSuperAdmin` +
  `useAuditLog` (via `admin-data`), `useAnalytics`, `useTheme`.
  (Removed: old `useRunConfig.js`.)
- **components/**: shell (`App`, `Dashboard`, `Sidebar`, `Topbar`, `Stats`, `Modal`,
  `BrandMark`, `ThemeToggle`, `LoginScreen`); tenant views (`AccountsPanel`,
  `AccountFormModal` [+ voice/identity], `DeleteModal`, `ProductPanel` + `ProductFields`,
  `PublishingPanel` [signed URLs], `AnalyticsPanel`, `EnginePanel`, `SettingsPanel`,
  `RatingWorkspace` [decision↔triage], `RunControl`, `RunConfigModal` [pipeline config],
  `TenantSetup` [brand+script+key+product]); super-admin (`SuperAdminApp`,
  `SuperAdminSidebar`, `SuperAdminOverview`, `TenantsList`, `TenantDetail`,
  `TenantConfigModal`, `TenantActionModal`, `ImpersonationBanner`, `AuditLogPanel`).

Navigation (tenant Dashboard): Accounts · Product · Publishing · Analytics ·
Learning engine · Settings. Super-admin: Overview · Tenants · Activity.

---

## 6. How to RUN it locally (PowerShell)

```powershell
cd D:\video_automation_prototype\opensource\alluvi-clean\web
# one-time: create env file (publishable key, no BOM)
"VITE_SUPABASE_URL=https://ylmtphqqhhgfjurqjujs.supabase.co`nVITE_SUPABASE_ANON_KEY=sb_publishable_X2tdejX42qgwFchECQmF3A_aYjd7OCA" | Set-Content .env.local
npm install     # first time only
npm run dev     # http://localhost:5173
```
- Blank white page = `.env.local` missing/not loaded → recreate it and **restart** `npm run dev`, then hard-refresh (Ctrl+Shift+R).
- Super-admin login: **`admin` / `Alluvi@admin@1512`**.

---

## 7. How it was DEPLOYED (already done once on `alluvi-prod`)

**Migrations** — pasted into Supabase Dashboard → SQL Editor → Run, in order:
`017` (corrected, no `alter table`), `018`, `019`. (Base `001` was already applied.)

**Edge Functions** — from repo root `D:\...\alluvi-clean` with the Supabase CLI:
```powershell
npx supabase login                       # (plain — NO '!' prefix in a real terminal)
npx supabase link --project-ref ylmtphqqhhgfjurqjujs
npx supabase functions deploy provision-tenant   --no-verify-jwt
npx supabase functions deploy store-tenant-secret --no-verify-jwt
npx supabase functions deploy trigger-pipeline    --no-verify-jwt
npx supabase functions deploy admin-data          --no-verify-jwt
npx supabase secrets set ADMIN_SECRET="Alluvi@admin@1512"   # MUST equal ADMIN_PASS in src/lib/constants.js
```
("WARNING: Docker is not running" during deploy is harmless — Docker is only for
local function serving.) Also: turn OFF Supabase Auth "Confirm email" so signup →
immediate session.

---

## 8. Current status (as of this build)

- ✅ Web app builds clean (`npm run build`), boots, login works, design intact.
- ✅ All 4 Edge Functions deployed to `ylmtphqqhhgfjurqjujs`; `ADMIN_SECRET` set.
- ✅ Super-admin console loads (Overview shows zeros until tenants exist).
- ⏳ Confirm migrations `017`/`018`/`019` all ran green (needed for uploads / audit /
  Vault key respectively).
- ⏳ Not yet tested end-to-end: member signup → setup → onboard account → rate.

---

## 9. Known issues / next steps

1. **Run button needs a backend consumer.** `trigger-pipeline` only enqueues a
   `jobs` row (`job_type='pipeline_run'`). A small orchestrator-side daemon must
   claim it (via `claim_next_job`) and run `python orchestrator/run_pipeline.py
   --tenant <slug>`. Until then, start runs from the CLI. **(Not built yet.)**
2. **Super-admin impersonation ("Page")** renders the tenant Dashboard, but with RLS
   on, the anon session can't read another tenant's rows, so data may be empty under
   impersonation. Needs a real elevated/member session to show live data.
3. **MVP security (preserved from reference):** hard-coded super-admin password in
   `src/lib/constants.js`; publishable key in the bundle. Rotate / move to real auth
   before any public launch. Lock Edge Function CORS to the prod origin (currently `*`).
4. **Cost is estimated**, not metered (`COST_RATES` in `constants.js`).
5. Nothing here has been committed to git yet — `web/`, the new migrations, and
   `supabase/functions/` are untracked in the working tree.

---

## 10. Reference material
- `web/README.md` — condensed setup/run notes.
- `../reference-web/rule.md` — the original console's full feature spec (design source).
- `db/migrations/001_alluvi_schema.sql` — the full backend schema (consolidated thru 016).
