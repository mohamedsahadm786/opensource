-- ============================================================================
-- ALLUVI PIPELINE — COMPLETE CURRENT SCHEMA  (consolidated through migration 016)
-- ----------------------------------------------------------------------------
-- This is the FULL up-to-date schema: migration 001 with every later DDL change
-- merged in (002 gpu config, 005 job-queue fields, 007 claim_next_job, 008 vault
-- RPC, 010 drop do_dont + products qc_brief, 011 voice refs + tenant_pipeline_config,
-- 012 script_company_info/script_directives/product_info, 013 engine tables + view,
-- 015 exploration-gate view, 016 tuning fields, 017 raw briefs, 018 specific-account ids). Run top-to-bottom ONCE on a NEW,
-- EMPTY Supabase project (Pro). Safe to re-run (IF NOT EXISTS / drop-if-exists).
-- Structure only — tenant DATA seeds (accounts, scenarios, brand/product content,
-- config + learning-state rows) live in their own migration files, not here.
-- ----------------------------------------------------------------------------
--
-- LOCKED DESIGN DECISIONS (this revision):
--   1. One tenant = one company = one brand = ONE product. products has a
--      UNIQUE(tenant_id) constraint enforcing a single product per signup.
--      Brand/company creative config lives ON the tenant (no separate brands table).
--   2. scenarios is GLOBAL — no tenant_id. Every tenant shares the same library.
--      The full scenario JSON lives in spec (jsonb), so any extra columns in your
--      example are absorbed automatically; category/archetype/difficulty are kept
--      as denormalized filter columns only.
--   3. The ONLY tenant-supplied realism text is products.mask_prompt. All other
--      realism settings (denoise/cfg/LoRA/pos-neg) stay hardcoded on the pod —
--      not modelled here.
--   4. ALL primary keys are uuid (gen_random_uuid()).
--   5. Fully normalized, enterprise-grade: central media_assets registry for every
--      blob, full per-stage audit, every prompt captured.
--
--   * Multi-tenancy = shared schema + tenant_id + RLS. The pipeline uses the
--     SECRET key (bypasses RLS) so it is unaffected; RLS guards the future web app.
--   * Secrets (Anthropic keys, GPU tokens) = Supabase VAULT, referenced by name.
--   * Files = Supabase STORAGE; DB stores only bucket+path (media_assets).
--   * GPU endpoints = the CHANGING host lives ONCE on the tenant (gpu_host +
--     gpu_url_template); service_endpoints holds only the stable per-stage port.
--     On RunPod restart you update ONE field (tenants.gpu_host). Migrating to your
--     own GPU box = change gpu_url_template + gpu_host once; ports stay.
--   * seed columns are NUMERIC (fixes the PuLID > bigint overflow bug).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 0. EXTENSIONS
-- ---------------------------------------------------------------------------
create extension if not exists pgcrypto;          -- gen_random_uuid()
create extension if not exists supabase_vault;     -- Vault (usually already on)

-- ---------------------------------------------------------------------------
-- 1. RLS HELPER — reads tenant_id from the logged-in user's JWT app_metadata.
--    (The service/secret key bypasses RLS entirely, so the pipeline ignores this.)
-- ---------------------------------------------------------------------------
create or replace function public.current_tenant_id()
returns uuid language sql stable as $$
  select nullif(((auth.jwt() -> 'app_metadata') ->> 'tenant_id'), '')::uuid
$$;

