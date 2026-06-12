# video.md — Video-stage quality roadmap (PENDING work only)

> **Purpose:** the remaining plan for taking the video stage to ad-grade output.
> Completed phases (V1 motion doctrine, V2 pose grounding, V3 shot plan + the
> hand-zoom/scored QC gate and pose-fidelity tightenings) are in git history —
> see commits `2e7aa2e`, `6476683`, `71903ee` and `update.md`. This file holds
> only what is still TO DO, ordered by recommended build sequence.

---

## How the stage works today (1-paragraph refresher)

Run → Opus + `rules/script.md` + DB data → per-shot `{dialogue, wan_motion_prompt,
wan_negative_prompt}` (+ `shot_plan`, + POSE GROUND TRUTH from the step-2 prompt)
→ F5-TTS speaks → Wan 2.2 I2V animates the SAME step3 photo per shot (768x1344,
16 fps, ≤81 frames ≈ 5 s, 20 steps, CFG 3.5/1.0 two-expert ladder) → LatentSync
mouth → stitch/frame-join + intro/outro silences → mp4. Every shot restarts from
the same still; gestures are distributed once-per-video by the shot plan.

## Research facts to remember (verified June 2026)

- Wan text encoder: 512-token hard cap, quality cliff past ~320 tokens → keep
  motion prompts ≤ 200 words (rule-book enforced).
- Native: 480P/720P @ 24 fps; our 768x1344 ≈ the 720P pixel budget; 16 fps/81
  frames is a Wan-2.1-era throughput convention.
- Render cost: ~9 min per 5 s 720P clip on H100-class (fp8: 2–4 min);
  full-potential settings ≈ 2–3x today's render time.
- Last-frame chaining drifts (color/quality accumulate per hop; ≤3–4 hops max).
- Wan 2.2 FLF2V (first+last frame) is open and works in ComfyUI.
- InfiniteTalk (open, Wan-based): image + full audio → ONE continuous video,
  81-frame chunks with 25-frame carry-over; built-in continuity + audio-driven
  body motion. Successor: LongCat-Video-Avatar-1.5 (May 2026).
- LoRA fine-tuning needs VIDEO clips (50–300, captioned); teaches domain style,
  does NOT close the base-model gap to Seedance-class models. Ratings data is
  quietly becoming the training set.

---

## PENDING — in recommended order

### P1. Open verifications (no build — just run & look)
- [ ] Watch the latest rendered videos (V1–V3 as pixels): product stays planted,
      calm motion, directed cuts, intro/outro silences correct.
- [ ] One run with the saved realism knobs (Save → THEN Run; pod gateway worker
      must be the post-TASK-2 process). Expect `[pipeline] realism knobs: {...}`
      on the PC worker terminal and `[realism] denoise=0.45 lora=0.75
      (per-request)` on the pod → then mark claudeAI.md TASK 2 DONE.
- [ ] One 20 s run (4 shots) to see the full shot-plan arc in a real render.
- [ ] Watch the new scored hand gate live: `qc_checks` should show
      `hand_render_quality` per attempt; tune `QC_HAND_QUALITY_MIN` (default 7)
      if it over/under-fires across a few runs.

### P2+P3. Web-tunable video quality: RIFE 16→32 fps + Wan sampling knobs
**(repo side BUILT 2026-06-12 — pending: migration 023 apply + pod TASK 3 + live A/B)**

Built as ONE chain (migration 023 → Run-settings "Video quality (advanced)" →
`run_video.resolve_controls` → payload `wan_params` {steps, cfg_high, cfg_low} +
`interp_fps` (keys OMITTED when unset) → gateway `VideoJobRequest` → worker →
video-service → `step_5_video_wan._apply_sampling` (literal writes onto the two
KSamplerAdvanced expert nodes) + `multishot/stitch interp_fps` → NEW
`video_pipeline/interpolate_rife.py` (Practical-RIFE subprocess, PER CLIP before
concat — never across a cut). Verified by `orchestrator/_video_paramtest.py`
(7 cases incl. byte-identical no-op on the real wan_api.json) + web build.

**CORRECTION to the old table (read from the real graph):** both experts share
ONE CFG = 3.5 via a switch node; the "low expert 1.0" previously quoted here is
actually the disabled lightx2v fast-mode CFG. Defaults: steps 20, split 10,
CFG 3.5/3.5, 16 fps. Knob clamps: steps [4,40], cfg [1.0,8.0]; split always 50%.

Remaining: apply migration 023; pod TASK 3 (gateway+worker+service pass-through,
Practical-RIFE install, deploy repo files, restarts); A/B: one run RIFE-only
(32 fps, same sampling), then one run steps=28/cfg_high=4.5; verify by
`[pipeline] video knobs:` line, `[rife]`/`sampling overrides` pod logs, ffprobe
fps of the stored mp4, and side-by-side eyeball. Costs ~2–3x render time when
sampling knobs are raised → per-tenant, never default.

### P4. Source-side hand refinement (raises the floor the new QC gate measures)
The realism pass (denoise 0.30) refines texture but cannot reconstruct fingers —
mediocre "AI hands" pass through generation and now FAIL the scored QC gate,
burning retries. A dedicated hand-fix step (hand-detect → inpaint at higher
denoise; standard ComfyUI pattern, e.g. MeshGraphormer/hand detailer) after
step2 or step3 would fix hands instead of rejecting them. Pod-side workflow +
claudeAI.md task when prioritized.

### P5. Video QC gate (nothing judges the rendered VIDEOS today)
Sample N frames per shot → the same Opus vision QC (anatomy + product integrity
+ the hand tiles) → fail = re-render the shot with a new seed (retry loop like
the image gate). Render time per retry is the cost. Design choice to make:
per-shot gate (cheap retries) vs final-video gate (simpler).

### P6. Config-snapshot at trigger time (kills the Run-vs-Save race)
Twice today a run launched 1–2 s before the settings save committed and silently
used stale knobs. Proper fix: `trigger-pipeline` (or the web Run click) snapshots
the just-saved config INTO the job payload; `run_pipeline` prefers the snapshot
over a fresh DB read. Makes the race structurally impossible.

### P7. InfiniteTalk proof-of-concept (the strategic quality jump)
Pod experiment, NOT pipeline: install InfiniteTalk, run ONE scenario manually
(step3 image + full F5-TTS audio) → one continuous audio-driven video. Compare
side-by-side vs the multishot output. If it wins → new video mode
`infinitetalk` (LatentSync skipped for talking shots; punch-ins/cuts in post via
the silent-first machinery). This is the credible path to "Seedance-feel"
presenter ads + true positional continuity + speech-coupled gestures.

### P8. PARKED (evidence-gated — do not start without a trigger)
- **Last-frame chaining** (true positional continuity): only if continuous-camera
  looks are demanded before P7's verdict; ≤4 hops + color-match + drift QC.
- **FLF2V keyframe storyboards**: shelf until cinematic multi-angle ads are a
  real requirement.
- **Wan 2.2 LoRA fine-tune**: only if ratings still show a motion-quality gap
  after P2–P7; train on the accumulated high-rated clips.

---

## Standing decisions (never re-litigate)

- No "static/frozen" terms in ANY negative — stillness comes from holds +
  restraint words, never from negatives.
- No product-form guesses in motion prompts — generic "the product" only.
- No frame-accurate gesture-to-audio sync via prompting — that is P7's job.
- A gesture happens at most ONCE per video; most beats are presence-only.
- Hands: state every hand exactly as the pose ground truth describes.
- No fine-tuning before the cheaper levers are measured.
- Save settings → THEN Run (until P6 removes the race).
