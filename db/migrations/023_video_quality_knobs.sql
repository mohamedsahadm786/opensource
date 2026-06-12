-- ============================================================================
-- 023_video_quality_knobs.sql — per-tenant Wan video-quality knobs (ADDITIVE)
-- ----------------------------------------------------------------------------
-- Four web-editable knobs for the video stage (Run settings → video quality):
--   wan_steps         — Wan sampling steps (workflow default 20; 25-30 = sharper,
--                       more coherent frames; render time scales linearly)
--   wan_cfg_high      — CFG of the HIGH-NOISE expert (steps 0..split; controls how
--                       strictly MOTION/composition follows the prompt; default 3.5)
--   wan_cfg_low       — CFG of the LOW-NOISE expert (steps split..N; controls how
--                       strictly fine DETAIL follows the prompt; default 3.5 —
--                       NOTE: the workflow shares ONE 3.5 between both experts;
--                       the often-quoted 1.0 is the disabled lightx2v fast-mode CFG)
--   interp_target_fps — RIFE frame-interpolation target fps (NULL = off = native
--                       16 fps; 32 = double via RIFE per clip, near-free vs diffusion)
-- NULL = not set = today's behavior, byte-identical.
-- Chain: web → these columns → run_video.resolve_controls → video payload
-- `wan_params` {steps, cfg_high, cfg_low} + `interp_fps` (keys OMITTED when unset)
-- → gateway VideoJobRequest → pod worker → video-service → step_5_video_wan
-- sampling kwargs + multishot/stitch interp_fps (claudeAI.md TASK 3).
-- ============================================================================

alter table public.tenant_pipeline_config
  add column if not exists wan_steps          integer,
  add column if not exists wan_cfg_high       numeric,
  add column if not exists wan_cfg_low        numeric,
  add column if not exists interp_target_fps  integer;