-- ============================================================================
-- 2. TENANTS  (= company = brand; holds identity, access, secrets refs, brand config)
-- ============================================================================
create table if not exists public.tenants (
  id                    uuid primary key default gen_random_uuid(),
  name                  text not null,
  slug                  text unique not null,
  email                 text,
  status                text not null default 'active',     -- active | paused | disabled
  plan                  text,
  -- Anthropic key lives in Vault; store only the reference name here.
  -- Convention: secret name = 'anthropic:'||tenants.id
  anthropic_secret_name text,
  -- brand/company creative config (was brand.yaml): voice, vocabulary,
  -- target_customer, differentiation_pillars, market_context, visual_identity...
  brand_config          jsonb not null default '{}'::jsonb,
  -- video/dialogue config (was alluvi_information.json): system_identity,
  -- brand_personality, dialogue_generation_rules, scene_generation_system...
  video_config          jsonb not null default '{}'::jsonb,
  -- script-generation inputs (migration 012): per-tenant company/brand knowledge + directives
  script_company_info   jsonb,                               -- system_identity, brand_personality, marketing_language_engine, ...
  script_directives     jsonb,                               -- dialogue_generation_rules + ai_generation_priorities
  -- raw plain-English briefs from the signup form (migration 017); Claude converts these into the jsonb above
  company_brief_text    text,
  script_brief_text     text,
  -- GPU connection (the part that changes on RunPod restart lives HERE, once):
  gpu_provider          text not null default 'runpod',      -- runpod | own_gpu
  gpu_host              text,                                 -- RunPod pod-id, or your server host/IP (the CHANGING value)
  gpu_url_template      text not null default 'https://{host}-{port}.proxy.runpod.net',
  settings              jsonb not null default '{}'::jsonb,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

-- Maps Supabase auth.users <-> tenant (future web app + RLS).
create table if not exists public.tenant_members (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  user_id     uuid not null,                                 -- references auth.users(id)
  role        text not null default 'member',                -- owner | admin | member
  created_at  timestamptz not null default now(),
  unique (tenant_id, user_id)
);

-- ============================================================================
-- 3. SERVICE ENDPOINTS  (dynamic per-stage GPU config; RunPod now, own GPU later)
--    ONE ROW PER (tenant, stage_key). Stores only the STABLE per-stage port.
--    Effective URL is built at call time:
--      if url_override is set            -> url_override
--      else  tenants.gpu_url_template, with {host}=tenants.gpu_host and {port}=port
--    So a pod restart only needs tenants.gpu_host updated (one field, not N rows).
-- ============================================================================
create table if not exists public.service_endpoints (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references public.tenants(id) on delete cascade,
  -- gateway | portrait | stage1_pulid | stage2_qwen | stage3_realism | tts | wan | lipsync | merge
  stage_key        text not null,
  provider         text not null default 'runpod',           -- runpod | own_gpu | other
  port             int,                                       -- the STABLE per-stage port (8188, 8189, ...)
  url_override     text,                                      -- full URL if this stage is NOT on the tenant's gpu_host
  path_prefix      text,                                      -- optional path prefix (usually null)
  auth_secret_name text,                                      -- Vault ref for a bearer token, if any
  model_name       text,
  extra            jsonb not null default '{}'::jsonb,        -- headers, timeouts, gpu notes
  is_active        boolean not null default true,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (tenant_id, stage_key),
  -- a stage must be reachable by either a port (via the tenant template) or a full override:
  constraint service_endpoints_port_or_override check (port is not null or url_override is not null)
);

-- ============================================================================
-- 4. MEDIA ASSETS  (central blob registry — every uploaded/generated file)
--    Created early because products/personas/outputs/videos reference it.
--    Files live in Storage; this row holds bucket+path + metadata. Sign URLs on read.
-- ============================================================================
create table if not exists public.media_assets (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references public.tenants(id) on delete cascade,
  kind             text not null,    -- product_ref | portrait | scene | composite | realism | audio | silent_video | final_video
  bucket           text not null,
  path             text not null,    -- object path within the bucket (start with '<tenant_id>/...')
  mime_type        text,
  bytes            bigint,
  width            int,
  height           int,
  duration_seconds numeric,
  sha256           text,
  created_by_stage text,
  created_at       timestamptz not null default now(),
  unique (bucket, path)
);

-- ============================================================================
-- 5. PRODUCTS  (was product.yaml — exactly ONE per tenant)
--    mask_prompt = the only tenant-supplied realism text (Stage-3 box protect).
-- ============================================================================
create table if not exists public.products (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references public.tenants(id) on delete cascade,
  product_key        text not null,                          -- slug e.g. tirzepatide_40mg
  name               text not null,
  peptide_compound   text,
  dose               text,
  category           text,
  packaging          jsonb not null default '{}'::jsonb,     -- type, shape, colors, text_on_packaging, graphics, dims
  key_benefits       jsonb not null default '[]'::jsonb,
  target_audience    jsonb not null default '{}'::jsonb,
  do_not_claim       jsonb not null default '[]'::jsonb,
  -- Stage-3 box-protect detector prompt (GroundingDINO/SAM2). Maps to
  -- ALLUVI_MASK_PROMPT env at call time. Required, validate non-empty on the web form.
  mask_prompt        text not null default 'white product box package, white rectangular box',
  reference_asset_id uuid references public.media_assets(id) on delete set null,  -- the real product photo
  -- QC reference brief (migration 010): one-time Claude-vision ground truth + retry control
  qc_brief              text,
  qc_brief_generated_at timestamptz,
  qc_max_retries        int not null default 3,              -- total attempts = 1 + qc_max_retries
  -- product knowledge for video script generation (migration 012)
  product_info          jsonb,
  product_brief_text    text,                                -- raw plain-English product brief (migration 017)
  status             text not null default 'active',
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (tenant_id)                                         -- ENFORCES one product per tenant
);

-- ============================================================================
-- 6. SCENARIOS  (GLOBAL — shared by all tenants; no tenant_id)
--    1 row = 1 scenario. Full director's-brief JSON in spec (absorbs any extra
--    columns from your example). category/archetype/difficulty kept for filtering.
-- ============================================================================
create table if not exists public.scenarios (
  id            uuid primary key default gen_random_uuid(),
  scenario_key  text unique not null,                        -- e.g. gym_post_workout_mirror_01
  category      text,
  archetype     text,                                        -- held_product_high | placed_on_surface ...
  difficulty    text,                                        -- easy | medium | hard
  spec          jsonb not null,                              -- the COMPLETE scenario object
  source        text not null default 'hardcoded',           -- hardcoded | curated | tiktok_scrape | generated
  is_active     boolean not null default true,
  -- engine fields (migration 013): canonical attribute snapshot (our vocab) + versioning
  composed_attributes jsonb not null default '{}'::jsonb,
  version             text not null default 'v1',
  content_hash        text,
  scenario_title      text,
  created_at    timestamptz not null default now()
);

