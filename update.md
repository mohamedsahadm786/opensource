# update.md — Alluvi Console Web Build (handoff / context doc)

> **Purpose:** read this first when resuming work on the web app. It captures what
> was built, why, how it wires to the backend, the full file map, how to run it,
> how it was deployed, current status, and known issues. The pipeline backend
> (`orchestrator/`, schema `db/migrations/001`) already existed; this effort added
> the **web UI layer** plus the additive backend glue (Storage RLS, an audit
> table, a Vault RPC, and Edge Functions).

---

## v3 — operational + UX hardening (2026-06-09 session — READ THIS FIRST)

> This session took the v2 build from "compiles" to "runs end-to-end, web-driven."
> It made the Run button actually launch the pipeline, added live progress, fixed
> several reference-leftover bugs, added real super-admin impersonation + forgot-
> password, and wired the persona appearance control. **Where this disagrees with
> v2/v1 below, this wins.** Everything here is committed + pushed to `main`
> (`github.com/mohamedsahadm786/opensource`); the full README + `PROMPT_TUNING_MAP.md`
> are the canonical references now.

### Project facts (so you don't have to ask)
- Supabase project ref **`ylmtphqqhhgfjurqjujs`**. Super-admin login **`admin` / `Alluvi@admin@1512`**.
- Test tenant: **`sahad-c6fd0d`** (`6c7bf137-aeae-4914-8242-c107254c1156`), product **ALLUVI Tirzepatide**,
  one account **@sahad** (persona already generated, has videos).
- The orchestrator runs on the owner's PC; `orchestrator/.env` (gitignored) holds `SUPABASE_SECRET_KEY`,
  `GATEWAY_URL=https://<POD_ID>-8191.proxy.runpod.net`, `GATEWAY_API_KEY`, `OPUS_MODEL=claude-opus-4-7`.
- **All 6 Edge Functions are deployed + ACTIVE:** `provision-tenant`, `store-tenant-secret`,
  `trigger-pipeline`, `admin-data`, `convert-briefs`, `generate-qc-brief`.

### What got built / fixed this session
1. **convert-briefs deployed + upgraded** — three type-specific system prompts (`PRODUCT_SYS` /
   `COMPANY_SYS` / `SCRIPT_SYS`) with per-field guidance + worked examples, `max_tokens 4096`. Target
   JSON shapes unchanged. (`supabase/functions/convert-briefs/index.ts`.)
2. **`generate-qc-brief` Edge Function (NEW, deployed)** — Claude-vision QC ground-truth from the
   product photo; mirrors `orchestrator/generate_qc_brief.py` (rules embedded). Called in the
   Finish-setup chain after `convert-briefs` (non-fatal) and on **Product re-save when the photo is
   swapped** (`{force:true}`). Helpers in `web/src/lib/briefs.js`.
3. **`tenants_member_update` RLS policy** (live on DB + added to `001_alluvi_schema.sql`). Root cause
   of the "Cannot coerce the result to a single JSON object" error on Finish-setup: `tenants` had no
   UPDATE policy, so the member's `update tenants …` hit 0 rows. Members can now update their own row.
4. **`orchestrator/run_worker.py` (NEW)** — the job consumer that makes the web **Run** button live.
   Claims `pipeline_run` jobs via `claim_next_job`, runs `python run_pipeline.py --tenant <slug>`,
   sets job status (fail-fast). **UTF-8 subprocess fix** (`PYTHONUTF8=1`) — without it the pipeline's
   `✓` glyphs crashed under Windows cp1252. One worker per GPU.
5. **`run_pipeline.py` — live stage markers + toggle wiring.** Writes a `stage_executions` row at each
   step (`phasea/step1/step2/qc/step3/script/video`) so the web shows the live stage. And
   `STEP3_ENABLED`/`QC_ENABLED` now read from `tenant_pipeline_config.step_3_enabled`/`qc_enabled`
   (env vars are the fallback default). **Option A coupling: Stage 3 OFF ⇒ no realism AND no video**
   (Phase C anchors on the realism image).
6. **Web run progress rewired** — `useRunProgress` now drives completion from **`jobs.status`**
   (`succeeded`/`failed`), not the old 5-min idle heuristic (which falsely showed "complete" mid-video).
   Polls the latest `stage_executions` for the current stage; `RunControl` shows a friendly label
   ("Compositing product (Qwen)…"); 8s poll.
