-- ============================================================================
-- 022_realism_knobs.sql — per-tenant Stage-3 realism tuning knobs (ADDITIVE)
-- ----------------------------------------------------------------------------
-- Two web-editable knobs for the realism pass (Run settings → advanced):
--   realism_denoise        — repaint strength (default 0.30 in step_3_realism.py;
--                            higher = more pores/texture, too high drifts identity)
--   realism_lora_strength  — skin-realism LoRA intensity (default 0.7)
-- NULL = not set = the pod's env/code defaults — existing behavior unchanged.
-- Chain: web → these columns → orchestrator step3 payload `realism_params`
-- (only when set) → gateway worker forwards → realism service monkey-patches
-- step_3_realism.DENOISE / .LORA_STRENGTH per request (claudeAI.md TASK 2).
-- ============================================================================

alter table public.tenant_pipeline_config
  add column if not exists realism_denoise        numeric,
  add column if not exists realism_lora_strength  numeric;