-- ============================================================================
-- 7. PROMPT TEMPLATES  (system prompts, versioned)
--    tenant_id null = global default; a tenant row overrides the global.
-- ============================================================================
create table if not exists public.prompt_templates (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid references public.tenants(id) on delete cascade,
  name        text not null,                                 -- phaseA | step1 | step2_qwen | script | multishot | qc
  version     text not null default 'v1',
  body        text not null,
  is_active   boolean not null default true,
  notes       text,
  created_at  timestamptz not null default now()
);
-- one (name, version) per tenant; global rows treated as 'GLOBAL'
create unique index if not exists uq_prompt_templates_name_version
  on public.prompt_templates (coalesce(tenant_id::text, 'GLOBAL'), name, version);

-- ============================================================================
-- 8. TIKTOK ACCOUNTS  (web "onboard account" page writes here)
-- ============================================================================
create table if not exists public.tiktok_accounts (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references public.tenants(id) on delete cascade,
  tiktok_id        text not null,                            -- the @handle
  name             text,                                     -- person/display name (persona builder reads this)
  gender           text,                                     -- 'male'|'female' (Stage 1 hard-fails if missing/invalid)
  age              int,
  country          text,
  language         text,
  identity_factors jsonb not null default '{}'::jsonb,       -- inputs to persona-appearance builder
  -- optional voice reference for F5-TTS cloning (migration 011); NULL on both -> gender default voice
  voice_reference_asset_id uuid references public.media_assets(id) on delete set null,
  voice_reference_text     text,                             -- transcript of the sample (F5 needs ref text)
  status           text not null default 'active',
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (tenant_id, tiktok_id)
);

-- ============================================================================
-- 9. PERSONAS  (Phase A — one locked face per account, reused forever)
-- ============================================================================
create table if not exists public.personas (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references public.tenants(id) on delete cascade,
  tiktok_account_id   uuid not null references public.tiktok_accounts(id) on delete cascade,
  appearance_spec     text,
  prompt_used         text,                                  -- exact Opus prompt that built the portrait
  portrait_asset_id   uuid references public.media_assets(id) on delete set null,
  portrait_local_path text,                                  -- transitional pod-side path (nullable)
  status              text not null default 'done',
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (tiktok_account_id)
);

-- ============================================================================
-- 10. PIPELINE RUNS  (batch header)
-- ============================================================================
create table if not exists public.pipeline_runs (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references public.tenants(id) on delete cascade,
  product_id      uuid references public.products(id) on delete set null,
  run_id          text not null,
  flow_name       text not null,                             -- image_chain | video_chain | ...
  scenario_filter text,
  total_scenarios int not null default 0,
  status          text not null default 'running',
  total_cost_usd  numeric not null default 0,
  notes           text,
  created_at      timestamptz not null default now(),
  finished_at     timestamptz,
  unique (tenant_id, run_id)
);

-- ============================================================================
-- 11. OUTPUTS  (canonical image: one per persona x scenario; resumable)
-- ============================================================================
create table if not exists public.outputs (
  id                     uuid primary key default gen_random_uuid(),
  tenant_id              uuid not null references public.tenants(id) on delete cascade,
  persona_id             uuid not null references public.personas(id) on delete cascade,
  product_id             uuid references public.products(id) on delete set null,
  run_id                 uuid references public.pipeline_runs(id) on delete set null,
  scenario_id            uuid references public.scenarios(id) on delete set null,
  scenario_key           text,
  scenario_title         text,
  step1_asset_id         uuid references public.media_assets(id) on delete set null,   -- PuLID scene
  step2_asset_id         uuid references public.media_assets(id) on delete set null,   -- Qwen composite
  step3_asset_id         uuid references public.media_assets(id) on delete set null,   -- realism (video anchor)
  final_image_asset_id   uuid references public.media_assets(id) on delete set null,
  final_image_local_path text,                               -- transitional (nullable)
  qc_status              text,                               -- pass | fail | skipped
  qc_reason              text,
  attempts               int not null default 1,
  status                 text not null default 'done',
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  unique (persona_id, scenario_id)
);

-- ============================================================================
-- 12. STAGE EXECUTIONS  (the spine — one row per stage per artifact)
-- ============================================================================
create table if not exists public.stage_executions (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references public.tenants(id) on delete cascade,
  run_id          uuid references public.pipeline_runs(id) on delete set null,
  persona_id      uuid references public.personas(id) on delete set null,
  output_id       uuid references public.outputs(id) on delete set null,
  stage_name      text not null,                             -- phasea|step1|step2|step3|tts|wan|lipsync|merge|qc
  status          text not null default 'running',
  elapsed_seconds numeric,
  error_message   text,
  created_at      timestamptz not null default now(),
  finished_at     timestamptz
);

