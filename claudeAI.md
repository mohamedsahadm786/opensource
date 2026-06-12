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

## TASK 2 — per-request realism knobs (denoise + LoRA strength) (status: DONE 2026-06-12 — verified live: jobs.request_payload carried realism_params {0.45/0.75} and the pod logged `[realism] denoise=0.45 lora=0.75 (per-request)`; required the TASK 2b gateway fix)

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

---

## TASK 2b — gateway drops realism_params (root cause of TASK 2 not working) (status: DONE 2026-06-12 — gateway Step3JobRequest got realism_params field, gateway restarted, verified live same day)

Diagnosed 2026-06-12 with DB evidence + pod-Claude code audit: the orchestrator DOES send
"realism_params" in POST /step3, but the gateway's pydantic model `Step3JobRequest` in
/workspace/alluvi-gateway/app.py has no `realism_params` field, and the handler stores
`req.model_dump()` as the job payload — pydantic v2 silently drops unknown keys, so the
knobs never reach jobs.request_payload. worker.py forwarding + realism app.py per-request
application are both implemented correctly and need NO changes.

Copy-paste everything between the lines to the pod Claude:

------------------------------------------------------------------------------------

Confirmed — ship the gateway fix for the realism knobs (your Q1 finding). Scope:

1. /workspace/alluvi-gateway/app.py — add to Step3JobRequest:
     realism_params: Optional[dict] = None
   so model_dump() carries it into the job payload. (If model_dump() is called without
   exclude_none and a None value would now appear in payloads, that is acceptable ONLY
   if worker.py's `if p.get("realism_params")` still treats it as absent — it does.
   Prefer matching however Step2JobRequest handles its optional field.)
