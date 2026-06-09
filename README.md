# Alluvi — AI UGC Pipeline (Console + Orchestrator + GPU)

Alluvi turns a TikTok account's identity (gender, age, country, language) and one product into
**photoreal, lip-synced product videos**:

```
persona portrait  →  scene image  →  product composite  →  QC  →  realism  →  script  →  video
   (Phase A)            (Step 1)         (Step 2)         (gate)  (Step 3)   (Opus)   (Phase C)
```

It is built from **three independent planes that never call each other's code** — they meet only
through a shared **Supabase** database (plus a few Edge Functions). That separation is the most
important architectural fact: **the database is the integration contract.**

| Plane | Where it runs | Role | Talks to |
|---|---|---|---|
| **Web Console** (`web/`) | browser (React + Vite) | human control panel — onboarding, accounts, run settings, ratings, super-admin | Supabase (anon key + RLS) + Edge Functions |
| **Orchestrator** (`orchestrator/`) | **your PC / VS Code** (Python) | the "brain" — builds prompts with Claude, drives each stage, reads/writes Supabase (service key), calls the GPU gateway over HTTP | Supabase (service key) + RunPod gateway |
| **GPU pod** (RunPod) | RunPod container | the "muscle" — a gateway + worker + ComfyUI instances + per-stage services that run the actual diffusion / TTS / video models | (driven by the orchestrator) |

```
  ┌────────────┐        Supabase (Postgres + Storage + Vault)        ┌──────────────┐
  │ Web Console│◄──── anon key + RLS ────►  ▣ tables  ▣ buckets  ◄────│ Orchestrator │
  └────────────┘        Edge Functions (service role)                │  (your PC)   │
        ▲                                                             └──────┬───────┘
        │ signed URLs / live progress                                       │ HTTP (/portrait /scene
        │                                                                    │  /step2 /step3 /video)
        └──────────────────────────  outputs / videos  ◄────────────────────┤
                                                                             ▼
                                                                  ┌────────────────────┐
                                                                  │  RunPod GPU pod     │
                                                                  │  gateway :8191      │
                                                                  │  + worker + ComfyUI │
                                                                  └────────────────────┘
```

---

## 1. The pipeline — phases, stages, models, parameters

Everything is **DB-driven data + repo-file rules**: the orchestrator pulls the tenant's data from
Supabase, builds each prompt with **Claude Opus** (`OPUS_MODEL`, default `claude-opus-4-7`) using the
rule books in `orchestrator/rules/`, then enqueues a job on the GPU gateway and polls it to completion.

### Phase A — Portrait (one locked face per account)
| | |
|---|---|
| **Model** | **FLUX** (+ **PuLID** for identity injection) |
| **Brain** | Opus builds the portrait prompt from `tiktok_accounts` identity only (`rules/phaseA.md`) — no brand/product |
| **Output** | `personas.portrait_asset_id` (reused forever for that account) |
| **VRAM** | FLUX loads lazily on the first portrait, **freed after the batch** |

### Phase B — Image trio (per scenario, chained)
| Step | Model | Brain / inputs | Key parameters |
|---|---|---|---|
| **1 — Scene** | **PuLID** (ComfyUI) | Opus → `step_1_image_prompt` + `fal_pulid_params` from persona + `scenarios.spec` (`rules/step1.md`) | `id_weight` (PuLID identity, clamped **≤ 0.6**), `seed` (NUMERIC, overflow-safe), `cfg`, `num_inference_steps`, `width/height` |
| **2 — Product composite** | **Qwen-Image-Edit** (ComfyUI :8188) | Opus → `step_2_image_prompt` + `fal_qwen_params` from `products.packaging` — **`text_on_packaging` is rendered verbatim** onto the box (`rules/step2_qwen.md`) | `cfg`, `guidance`, `seed`, `max_sequence_length`, `target_size` |
| **QC gate** | **Claude vision** | Validates the composite against `products.qc_brief` (the vision ground-truth) | retries up to **`products.qc_max_retries`** (total attempts = 1 + this); pass → continue, exhaust → keep last + flag |
| **3 — Realism** | **RealVisXL / SDXL** (realism-service :8194) | Static realism pass; **`products.mask_prompt`** protects the product box (GroundingDINO/SAM2 box-protect) | fixed realism prompt + SDXL params (on the pod); only tenant input is `mask_prompt` |

