# claudeAI.md — prompts for the POD-side Claude

> Purpose: tasks for the pod's own code (`/workspace/alluvi-gateway`, `/workspace/qwen-service`)
> live OUTSIDE this repo, so they are fixed by giving the prompt below to the Claude that
> maintains the pod code. Copy-paste the whole block for one task. After the pod work is done,
> mark the task DONE here.

---

## TASK 1 — image_generations must record EVERY Step-2 attempt (status: DONE 2026-06-11 — verified: 14 step2 rows, one per attempt, correct attempt_number + fresh seeds)

Copy-paste everything between the lines to the pod Claude:

------------------------------------------------------------------------------------

CONTEXT
You maintain the Alluvi GPU-pod services: /workspace/alluvi-gateway (FastAPI app.py +
worker.py) and /workspace/qwen-service (app.py), which wrap the repo at
/workspace/alluvi-clean. The orchestrator (on the owner's PC) runs a QC retry loop for
Step 2 (Qwen product compositing): when QC fails, it enqueues ANOTHER step2 job for the
same persona+scenario with a bumped `attempt` field in the payload (the gateway's
idempotency key is `step2:<persona>:<scenario>:a<attempt>`, so each attempt is a separate
job that really runs a fresh Qwen generation).

THE BUG (verified from the database on 2026-06-10)
A QC retry run executed THREE real Qwen generations for one scenario
(gym_post_workout_mirror_01, attempts 1→2→3; three jobs, three qc_checks rows, three
llm_calls rows) — but the `image_generations` table received only ONE row (attempt 1,
seed 1138169898, created 17:21:49). Attempts 2 and 3 produced images (the storage object
at .../step2.jpg was overwritten each time, and outputs.step2_asset_id points at the
reused media_assets row because the storage path is deterministic) but their generation
metadata (prompt, seed, params, attempt_number) was never inserted.

CONSEQUENCE
The audit trail is wrong: the final stored image is from attempt 3, but the only
image_generations row carries attempt 1's prompt/seed. The web console's Image-debug
panel joins image_generations by output_asset_id and therefore shows the WRONG prompt
next to the final image.

YOUR TASK
Find where the step2 path writes the `image_generations` row (it is wherever the worker
or qwen-service records the generation after a successful render — locate the insert for
stage_name='step2') and figure out why it only happens for the first attempt of a
scenario. Likely suspects, in order:
  1. The insert is keyed/guarded so a second insert for the same output/asset is skipped
     (an upsert with on_conflict, or an "already exists" check).
  2. The insert errors on attempts 2+ (e.g. a unique constraint via the reused
     media_assets id) and the error is swallowed.
  3. The row-writing code path is skipped when the outputs row already has
     step2_asset_id set.
Fix it so that EVERY successful Step-2 generation inserts ONE NEW image_generations row
with at minimum: tenant_id, stage_name='step2', prompt (the full positive prompt),
negative_prompt, seed, attempt_number (from the job payload's `attempt`, default 1),
output_asset_id (the media_assets id — reused across attempts is fine), and
elapsed_seconds. Do NOT de-duplicate; three attempts = three rows.

CONSTRAINTS
- Do not change the storage path convention, the media_assets upsert, or
  outputs.step2_asset_id handling — overwriting the same object per scenario is
  intentional; only the per-attempt metadata rows are missing.
- Do not touch the scene/step3/portrait/video handlers unless they share the exact same
  guarded insert (if they do, apply the same fix there too and say so).
- Backward compatible: payloads without `attempt` default to attempt_number=1.

VERIFY
1. Enqueue a step2 job for an already-done scenario with attempt bumped (the owner can
   do this from the PC). After it succeeds:
   select created_at, attempt_number, seed from image_generations
   where stage_name='step2' order by created_at desc limit 3;
   → the new attempt must appear as a NEW row with the right attempt_number and a fresh
   created_at/seed.
2. Confirm the older rows were not deleted or updated.

------------------------------------------------------------------------------------

NOTE for the repo side (not the pod): once per-attempt rows exist, the web's
`useOutputDebug` should prefer the LATEST image_generations row per stage (order by
created_at desc) so the debug panel shows the prompt that actually produced the stored
image. Check this after the pod fix lands. (DONE 2026-06-11 — debug panel shows
per-attempt prompts with the final one labeled.)

---

## TASK 2 — per-request realism knobs (denoise + LoRA strength) (status: PENDING)

Copy-paste everything between the lines to the pod Claude:

------------------------------------------------------------------------------------

CONTEXT
You maintain the Alluvi GPU-pod services: /workspace/alluvi-gateway (FastAPI app.py +
worker.py) and the realism service (port 8194), which wrap the repo at
/workspace/alluvi-clean. The repo's src/step_3_realism.py reads its tuning constants
from MODULE-LEVEL globals at workflow-build time (DENOISE, default 0.30, and
LORA_STRENGTH, default 0.7 — see _build_workflow), so they can be monkey-patched
per request exactly like the qwen-service already monkey-patches
q.PRODUCT_ANGLE_IMAGE_PATH for the Picture-3 feature.

WHAT CHANGED ON THE ORCHESTRATOR SIDE (already live)
The web Run-settings now has two optional Stage-3 knobs stored on
tenant_pipeline_config (migration 022): realism_denoise and realism_lora_strength.
When the tenant has set them, run_pipeline.py adds this to the step3 job payload:
  "realism_params": {"denoise": <float>, "lora_strength": <float>}
(one or both keys; the key is OMITTED ENTIRELY when the tenant set nothing —
payloads without realism_params must behave byte-identically to today).

YOUR TASK
1. /workspace/alluvi-gateway/worker.py — in handle_step3, read
   p.get("realism_params") and, when present, forward it in the JSON body of the
   POST to the realism service's /step3/generate as "realism_params".
2. The realism service (port 8194) app.py — accept the optional "realism_params"
   object. On EVERY request, set the module globals UNCONDITIONALLY before
   generating (no leakage between requests):
     import step_3_realism as r   # or however the module is imported there
     rp = body.get("realism_params") or {}
     r.DENOISE       = float(rp.get("denoise", DEFAULT_DENOISE))
     r.LORA_STRENGTH = float(rp.get("lora_strength", DEFAULT_LORA_STRENGTH))
   where DEFAULT_* are captured ONCE at service startup from the module's import-time
   values (which already honor the REALISM_DENOISE / REALISM_LORA_STRENGTH env vars).
   Setting unconditionally on every request is the multi-tenant-safe pattern — a
   request without knobs must always run with the startup defaults, never with a
   previous request's values.
3. Log one line per request showing the effective values, e.g.
   [realism] denoise=0.30 lora=0.70 (defaults)  /  [realism] denoise=0.45 lora=0.70 (per-request)

CONSTRAINTS
- Do NOT change CFG, STEPS, the mask params, the prompts, the workflow template,
  storage paths, or any other handler.
- Clamp sanity: denoise to [0.05, 0.95], lora_strength to [0.0, 2.0]; out-of-range
  values clamp, never error the job.
- Backward compatible: a payload without realism_params = today's behavior exactly.
- Remember /workspace/alluvi-clean is NOT a git clone — if you need the current
  step_3_realism.py, curl the raw GitHub file; back up before replacing anything.

VERIFY
1. Run a step3 with NO knobs set → the service log must show the default line and
   the output image must look as before.
2. Set realism_denoise=0.45 in the web Run settings, re-run a step3 → the log must
   show denoise=0.45, and jobs.request_payload for that job (the owner checks from
   the PC) must contain realism_params.denoise=0.45.
3. Immediately run another step3 WITHOUT knobs → log shows defaults again (no
   leakage).

------------------------------------------------------------------------------------