7. **Run gated until configured** — `num_videos_per_account` + `video_duration_seconds` now start
   empty and are required; Run stays disabled (tooltip) until they're set + saved
   (`usePipelineConfig`, `RunConfigModal`).
8. **Forgot / reset password** — `useAuth` (`resetPassword`/`updatePassword` + `recovery`), `App`
   routes the recovery link to a reset form, `LoginScreen` has `forgot`/`reset` modes + the link.
   (Needs the app origin in Supabase Auth → URL Configuration.)
9. **Publishing debug split + fix** — one Debug button → **Image debug** + **Video debug**
   (`DebugModal` `mode` prop). Fixed the empty-prompts bug: `useOutputDebug` now joins
   `image_generations` by **`output_asset_id`** (= `outputs.step1/2/3_asset_id`), because this pipeline
   leaves `stage_executions`/`stage_execution_id` empty. `llm_calls` dropped from per-output debug
   (no per-output link).
10. **Analytics QC fix** — counted `qc_status === 'pass'` (reference value) but this pipeline writes
    **`'passed'`/`'exhausted'`**. Now correct → pass rate / video conversion / top scenarios populate.
11. **Super-admin FULL impersonation** — `admin-data` `impersonate` action mints a magic-link for the
    tenant **owner**; the browser `verifyOtp`s into a real tenant session, so RLS passes and the exact
    tenant Dashboard renders with full data + actions. `App` shows the banner + Dashboard; Exit restores
    the admin. (`useAuth.impersonate`/`exitImpersonation`.) Fixes the old "shows the setup page" bug.
12. **Removed "Plan"** everywhere in super-admin (`TenantDetail`, `TenantConfigModal`).
13. **Run-settings modal overflow fixed** — `.field-row` → `grid-template-columns: repeat(2, minmax(0,1fr))`
    + `.field { min-width:0 }`. "Specific accounts" supports **multiple** (checkbox list → `target_account_ids`).
14. **Persona appearance control (verified working)** — the account form's "Identity factors (JSON)" is
    now plain-English **"Appearance / body type"** (`identity_factors.appearance`). `run_portrait.py`
    passes it to Opus as `appearance_request`; `rules/phaseA.md` + `TASK_BLOCK` honor a requested
    heavier/plus-size build (kept compliance: 21+, not emaciated). **Before this, `identity_factors`
    was dead data read by nothing** — that's why all portraits were slim. Verified: a "heavier/fat
    build" request flowed brief → Opus prompt → FLUX render.
15. **README rewritten** (architecture, phases/models/params, ports, A→Z run + deploy) and
    **`PROMPT_TUNING_MAP.md`** added (per-stage prompt tuning reference).

### The ONE backend task still NOT done (flag to owner)
- **GPU host from DB.** The orchestrator still reads `GATEWAY_URL` from `orchestrator/.env`, NOT the
  tenant's `gpu_host`/`gpu_url_template`. So the web Settings GPU field is **stored but not used by
  runs** yet. On every pod restart you must update `.env` `GATEWAY_URL` and restart `run_worker.py`.
  Wiring this (build the URL from the tenant row) is the last piece for true multi-tenant/multi-pod.

### How a run works now (the loop)
Web **Run** → `trigger-pipeline` enqueues `jobs(pipeline_run)` → **`run_worker.py`** (must be running on
the PC) claims it → `run_pipeline.py --tenant <slug>` drives the RunPod gateway (`:8191`) → writes
`stage_executions` (live progress) + outputs/videos → web pill shows the stage and flips to complete on
`jobs.status='succeeded'`. In production, run `run_worker.py` as an always-on service (NSSM/systemd/pm2).

### Verifying changes (the methods used this session)
- **Stage progress / which stages ran:** `select stage_name, created_at from stage_executions where
  tenant_id=… and created_at > now() - interval '20 minutes' order by created_at;`
- **Toggle test (QC/Stage-3 off):** no `qc`/`step3`/`video` markers; `outputs.qc_status` null,
  `step3_asset_id` null; video count unchanged; `v_tenant_exploration_progress.resolved_curated`
  unchanged (a step2-only output is NOT counted toward coverage — resolved = `step3_done` OR
  `qc_status in (passed,exhausted,skipped,fail,failed)`).