After all scenarios for the account: **free the image stack (PuLID + Qwen + RealVisXL).**

### Phase C — Video (per finished realism image)
| | |
|---|---|
| **Models** | **F5-TTS** (voice) → **Wan** (video, ComfyUI :8188) → **LatentSync** (lip-sync, :8189) → merge |
| **Brain** | Opus builds the multi-shot script from `tenants.script_company_info` + `products.product_info` + `tenants.script_directives` (`rules/script.md`) |
| **Modes** | `multishot` (cut-based, N shots stitched) · `silentfirst` (continuous, lip-driven) |
| **Output** | `videos.final_video_asset_id` (mp4 in the `videos` bucket) |
| **VRAM** | **free the video stack (Wan + F5 + LatentSync)** after the batch |

### VRAM / GPU strategy (why it fits one GPU)
Models are **loaded lazily on first use and freed between phases** (`/free` on the gateway), so a
single GPU's VRAM is *reused* across phases instead of holding every model at once:
**FLUX** (Phase A) → freed → **PuLID + Qwen + RealVisXL** (Phase B) → freed → **Wan + F5 + LatentSync**
(Phase C) → freed. Pass `--no-free` to keep models resident (faster repeated runs, more VRAM held).

### Run-time knobs (`tenant_pipeline_config`, written by the web Run-settings page)
`num_videos_per_account` (= scenarios = images = videos) · `video_mode` · `video_duration_seconds`
(`num_shots = ceil(duration / shot_seconds)`) · `shot_seconds` · `intro/outro/tail_seconds` ·
`lips_expression` · `inference_steps` · `punch_in` · `threshold` · `seed` · `step_3_enabled` ·
`qc_enabled`. CLI flags override the config for testing.

---

## 2. The GPU pod — services & ports

The pod runs a **gateway** (the only port exposed publicly via the RunPod proxy) in front of several
ComfyUI instances and per-stage FastAPI services:

| Port | Service | Used by |
|---|---|---|
| **8191** | **Gateway** (job queue API) — *exposed via RunPod proxy* | the orchestrator (`GATEWAY_URL`) |
| 8188 | ComfyUI — **Qwen / Wan** | Step 2, Phase C video |
| 8189 | ComfyUI — **TTS / lip-sync** | Phase C (F5-TTS, LatentSync) |
| 8190 | ComfyUI — **realism** | Step 3 |
| 8192 | **stage1-service** | Step 1 (PuLID scene) |
| 8193 | **qwen-service** | Step 2 |
| 8194 | **realism-service** | Step 3 |
| 8195 | **video-service** | Phase C assembly |

The orchestrator only ever talks to **:8191**. On a pod restart the **only** value that changes is the
pod id — update `GATEWAY_URL` (see §5.4).

---

## 3. Data model (the contract)

```
tenants ──< tenant_members              tenants = company = brand (ONE product each)
   │                                    secrets → Vault (anthropic_secret_name)
   ├──< products (UNIQUE per tenant)    files   → private Storage buckets + signed URLs
   ├──< tiktok_accounts ──< personas ──< outputs ──< videos
   └──  tenant_pipeline_config          (run settings)
scenarios            GLOBAL (shared library; engine selects from it)
media_assets         every blob (bucket + path); rows reference it
asset_ratings        human RLHF → attribute_stats → learning engine (exploration → active)
jobs                 durable queue (pipeline_run) — claim_next_job()
stage_executions     live per-stage progress (web polls this)
```
- **RLS everywhere**, scoped by `tenant_id = current_tenant_id()` (read from the JWT). The browser uses
  the **anon key**; the orchestrator + Edge Functions use the **service key** (bypasses RLS).