-- ============================================================================
-- 13. LLM CALLS  (EVERY Anthropic call — prompt builders + QC + scripts)
-- ============================================================================
create table if not exists public.llm_calls (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants(id) on delete cascade,
  stage_execution_id    uuid references public.stage_executions(id) on delete set null,
  purpose               text not null,                       -- step1_prompt|step2_prompt|phasea_prompt|script|qc
  model                 text not null,
  system_prompt_name    text,
  system_prompt_version text,
  user_message          text,                                -- the rendered user prompt (FULL)
  raw_response          text,                                -- the model's raw output (FULL)
  parsed_json           jsonb,
  input_tokens          int,
  output_tokens         int,
  cost_usd              numeric not null default 0,
  latency_ms            int,
  created_at            timestamptz not null default now()
);

-- ============================================================================
-- 14. IMAGE GENERATIONS  (EVERY diffusion call — PuLID/Qwen/realism). seed NUMERIC.
-- ============================================================================
create table if not exists public.image_generations (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references public.tenants(id) on delete cascade,
  stage_execution_id  uuid references public.stage_executions(id) on delete set null,
  stage_name          text not null,                         -- step1 | step2 | step3
  model_name          text,
  endpoint            text,
  backend             text,
  workflow_template   text,
  prompt              text,                                  -- positive prompt fed to model (FULL)
  negative_prompt     text,
  mask_prompt         text,                                  -- realism box-protect prompt (Stage 3)
  input_image_paths   jsonb,
  num_inference_steps int,
  cfg                 numeric,
  guidance            numeric,
  seed                numeric,                               -- NUMERIC (overflow-safe)
  width               int,
  height              int,
  target_size         int,
  id_weight           numeric,                               -- PuLID (clamped <= 0.6 for persona)
  max_sequence_length int,
  output_asset_id     uuid references public.media_assets(id) on delete set null,
  output_local_path   text,
  request_id          text,
  elapsed_seconds     numeric,
  cost_usd            numeric not null default 0,
  attempt_number      int not null default 1,
  comfyui_server      text,
  created_at          timestamptz not null default now()
);

-- ============================================================================
-- 15. QC CHECKS  (every QC attempt)
-- ============================================================================
create table if not exists public.qc_checks (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references public.tenants(id) on delete cascade,
  stage_execution_id  uuid references public.stage_executions(id) on delete set null,
  output_id           uuid references public.outputs(id) on delete set null,
  image_generation_id uuid references public.image_generations(id) on delete set null,
  attempt_number      int not null default 1,
  qc_model            text not null,
  passed              boolean,
  qc_reason           text,
  issues              jsonb,
  limb_description    text,
  scores              jsonb,
  avoid_line          text,
  image_evaluated_path text,
  raw_result          jsonb,
  created_at          timestamptz not null default now()
);

-- ============================================================================
-- 16. VIDEOS  (one per output)
-- ============================================================================
create table if not exists public.videos (
  id                     uuid primary key default gen_random_uuid(),
  tenant_id              uuid not null references public.tenants(id) on delete cascade,
  output_id              uuid not null references public.outputs(id) on delete cascade,
  persona_id             uuid references public.personas(id) on delete set null,
  product_id             uuid references public.products(id) on delete set null,
  run_id                 uuid references public.pipeline_runs(id) on delete set null,
  scenario_id            uuid references public.scenarios(id) on delete set null,
  dialogue               text,
  language               text,
  hook_style             text,
  scene_mood             text,
  wan_motion_prompt      text,
  wan_negative_prompt    text,
  ref_audio_path         text,
  ref_text               text,
  audio_asset_id         uuid references public.media_assets(id) on delete set null,
  silent_video_asset_id  uuid references public.media_assets(id) on delete set null,
  final_video_asset_id   uuid references public.media_assets(id) on delete set null,
  audio_duration_seconds numeric,
  num_frames             int,
  fps                    int,
  video_mode             text,                               -- multishot | silentfirst
  status                 text not null default 'running',
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  unique (output_id)
);

-- ============================================================================
-- 17. MEDIA GENERATIONS  (audio/video analog of image_generations)
-- ============================================================================
create table if not exists public.media_generations (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references public.tenants(id) on delete cascade,
  video_id           uuid references public.videos(id) on delete cascade,
  stage_execution_id uuid references public.stage_executions(id) on delete set null,
  stage_name         text not null,                          -- tts | wan | lipsync | merge
  media_type         text not null,                          -- audio | video
  model_name         text,
  backend            text not null default 'comfyui',
  workflow_template  text,
  comfyui_server     text,
  prompt             text,
  negative_prompt    text,
  params             jsonb,
  input_paths        jsonb,
  output_asset_id    uuid references public.media_assets(id) on delete set null,
  output_local_path  text,
  duration_seconds   numeric,
  elapsed_seconds    numeric,
  cost_usd           numeric not null default 0,
  created_at         timestamptz not null default now()
);