- **Appearance request reached the model:** `select user_message, parsed_json->'identity'->>'body_type',
  parsed_json->>'portrait_prompt' from llm_calls where purpose='phasea_prompt' order by created_at desc limit 1;`
- **Cross-tenant / job state:** query via the service key (curl `…/rest/v1/<table>` with the secret key),
  since RLS hides cross-tenant rows from the anon key.
- **Revert any change:** everything is committed; `git checkout -- <file>` restores the pushed version.

### Prompt tuning (where every prompt comes from)
See **README §8** and **`PROMPT_TUNING_MAP.md`**. Short version: every prompt = a generic **rule book**
(`orchestrator/rules/*.md`, edit → all tenants) + per-tenant **DB data** (briefs/product, edit → one
tenant), assembled by a **brain** (`run_*.py`). Edit a rule book → just re-run, no redeploy. Dialogue +
Wan motion are the same stage (`script.md`); realism strength/LoRA lives in the pod ComfyUI workflow.

---

## 0. v2 revision (previous)

The owner revised the brief ("Rebuild Brief v2"). Where v2 disagrees with v1, **v2 wins.**
Implemented:
- **Setup page = 7 plain-English fields only** (no company/slug/plan, **no JSON typing**):
  GPU host, Anthropic key, product brief, company brief, mask prompt, script brief, product photo.
  The **Save button is gated** until all are filled; on save it stores the raw briefs, stores the
  key in Vault, uploads the photo, runs the Claude conversion, marks onboarded, → Accounts.
- **Plain-English → Claude → JSON** via a NEW Edge Function **`convert-briefs`** (§5 contract): it
  reads the tenant's Vault key + the raw briefs and writes `products.name/product_info/packaging`,
  `tenants.script_company_info`, `tenants.script_directives`. Raw briefs are also kept
  (`tenants.company_brief_text`/`script_brief_text`, `products.product_brief_text`).
- **Multiple specific accounts**: Run config uses `tenant_pipeline_config.target_account_ids`
  (jsonb array) with a multi-select checklist. (`target_account_id` single is legacy.)
- **QC retry count lives on the product** (`products.qc_max_retries`) — editable from Run settings.
- **Debug panel** (§7): per-output prompt inspector (image_generations / media_generations /
  llm_calls), opened from a "Debug" button in the Publishing lightbox.

These columns ALREADY exist in the consolidated `001` schema (the owner merged them), so **no new
column migrations were needed** — `017/018/019` (storage/impersonation/vault) are unchanged.

**Still backend tasks (NOT web):** the orchestrator building the gateway URL from the tenant's
`gpu_host`+`gpu_url_template`, and the Run-trigger consumer that actually launches the orchestrator
per tenant.

**New files for v2:** `supabase/functions/convert-briefs/`, `web/src/lib/briefs.js`,
`web/src/hooks/useOutputDebug.js`, `web/src/components/DebugModal.jsx`. Rewritten:
`TenantSetup.jsx`, `ProductFields.jsx`, `RunConfigModal.jsx`, `lib/productForm.js`; updated
`useProduct`, `useTenant`, `usePipelineConfig`, `ProductPanel`, `PublishingPanel`, `Dashboard`.