- **Edge Functions** (Deno, service role): `provision-tenant`, `store-tenant-secret`, `convert-briefs`
  (plain-English briefs → JSON), `generate-qc-brief` (vision QC ground-truth), `trigger-pipeline`
  (enqueues a run), `admin-data` (super-admin + impersonation).

---

## 4. Repository structure

```
alluvi-clean/
├── web/                       React + Vite console (anon key + RLS)
│   └── src/{components,hooks,lib,contexts}
├── orchestrator/              Python "brain" — runs on your PC
│   ├── run_pipeline.py        THE single command (Phase A→B→C)
│   ├── run_worker.py          job consumer (makes the web Run button live)
│   ├── run_portrait/scene/step2/step3/video.py   per-stage brains
│   ├── script_gen.py · qc.py · engine.py · tuning.py
│   ├── generate_qc_brief.py   one-time QC ground-truth from the product photo
│   └── rules/                 phaseA / step1 / step2_qwen / qc_brief_builder / script .md
├── supabase/functions/        Edge Functions (Deno, service role)
└── db/migrations/             001_alluvi_schema.sql (+ 017/018/019)
```

---

## 5. Running it — A to Z

You operate **three things in order**: ① the **GPU pod** (RunPod), ② the **orchestrator worker** (your
PC / VS Code), ③ the **web console**.

### 5.1 Prerequisites (one-time)
- A **Supabase** project with `001_alluvi_schema.sql` + `017/018/019` applied, the 5 Storage buckets,
  the global `scenarios` seeded, and the Edge Functions deployed (`npx supabase functions deploy <name> --no-verify-jwt`).
- `orchestrator/.env` (gitignored — never commit):
  ```ini
  SUPABASE_URL=https://<project>.supabase.co
  SUPABASE_SECRET_KEY=sb_secret_...        # service role — bypasses RLS
  GATEWAY_URL=https://<POD_ID>-8191.proxy.runpod.net
  GATEWAY_API_KEY=...                      # gateway bearer token
  OPUS_MODEL=claude-opus-4-7
  ```
- Python venv in `orchestrator/`: `python -m venv venv` then `pip install -r requirements.txt`.

### 5.2 ① Start the GPU pod (RunPod)

> **In the RunPod console, start the pod and EXPOSE HTTP port `8191`. Note the new pod id and update `GATEWAY_URL` on your PC accordingly (see §5.4).**

On the pod (boot the model stack, then keep three services running):

```bash
bash /workspace/start_pipeline.sh

# three terminals:
cd /workspace/alluvi-gateway && source /workspace/ai-toolkit/venv/bin/activate && uvicorn app:app --host 0.0.0.0 --port 8191
cd /workspace/alluvi-gateway && source /workspace/ai-toolkit/venv/bin/activate && python worker.py        # expect types=[...,'video']
cd /workspace/video-service  && source /workspace/ai-toolkit/venv/bin/activate && ALLUVI_REPO=/workspace/alluvi-clean nohup python app.py > /workspace/video-service/service.log 2>&1 & sleep 4 && curl -s http://127.0.0.1:8195/health
```

**Health check — every service must be up before a run:**

```bash
curl -s http://127.0.0.1:8188/system_stats >/dev/null && echo "8188 OK (Qwen/Wan)"     || echo "8188 DOWN"
curl -s http://127.0.0.1:8189/system_stats >/dev/null && echo "8189 OK (TTS/lipsync)"  || echo "8189 DOWN"
curl -s http://127.0.0.1:8190/system_stats >/dev/null && echo "8190 OK (realism)"      || echo "8190 DOWN"
curl -s http://127.0.0.1:8192/health >/dev/null && echo "8192 OK (stage1)"             || echo "8192 DOWN"
curl -s http://127.0.0.1:8193/health >/dev/null && echo "8193 OK (qwen-service)"       || echo "8193 DOWN"
curl -s http://127.0.0.1:8194/health >/dev/null && echo "8194 OK (realism-service)"    || echo "8194 DOWN"
curl -s http://127.0.0.1:8195/health >/dev/null && echo "8195 OK (video-service)"      || echo "8195 DOWN"
curl -s http://127.0.0.1:8191/health >/dev/null && echo "8191 OK (gateway)"            || echo "8191 DOWN"
```