-- ============================================================================
-- 18. JOBS  (async API layer — gateway writes, orchestrator polls)
-- ============================================================================
create table if not exists public.jobs (
  id                  uuid primary key default gen_random_uuid(),   -- the job_id returned to the caller
  tenant_id           uuid not null references public.tenants(id) on delete cascade,
  job_type            text not null,                         -- portrait | image_chain | video_chain | stage_*
  status              text not null default 'queued',        -- queued | running | succeeded | failed | cancelled
  service_endpoint_id uuid references public.service_endpoints(id) on delete set null,
  request_payload     jsonb not null default '{}'::jsonb,
  result              jsonb,
  error               text,
  persona_id          uuid references public.personas(id) on delete set null,
  output_id           uuid references public.outputs(id) on delete set null,
  video_id            uuid references public.videos(id) on delete set null,
  -- durable-queue fields (see migration 005):
  idempotency_key            text,                           -- one job per (tenant, key)
  priority                   int not null default 100,       -- lower runs sooner
  scheduled_at               timestamptz not null default now(),
  retry_count                int not null default 0,
  max_retries                int not null default 3,
  locked_at                  timestamptz,                    -- worker lease timestamp
  locked_by                  text,                           -- worker id holding the lease
  visibility_timeout_seconds int not null default 5400,      -- stuck-job reclaim window (>= max job runtime)
  created_at          timestamptz not null default now(),
  started_at          timestamptz,
  finished_at         timestamptz
);
-- one active job per (tenant, idempotency_key); fast queue scan over queued rows
create unique index if not exists uq_jobs_idempotency
  on public.jobs (tenant_id, idempotency_key) where idempotency_key is not null;
create index if not exists idx_jobs_queue
  on public.jobs (scheduled_at, priority) where status = 'queued';

-- ============================================================================
-- 18b. TENANT PIPELINE CONFIG  (migration 011 — web execution page writes this)
--      num_shots = ceil(video_duration_seconds / shot_seconds); one clip per shot.
-- ============================================================================
create table if not exists public.tenant_pipeline_config (
  tenant_id              uuid primary key references public.tenants(id) on delete cascade,
  creation_mode          text not null default 'all',        -- 'all' | 'new_only' | 'specific'
  target_account_id      uuid references public.tiktok_accounts(id) on delete set null,  -- single (legacy)
  target_account_ids     jsonb not null default '[]'::jsonb,  -- multiple specific accounts (migration 018)
  num_videos_per_account int  not null default 1,            -- n videos = n scenarios = n images
  video_mode             text not null default 'multishot',  -- 'multishot' | 'silentfirst'
  video_duration_seconds int  not null default 10,
  shot_seconds           int  not null default 5,
  lips_expression        numeric not null default 2.0,       -- silentfirst fine knobs
  inference_steps        int  not null default 40,
  intro_seconds          int  not null default 2,
  outro_seconds          int  not null default 2,
  punch_in               numeric not null default 1.2,
  threshold              int  not null default 70,
  tail_seconds           int  not null default 0,
  seed                   int,
  step_3_enabled         boolean not null default true,      -- stage toggles
  qc_enabled             boolean not null default true,
  updated_at             timestamptz not null default now(),
  constraint creation_mode_chk check (creation_mode in ('all','new_only','specific')),
  constraint video_mode_chk    check (video_mode  in ('multishot','silentfirst'))
);

-- ============================================================================
-- 18c. RECOMMENDATION / TUNING ENGINE  (migrations 013 + 016)
--      scenarios + attribute_priors are UNIVERSAL; ratings/stats/tuning/state are PER-TENANT.
--
-- Rubric contract (the web writes these exact ids into asset_ratings.image/.video):
--   IMAGE gates(8): img_product_present, img_color_fidelity, img_shape_dimensions,
--                   img_brand_text, img_productname_text, img_grip_logic,
--                   img_persona_identity, img_scene_logic
--   IMAGE scores(5): img_scene_adherence, img_aesthetic, img_detail_realism,
--                    img_lighting, img_ad_worthiness(commercial)
--   VIDEO gates(5): vid_product_identity, vid_persona_identity, vid_no_artifacts,
--                   vid_grip_maintained, vid_brand_text_motion
--   VIDEO scores(7): vid_motion_smoothness, vid_temporal_stability, vid_dynamic_degree,
--                    vid_camera_motion, vid_physical_plausibility, vid_imaging_quality,
--                    vid_hook_strength(commercial)
--   gates store {"result":"Pass"|"Fail",...}; scores are 1..5 (omit if unrated).
-- ============================================================================

-- one row per output (the web upserts by output_id)
create table if not exists public.asset_ratings (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references public.tenants(id) on delete cascade,
  output_id           uuid not null references public.outputs(id) on delete cascade,
  image               jsonb,                       -- {gates:{id:{result,...}}, scores:{id:1..5}}
  video               jsonb,                       -- same shape with vid_* ids
  image_rated         boolean not null default false,
  video_rated         boolean not null default false,
  decision            text,                        -- accept | reject | flag (UI DECISION control)
  composed_attributes jsonb,                       -- immutable snapshot at rating time
  scenario_version    text,
  rated_by            text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (output_id)
);