**Deploy the new function:** `npx supabase functions deploy convert-briefs --no-verify-jwt`
(optional: `npx supabase secrets set CONVERT_MODEL=claude-sonnet-4-6` — that's the default).

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
| Tenant Setup (v2, 7 plain-English fields) | `tenants` (gpu_host, company_brief_text, script_brief_text) + Vault key (via `store-tenant-secret`) + `products` (product_brief_text, mask_prompt, qc_max_retries) + photo → `products` bucket; then **`convert-briefs`** derives the JSON (`products.name/product_info/packaging`, `tenants.script_company_info/script_directives`) |
| Product page | `products` (product_brief_text, mask_prompt, qc_max_retries) + re-runs `convert-briefs` |
| Settings | `tenants.gpu_*`; Anthropic key → Vault |
| Accounts | `tiktok_accounts` (+ optional voice → `audio` bucket + `media_assets`) |
| Run settings | `tenant_pipeline_config` (incl. `target_account_ids` array) + `products.qc_max_retries` |
| Run button | enqueues a `jobs` row (`job_type='pipeline_run'`) via `trigger-pipeline` |
| Rate generation | `asset_ratings` (one row per `output_id`; the 25-id rubric contract) |
| Publishing (+Debug) / Analytics / Engine | read-only (`outputs`/`videos`/`media_assets`; Debug reads `image_generations`/`media_generations`/`llm_calls`) |
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
- **`convert-briefs`** (v2) — reads the tenant's Vault key + raw briefs, calls Claude
  (`CONVERT_MODEL`, default `claude-sonnet-4-6`), writes the §5 JSON columns.
- `_shared/cors.ts` — shared CORS/JSON helpers.

---

## 5. Frontend file map (`web/src/`)

- **lib/**: `supabase.js` (anon client), `constants.js` (env + hard-coded super-admin
  creds + dropdowns), `ratingConfig.js` (25-id rubric), `assets.js` (signed URLs +
  download), `adminApi.js` (calls `admin-data`), `audit.js` + `tenantAdmin.js`
  (super-admin actions via `admin-data`; status maps suspend→`paused`, remove→`disabled`),
  `cost.js`, `utils.js`, `briefs.js` (v2: calls `convert-briefs`), `productForm.js`
  (v2: plain-English product form helpers), `jsonField.js` (legacy helper).
- **hooks/**: `useAuth` (super-admin flag + member auth + auto-provision + session
  refresh), `useTenant` (tenants + members + Vault key + raw briefs + onboarded flag in
  `tenants.settings.onboarded`), `useProduct` (raw brief + mask + qc; preserves Claude
  fields), `useAccounts` (DB-cascade delete), `usePipelineConfig` (`target_account_ids`
  array), `usePipelineRun` + `useRunProgress` (durable-queue job), `usePublishing`
  (signed URLs from `media_assets`), `useAssetRating` (contract shape), `useOutputDebug`
  (v2: per-output prompts), `useSettings` (Vault key + GPU), `useEngine` (read-only),
  `useSuperAdmin` + `useAuditLog` (via `admin-data`), `useAnalytics`, `useTheme`.
  (Removed: old `useRunConfig.js`.)
- **components/**: shell (`App`, `Dashboard`, `Sidebar`, `Topbar`, `Stats`, `Modal`,
  `BrandMark`, `ThemeToggle`, `LoginScreen`); tenant views (`AccountsPanel`,
  `AccountFormModal` [+ voice/identity], `DeleteModal`, `ProductPanel` + `ProductFields`
  [v2 plain-English], `PublishingPanel` [signed URLs + Debug button], `DebugModal` [v2],
  `AnalyticsPanel`, `EnginePanel`, `SettingsPanel`, `RatingWorkspace` [decision↔triage],
  `RunControl`, `RunConfigModal` [v2 multi-account + qc-on-product],
  `TenantSetup` [v2: 7 plain-English fields, gated, runs convert-briefs]); super-admin
  (`SuperAdminApp`, `SuperAdminSidebar`, `SuperAdminOverview`, `TenantsList`,
  `TenantDetail`, `TenantConfigModal`, `TenantActionModal`, `ImpersonationBanner`,
  `AuditLogPanel`).

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
npx supabase functions deploy convert-briefs      --no-verify-jwt   # v2 (deploy this one)
npx supabase secrets set ADMIN_SECRET="Alluvi@admin@1512"   # MUST equal ADMIN_PASS in src/lib/constants.js
# optional: npx supabase secrets set CONVERT_MODEL=claude-sonnet-4-6   # default already
```
("WARNING: Docker is not running" during deploy is harmless — Docker is only for
local function serving.) Also: turn OFF Supabase Auth "Confirm email" so signup →
immediate session.

---

## 8. Current status (as of this build)

- ✅ Web app builds clean (`npm run build`), boots, login works, design intact.
- ✅ First 4 Edge Functions deployed to `ylmtphqqhhgfjurqjujs`; `ADMIN_SECRET` set.
- ✅ Super-admin console loads (Overview shows zeros until tenants exist).
- ✅ v2 implemented (plain-English briefs + `convert-briefs`, multi-account, qc-on-product,
  Debug panel); build passes.
- ⏳ **Deploy `convert-briefs`** (`npx supabase functions deploy convert-briefs --no-verify-jwt`)
  — required before the v2 setup page works (Save runs the Claude conversion).
- ⏳ Confirm migrations `017`/`018`/`019` all ran green (needed for uploads / audit /
  Vault key respectively).
- ⏳ Not yet committed to git: the v2 changes.
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