> ### 🔴 IMPORTANT — confirm the proxy from your PC before doing anything else:
> ```bash
> curl https://<NEW_POD_ID>-8191.proxy.runpod.net/health
> ```
> **If this does not return OK, the orchestrator cannot reach the GPU and every run will fail. Fix the pod / `GATEWAY_URL` first.**

### 5.3 ② Start the orchestrator worker (your PC — VS Code)

This is the consumer that makes the web **Run** button actually run the pipeline. Open a terminal in
VS Code and run:

> ```powershell
> cd D:\video_automation_prototype\opensource\alluvi-clean\orchestrator
> .\venv\Scripts\Activate.ps1
> python run_worker.py
> ```

Leave it running. It polls the `pipeline_run` job queue every ~5s; when the web enqueues a run it
claims it and executes `run_pipeline.py --tenant <slug>` (one run at a time = one GPU).

**Run directly from the CLI (no web) for testing:**
```powershell
python run_pipeline.py --tenant <slug> --scenarios 1                      # DB-driven (uses run settings)
python run_pipeline.py @handle --scenarios 1 --video-mode silentfirst --duration 5 --shot-seconds 5
python run_pipeline.py @handle --scenarios 1 --skip-videos                 # images only
```

### 5.4 ③ Use the web console
`cd web && npm install && npm run dev` → http://localhost:5173. Member: sign up → **Setup** (7
plain-English fields; Claude derives the JSON + QC brief) → **Accounts** (onboard, gender required) →
**Run settings** (videos/account + duration required) → **Run**. The Run pill shows the **live stage**
(portrait → scene → Qwen → QC → realism → script → video) and flips to "Pipeline complete" when the
job succeeds. Browse results under **Publishing** (Image/Video debug shows the exact prompts).

> **On every pod restart the pod id changes.** Update `GATEWAY_URL` in `orchestrator/.env` to
> `https://<NEW_POD_ID>-8191.proxy.runpod.net` and **restart `run_worker.py`**. (The tenant's
> `gpu_host` in web Settings is stored for the future DB-driven gateway; today the orchestrator reads
> `GATEWAY_URL` from `.env`.)

---

## 6. Deployment (production)

The web Run button **only works while `run_worker.py` is running** and reachable to the pod. In
production, don't run it by hand — run it as an **always-on, auto-restarting service** on a machine
that has this repo + `orchestrator/.env` + network access to the RunPod gateway (it does **not** need
its own GPU; it just calls the remote pod):

- **Windows:** wrap it as a service with **NSSM**, or a Task Scheduler task "at startup, restart on failure".
- **Linux:** a **`systemd`** unit with `Restart=always`, or **pm2** / **supervisor**.
- **Docker:** a container with `restart: always`.

Flow in production: tenant clicks **Run** → `trigger-pipeline` Edge Function enqueues a `jobs` row →
the always-on worker claims it via `claim_next_job` → runs the pipeline against the live pod → the
web's progress polling lights up and flips to complete. One worker per GPU.

Also: turn **OFF** Supabase Auth "Confirm email" (so signup → immediate session), and add your web
origin to **Auth → URL Configuration** (needed for password-reset / impersonation links).

---

## 7. Security notes (MVP posture — harden before public use)
- Super-admin uses **hard-coded creds** in `web/src/lib/constants.js` (and `ADMIN_SECRET` on the
  Edge Functions). Rotate / move to real auth before any public launch.
- Super-admin **impersonation acts fully as the tenant** (mints a real session) — intended for an
  internal tool sent to your own operators.
- The browser ships the **publishable/anon key** (safe — RLS gates rows). The **service key lives only
  in `.env` / Edge Function secrets** and must never reach the browser or git.