-- per-tenant sufficient statistics + cached shrunk estimate (rebuilt by the recompute job)
create table if not exists public.attribute_stats (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  context_key   text not null default 'global',     -- 'global' | 'country=<c>'
  attribute_key text not null,                       -- e.g. 'lighting=golden_hour'
  dimension     text not null,                       -- a rubric dimension id
  kind          text not null,                       -- 'gate' | 'score'
  n             int     not null default 0,
  passes        int     not null default 0,
  sum_val       numeric not null default 0,
  sum_sq        numeric not null default 0,
  estimate      numeric,
  updated_at    timestamptz not null default now(),
  unique (tenant_id, context_key, attribute_key, dimension)
);

-- GLOBAL cold-start priors (shared like scenarios; written by the recompute job)
create table if not exists public.attribute_priors (
  id            uuid primary key default gen_random_uuid(),
  context_key   text not null default 'global',
  attribute_key text not null,
  dimension     text not null,
  kind          text not null,
  n             int     not null default 0,
  passes        int     not null default 0,
  sum_val       numeric not null default 0,
  sum_sq        numeric not null default 0,
  estimate      numeric,
  updated_at    timestamptz not null default now(),
  unique (context_key, attribute_key, dimension)
);

-- per-tenant prompt/script edits (candidate -> testing -> validated/rejected)
create table if not exists public.tuning_suggestions (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references public.tenants(id) on delete cascade,
  scope_type        text not null,                   -- 'attribute' | 'scenario'
  scope_key         text not null,                   -- e.g. 'lighting=overcast'
  dimension         text not null,
  context_key       text not null default 'global',  -- (migration 016)
  cause             text,
  suggested_edit    text,
  status            text not null default 'candidate'
    check (status in ('candidate','testing','validated','rejected')),
  evidence_n        int not null default 0,
  score_delta       numeric,
  baseline_estimate numeric,                          -- (migration 016) pre/post A/B baseline
  baseline_n        int,                              -- (migration 016)
  source_output_id  uuid references public.outputs(id) on delete set null,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create unique index if not exists uq_tuning_scope
  on public.tuning_suggestions (tenant_id, scope_type, scope_key, dimension, context_key);

-- per-tenant lifecycle (exploration -> active); flips ON once the curated set is worked through
create table if not exists public.tenant_learning_state (
  tenant_id              uuid primary key references public.tenants(id) on delete cascade,
  phase                  text not null default 'exploration',   -- exploration | active
  engine_enabled         boolean not null default false,
  min_coverage_pct       int not null default 100 check (min_coverage_pct between 1 and 100),
  exploration_started_at timestamptz default now(),
  engine_enabled_at      timestamptz,
  updated_at             timestamptz not null default now()
);

-- ============================================================================
-- 18d. EXPLORATION-PROGRESS VIEW  (migrations 013 + 015)
--   A curated scenario is "resolved" for a tenant once it is WORKED THROUGH to a terminal
--   state: image done (status='step3_done', covers QC-pass AND QC-exhaust) OR QC terminated.
--   No video-rating requirement — so the engine flips on even if some scenarios were QC-skipped.
-- ============================================================================
create or replace view public.v_tenant_exploration_progress as
with curated as (
  select count(*)::int as active_curated
  from public.scenarios
  where coalesce(source, '') <> 'generated' and is_active
),
resolved as (
  select o.tenant_id, count(distinct o.scenario_id)::int as resolved_curated
  from public.outputs o
  join public.scenarios s
    on s.id = o.scenario_id and coalesce(s.source, '') <> 'generated' and s.is_active
  where o.status = 'step3_done'
     or o.qc_status in ('passed', 'exhausted', 'skipped', 'fail', 'failed')
  group by o.tenant_id
)
select
  p.id as tenant_id,
  (select active_curated from curated) as active_curated,
  coalesce(r.resolved_curated, 0) as resolved_curated,
  case when (select active_curated from curated) > 0
       then round(100.0 * coalesce(r.resolved_curated, 0) / (select active_curated from curated))::int
       else 0 end as pct_complete,
  (coalesce(r.resolved_curated, 0) >= (select active_curated from curated)
   and (select active_curated from curated) > 0) as is_complete
from public.tenants p
left join resolved r on r.tenant_id = p.id;

-- ============================================================================
-- 18e. RPC FUNCTIONS  (migrations 007 + 008; service_role only)
-- ============================================================================
-- atomic durable-queue claim (FOR UPDATE SKIP LOCKED) + stuck-job reclaim
create or replace function public.claim_next_job(
  p_worker_id text,
  p_job_types text[]
)
returns setof public.jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  j public.jobs;
begin
  select * into j
  from public.jobs
  where job_type = any(p_job_types)
    and (
      (status = 'queued'  and scheduled_at <= now())
      or
      (status = 'running' and locked_at is not null
        and locked_at < now() - make_interval(secs => visibility_timeout_seconds))
    )
  order by priority asc, scheduled_at asc
  for update skip locked
  limit 1;

  if not found then
    return;
  end if;

  if j.status = 'running' then
    if (j.retry_count + 1) > j.max_retries then
      update public.jobs
        set status = 'failed',
            error = coalesce(error,'') || ' [reclaimed: exceeded max_retries]',
            finished_at = now(), locked_by = null, locked_at = null
      where id = j.id;
      return;
    end if;
    update public.jobs
      set status = 'running', locked_at = now(), locked_by = p_worker_id,
          retry_count = retry_count + 1
    where id = j.id
    returning * into j;
    return next j;
    return;
  end if;

  update public.jobs
    set status = 'running', locked_at = now(), locked_by = p_worker_id,
        started_at = coalesce(started_at, now())
  where id = j.id
  returning * into j;
  return next j;
end;
$$;

-- secure read of the tenant's Anthropic key from Vault (PostgREST can't read vault directly)
create or replace function public.get_tenant_anthropic_key(p_tenant_id uuid)
returns text
language sql
security definer
set search_path = public, vault
as $$
  select ds.decrypted_secret
  from vault.decrypted_secrets ds
  where ds.name = (select anthropic_secret_name from public.tenants where id = p_tenant_id)
  limit 1;