2. Do NOT touch worker.py or the realism service — both verified correct.
3. Restart the gateway uvicorn process so the new model is live.
4. Hygiene check (yesterday's open item): confirm the RUNNING gateway worker.py process
   is newer than the worker.py file (ls -l --time-style=full-iso vs ps -o lstart= -p
   <PID>); restart it if stale.
5. Report: the diff, the restart confirmations, and which file the realism service's
   stdout goes to (so the owner can tail -F it).

VERIFY (owner runs from the PC after your restart): a fresh pipeline run with the knobs
saved (0.45/0.75) must produce a step3 job whose jobs.request_payload contains
realism_params, and the realism log must show
[realism] denoise=0.45 lora=0.75 (per-request).

------------------------------------------------------------------------------------

---

## TASK 3 — web-tunable video quality: Wan sampling knobs + RIFE 16→32 fps (status: PENDING)

Copy-paste everything between the lines to the pod Claude:

------------------------------------------------------------------------------------

CONTEXT
You maintain the Alluvi GPU-pod services: /workspace/alluvi-gateway (FastAPI app.py +
worker.py) and the video-service on port 8195 (plain `python app.py`, logs to
/workspace/video-service/service.log), which wrap the repo at /workspace/alluvi-clean.
The video-service assembles multishot videos using the repo's modules:
video_pipeline/step_5_video_wan.py (Wan render), step_6_lipsync.py, and
video_pipeline/multishot/stitch.py (final concat).

WHAT CHANGED ON THE ORCHESTRATOR + REPO SIDE (already pushed to GitHub main)
1. The web Run-settings now has four optional video-quality knobs stored on
   tenant_pipeline_config (migration 023): wan_steps, wan_cfg_high, wan_cfg_low,
   interp_target_fps. When set, run_video.py adds to the /video payload:
     "wan_params": {"steps": <int>, "cfg_high": <float>, "cfg_low": <float>}
     "interp_fps": 32
   (any subset of wan_params keys; BOTH keys are OMITTED ENTIRELY when the tenant
   set nothing — payloads without them must behave byte-identically to today).
2. Repo file video_pipeline/step_5_video_wan.py: generate() gained optional kwargs
   sampling_steps / cfg_high / cfg_low. When set, _apply_sampling() writes LITERAL
   values onto the two KSamplerAdvanced expert nodes of wan_api.json (steps + 50%
   split boundaries + per-expert cfg), bypassing the workflow's switch chain exactly
   like `length` already bypasses the math chain. All-None = graph untouched.
   Clamps live in the repo code (steps [4,40], cfg [1.0,8.0]) — do not re-clamp.
3. Repo file video_pipeline/multishot/stitch.py: stitch() gained optional kwarg
   interp_fps. When set, it RIFE-interpolates EACH clip (via the new repo module
   video_pipeline/interpolate_rife.py) BEFORE the punch-in/concat — per-clip on
   purpose: interpolating across a hard cut would create ghost frames. Do NOT move
   the interpolation after the concat.
4. NEW repo file video_pipeline/interpolate_rife.py: runs Practical-RIFE
   (inference_video.py --multi=N --video=...) as a subprocess against a temp copy,
   muxes audio back if RIFE dropped it. Env knobs: RIFE_DIR (default
   /workspace/Practical-RIFE) and RIFE_PYTHON (default: the service's interpreter).

YOUR TASK
1. /workspace/alluvi-gateway/app.py — add to VideoJobRequest (THE TASK-2b LESSON:
   pydantic v2 silently DROPS unknown keys, so without these fields the knobs never
   reach the job payload):
     wan_params: Optional[dict] = None     # Wan sampling knobs: steps, cfg_high, cfg_low
     interp_fps: Optional[int] = None      # RIFE target fps (e.g. 32); None = off
2. /workspace/alluvi-gateway/worker.py — in handle_video, find how the request body
   for the video-service is built. If it forwards the whole payload, nothing to do;
   if it whitelists fields (check carefully — same failure mode as TASK 2b), add
   wan_params and interp_fps to the forwarded body.
3. The video-service (port 8195) app.py —
   a. If it validates the request with a pydantic model, add the same two optional
      fields there too (third place the whitelist trap can bite).
   b. Thread wan_params into EVERY wan.generate(...) call site as kwargs:
        wp = (payload.get("wan_params") or {})
        wan.generate(..., sampling_steps=wp.get("steps"),
                     cfg_high=wp.get("cfg_high"), cfg_low=wp.get("cfg_low"))
      Passing None for all of them is the no-op default — safe unconditionally.
   c. Pass interp_fps to the final stitch call:
        stitcher.stitch(final_clips, stitched, punch_in=..., interp_fps=payload.get("interp_fps"))
      (stitch already accepts **_ignored, so this is safe even before the repo file
      is updated — but update the repo files first anyway, see step 4.)
      Only the multishot path; leave silentfirst untouched.
4. Update the pod's repo copies (remember /workspace/alluvi-clean is NOT a git
   clone — back up each file first, then curl the raw GitHub files):
     cd /workspace/alluvi-clean
     cp video_pipeline/step_5_video_wan.py video_pipeline/step_5_video_wan.py.bak2
     cp video_pipeline/multishot/stitch.py video_pipeline/multishot/stitch.py.bak
     curl -fsSL https://raw.githubusercontent.com/mohamedsahadm786/opensource/main/video_pipeline/step_5_video_wan.py -o video_pipeline/step_5_video_wan.py
     curl -fsSL https://raw.githubusercontent.com/mohamedsahadm786/opensource/main/video_pipeline/multishot/stitch.py -o video_pipeline/multishot/stitch.py
     curl -fsSL https://raw.githubusercontent.com/mohamedsahadm786/opensource/main/video_pipeline/interpolate_rife.py -o video_pipeline/interpolate_rife.py
   Sanity: grep -c "_apply_sampling" video_pipeline/step_5_video_wan.py  → ≥2;
           grep -c "interp_fps" video_pipeline/multishot/stitch.py       → ≥2.
5. Install Practical-RIFE at /workspace/Practical-RIFE:
     git clone https://github.com/hzwer/Practical-RIFE /workspace/Practical-RIFE
   Download the model weights it needs (the train_log/ folder — follow the repo
   README's current model link, v4.x) and install its python deps INTO THE SAME
   VENV THE VIDEO-SERVICE RUNS WITH (/workspace/ai-toolkit/venv) so the default
   RIFE_PYTHON works. SMOKE-TEST standalone before wiring anything:
     cd /workspace/Practical-RIFE && python inference_video.py --multi=2 --video=<any short mp4>
   → confirm an output mp4 appears with double fps (ffprobe) and that audio
   survives (or note that it doesn't — the repo module muxes it back as insurance).
   If the checkout must live elsewhere, export RIFE_DIR in the video-service start
   environment instead.
6. Restart the gateway uvicorn AND the video-service (pkill -f "python app.py" hits
   the video-service; recover per the startup notes: nohup python app.py >
   /workspace/video-service/service.log 2>&1 & — don't forget the &). Remember:
   file replaced ≠ process running.

CONSTRAINTS
- A payload WITHOUT wan_params / interp_fps must produce byte-identical behavior to
  today (same workflow values, no RIFE, 16 fps output). No new defaults anywhere.
- Do not change prompts, the workflow JSON, LatentSync params, storage paths, or any
  other handler. Do not re-clamp values (repo code clamps).
- RIFE must NEVER run across a concat boundary — that placement decision is already
  enforced inside the repo's stitch.py; just pass the kwarg through.
- Multishot only; silentfirst untouched.

VERIFY (the owner drives the runs from the PC/web)
1. Knobless run → video-service log shows NO [rife] and NO "sampling overrides"
   lines; stored mp4 is 16 fps (ffprobe -v error -select_streams v:0
   -show_entries stream=avg_frame_rate -of default=nw=1:nk=1 <file>).
2. Run with interpolation = 32 fps (web) → log shows one "[rife] ... 16 -> 32 fps"
   line PER CLIP, stored mp4 is ~32 fps, audio intact, duration unchanged.
3. Run with steps=28 / cfg_high=4.5 → log shows
   "[step_5_wan] [...] sampling overrides: {'steps': 28, 'split_step': 14, 'cfg_high': 4.5}"
   and the per-shot *_request.json audit contains sampling_overrides; render time
   visibly longer (~1.4x per clip).
4. jobs.request_payload for the video job (owner checks from the PC) contains
   wan_params / interp_fps exactly as saved in the web.

------------------------------------------------------------------------------------