$$;

revoke execute on function public.claim_next_job(text, text[]) from public;
grant  execute on function public.claim_next_job(text, text[]) to service_role;
revoke execute on function public.get_tenant_anthropic_key(uuid) from public;
grant  execute on function public.get_tenant_anthropic_key(uuid) to service_role;

-- ============================================================================
-- 19. INDEXES  (FK + lookup helpers; tenant_id present for RLS-filtered scans)
-- ============================================================================
create index if not exists idx_service_endpoints_tenant on public.service_endpoints(tenant_id);
create index if not exists idx_media_assets_tenant      on public.media_assets(kind, tenant_id);
create index if not exists idx_products_tenant          on public.products(tenant_id);
create index if not exists idx_scenarios_active         on public.scenarios(is_active);
create index if not exists idx_prompt_templates_name    on public.prompt_templates(name, is_active);
create index if not exists idx_accounts_tenant          on public.tiktok_accounts(tenant_id);
create index if not exists idx_personas_account         on public.personas(tiktok_account_id);
create index if not exists idx_personas_tenant          on public.personas(tenant_id);
create index if not exists idx_runs_tenant              on public.pipeline_runs(tenant_id);
create index if not exists idx_outputs_persona          on public.outputs(persona_id);
create index if not exists idx_outputs_status           on public.outputs(status, qc_status, tenant_id);
create index if not exists idx_stage_exec_output        on public.stage_executions(output_id);
create index if not exists idx_llm_calls_stage          on public.llm_calls(stage_execution_id);
create index if not exists idx_imggen_stage             on public.image_generations(stage_execution_id);
create index if not exists idx_qc_output                on public.qc_checks(output_id);
create index if not exists idx_videos_output            on public.videos(output_id);
create index if not exists idx_mediagen_video           on public.media_generations(video_id);
create index if not exists idx_jobs_status              on public.jobs(status, tenant_id);
create index if not exists idx_jobs_type                on public.jobs(job_type, tenant_id);
-- engine indexes (migration 013)
create index if not exists idx_attr_stats_tenant_dim    on public.attribute_stats(tenant_id, dimension);
create index if not exists idx_tuning_lookup            on public.tuning_suggestions(tenant_id, scope_type, scope_key, dimension, status);
create index if not exists idx_asset_ratings_tenant     on public.asset_ratings(tenant_id);
create index if not exists idx_outputs_qc               on public.outputs(qc_status);

-- ============================================================================
-- 20. ROW LEVEL SECURITY  (enable on all; service/secret key bypasses it)
-- ============================================================================
do $$
declare t text;
begin
  foreach t in array array[
    'tenants','tenant_members','service_endpoints','media_assets','products',
    'scenarios','prompt_templates','tiktok_accounts','personas','pipeline_runs',
    'outputs','stage_executions','llm_calls','image_generations','qc_checks',
    'videos','media_generations','jobs',
    'tenant_pipeline_config','asset_ratings','attribute_stats','attribute_priors',
    'tuning_suggestions','tenant_learning_state'
  ]
  loop
    execute format('alter table public.%I enable row level security;', t);
  end loop;
end$$;

-- Tenant-scoped read/write for tables that carry a NOT NULL tenant_id.
do $$
declare t text;
begin
  foreach t in array array[
    'service_endpoints','media_assets','products','tiktok_accounts','personas',
    'pipeline_runs','outputs','stage_executions','llm_calls','image_generations',
    'qc_checks','videos','media_generations','jobs',
    'tenant_pipeline_config','asset_ratings','attribute_stats',
    'tuning_suggestions','tenant_learning_state'
  ]
  loop
    execute format('drop policy if exists %1$I_tenant_rw on public.%1$I;', t);
    execute format($f$
      create policy %1$I_tenant_rw on public.%1$I
        using (tenant_id = public.current_tenant_id())
        with check (tenant_id = public.current_tenant_id());
    $f$, t);
  end loop;
end$$;

-- tenants: a member sees their own tenant row.
drop policy if exists tenants_member_select on public.tenants;
create policy tenants_member_select on public.tenants
  for select using (id = public.current_tenant_id());

-- tenants: a member may UPDATE their own tenant row (gpu_host, briefs, settings.onboarded).
-- The browser (anon key) needs this for the web setup page; Edge Functions/orchestrator use the
-- service role and bypass RLS. NOTE: this allows updating ANY column on the member's own row;
-- for production, restrict to a column allow-list via a SECURITY DEFINER RPC.
drop policy if exists tenants_member_update on public.tenants;
create policy tenants_member_update on public.tenants
  for update using (id = public.current_tenant_id())
  with check (id = public.current_tenant_id());

-- tenant_members: a user sees membership rows for their tenant.
drop policy if exists members_tenant_select on public.tenant_members;
create policy members_tenant_select on public.tenant_members
  for select using (tenant_id = public.current_tenant_id());

-- scenarios: GLOBAL read for any authenticated user (writes via service key only).
drop policy if exists scenarios_read on public.scenarios;
create policy scenarios_read on public.scenarios
  for select using (true);

-- attribute_priors: GLOBAL cold-start read for any authenticated user (writes via service key only).
drop policy if exists attribute_priors_read on public.attribute_priors;
create policy attribute_priors_read on public.attribute_priors
  for select using (true);

-- prompt_templates: a tenant sees global rows + its own.
drop policy if exists prompt_templates_read on public.prompt_templates;
create policy prompt_templates_read on public.prompt_templates
  for select using (tenant_id is null or tenant_id = public.current_tenant_id());
drop policy if exists prompt_templates_write on public.prompt_templates;
create policy prompt_templates_write on public.prompt_templates
  for all using (tenant_id = public.current_tenant_id())
  with check (tenant_id = public.current_tenant_id());

-- ============================================================================
-- 21. STORAGE BUCKETS  (private — serve via signed URLs; path '<tenant_id>/...')
-- ============================================================================
insert into storage.buckets (id, name, public, file_size_limit)
values
  ('products', 'products', false, 26214400),       -- 25 MB
  ('portraits','portraits',false, 26214400),        -- 25 MB
  ('images',   'images',   false, 52428800),        -- 50 MB
  ('audio',    'audio',    false, 52428800),         -- 50 MB
  ('videos',   'videos',   false, 524288000)         -- 500 MB
on conflict (id) do nothing;

-- ============================================================================
-- 22. POST-RUN MANUAL STEPS (do these in the Dashboard / via RPC — see the guide)
--   1) Create the tenant:
--        insert into public.tenants (name, slug, email, plan)
--        values ('Alluvi','alluvi','you@example.com','enterprise') returning id;
--   2) Store its Anthropic key in Vault, then save the reference:
--        select vault.create_secret('<sk-ant-...>','anthropic:<TENANT_UUID>','Anthropic key');
--        update public.tenants set anthropic_secret_name='anthropic:<TENANT_UUID>'
--          where id='<TENANT_UUID>';
--      Read it server-side:
--        select decrypted_secret from vault.decrypted_secrets where name='anthropic:<TENANT_UUID>';
--   3) Set the tenant's GPU host (the RunPod pod-id) + add per-stage PORTS:
--        update public.tenants set gpu_host = 'YOURPODID' where id = '<TENANT_UUID>';
--        insert into public.service_endpoints (tenant_id, stage_key, port) values
--          ('<TENANT_UUID>','gateway',8191),('<TENANT_UUID>','stage2_qwen',8188), ... ;
--      On every pod restart, you only re-run the UPDATE above with the new pod-id.
--   4) Create the ONE product (with mask_prompt), upload the product photo to the
--      'products' bucket, register it in media_assets, set products.reference_asset_id.
--   5) Onboard tiktok_accounts. (Scenarios are GLOBAL — loaded once via the seed file.)
-- ============================================================================

-- ============================================================================
-- 24. GRANTS  (so the API roles can use the schema — fixes 42501 perm-denied)
--     service_role (secret key) bypasses RLS and gets full access; anon/
--     authenticated get DML for the future web app (RLS gates the rows).
-- ============================================================================
grant usage on schema public to anon, authenticated, service_role;
grant all on all tables    in schema public to service_role;
grant all on all sequences in schema public to service_role;
grant execute on all functions in schema public to service_role;
grant select, insert, update, delete on all tables in schema public to anon, authenticated;
alter default privileges in schema public grant all on tables    to service_role;
alter default privileges in schema public grant all on sequences to service_role;
alter default privileges in schema public grant execute on functions to service_role;
alter default privileges in schema public
  grant select, insert, update, delete on tables to anon, authenticated;