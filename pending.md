# pending.md — what is LEFT TO DO (rewritten 2026-06-12, end of session)

> **How to use:** next session, say "read update.md" (v6 section = full context of how
> we got here), then work this list top-down. Everything COMPLETED has been removed —
> this file is only open work. Items reference claudeAI.md TASK numbers (pod-side
> prompts) and video.md P-numbers (roadmap).

## Status legend
⏳ waiting on something   🔨 to build   👁 watch/judge (no building)   🧹 housekeeping

---

## 1. ✅ RIFE runs AUDITED + owner verdict: ARTIFACTS — knob turned OFF for now
**→ FULL write-up + fix options in `finding.md` (read it before discussing).**
Wiring 100% green (payload, `[rife]` lines, `videos.fps`=50 measured int, assembly
params). Discoveries (full detail in update.md v6 + this audit):
- **LatentSync outputs 25 fps** (Wan renders 16; lipsync resamples with duplicated
  frames). RIFE doubles what it receives → 50 fps, not 32. Knob = "2x smoothness".
- **OWNER VERDICT (2026-06-12 evening): RIFE output REJECTED in current form**:
  1. **Seam-morph artifact**: shots >5s are built from frame_join'd Wan chunks;
     those internal seams were invisible single-frame cuts at 16/25 fps, but RIFE
     interpolates ACROSS them → a 1-2 frame morph/warp = "sudden artificial
     shifting" (worst in silentfirst — every seam in the whole take morphs).
     The shot-to-shot cuts are NOT the problem (RIFE is per-clip there).
  2. **mp4v / browser-codec bug**: Practical-RIFE writes OpenCV mp4v → browsers show
     BLACK +audio-only (Supabase/web preview); local players fine. Bit the silentfirst
     final (multishot escaped via the punch-in libx264 re-encode). NOT just a RIFE
     issue — any path whose final step doesn't re-encode can ship a bad codec.
     **FIXED + HARDENED in repo (2026-06-13)**: new `video_pipeline/web_normalize.py`
     (`ensure_web_playable`) wired into `stitch.stitch()` + silentfirst `build_one()`
     guarantees H.264/yuv420p on every final video. **Deploy = claudeAI.md TASK 5**
     (curl 3 files + ONE guard call in the pod's `_build_silentfirst` + restart);
     safe to deploy NOW even with RIFE off. Full write-up in finding.md §4.
- **ACTION TAKEN: Frame interpolation = Off** in Run settings (old behavior back).
- NEXT (design, when prioritized): **seam-aware RIFE** — frame_join returns seam
  timestamps; split at seams → interpolate segments → rejoin with clean cuts.
  ALTERNATIVE: P3's native 24 fps Wan render (+50% cost, zero seam issues) may be
  the better smoothness lever. Decide after P7's verdict.

## 2. ⏳ P6 live verification (config snapshot — code all shipped, one restart left)
The PC's `run_worker.py` process predates the P6 commit and doesn't pass `--job-id`.
AFTER the in-flight run finishes:
1. PC worker terminal → Ctrl+C → `python run_worker.py` again.
2. Hard-refresh the web app (Ctrl+Shift+R — new bundle sends the snapshot).
3. Next web run: the worker terminal must print
   `[pipeline] config source: snapshot (frozen at Run click)` and the job's
   request_payload must contain `config_snapshot`. Once seen → the
   "Save → wait → Run" rule is officially obsolete; mark P6 DONE in video.md.

## 3. 🔨 Close TASK 3/3b verification (remaining A/B runs, one knob at a time)
- **Knobless control run**: all video-quality fields empty → NO `[rife]`/`sampling
  overrides` lines, `videos.fps`=16 (proves zero regression + the int fallback).
- **Sampling run**: steps=28, CFG motion=4.5 → log shows
  `sampling overrides: {'steps': 28, 'split_step': 14, 'cfg_high': 4.5}`, ~1.4x render.
- **Silentfirst run**: mode=silentfirst + steps + interp 32 → sampling lines on the
  silent renders and **exactly ONE** `[rife]` line at the end (not per clip).
- Then mark claudeAI.md TASK 3 + 3b DONE and video.md P2+P3 DONE.

## 4. ⏳ TASK 4 / P7 — InfiniteTalk PoC (prompt already given to the pod Claude)
The isolated-experiment prompt (claudeAI.md TASK 4) was handed to the pod Claude at
session end; its reply was NOT yet reviewed. Next session: paste its output to
repo-Claude for review. Owner side: upload `anchor.jpg` (a step3 image from
Publishing) + `dialogue.txt` (2–4 sentences) into `/workspace/infinitetalk-poc/input/`
via Jupyter, run ONLY when the pipeline is idle. Deliverable = one continuous video +
verdict vs multishot (lipsync, gesture-speech coupling, identity, PRODUCT stability,
render cost) → adopt / evaluate more / reject.

## 5. 👁 video.md P1 leftovers (watch & judge)
- Watch the recent rendered videos (V1–V3 as pixels): patio, gym bench, office desk.
- One 20 s / 4-shot run to see the full shot-plan arc in a real render.
- Watch `qc_checks.hand_render_quality` across runs; tune `QC_HAND_QUALITY_MIN`
  (env, default 7) if it over/under-fires. NOTE: today's run took 2 step2 attempts —
  the stricter hand gate burning a retry, as predicted. Expected until P4 ships.

## 6. 🔨 The build queue after that (video.md order)
- **P4 — source-side hand refinement** (hand-detect → inpaint after step2/step3;
  raises the floor the QC hand gate measures; brings the fail rate back down).
  Pod-side workflow + a claudeAI.md task when prioritized.
- **P5 — video QC gate** (nothing judges rendered VIDEOS today; design choice:
  per-shot vs final-video gate).
- **P8 — parked** (last-frame chaining / FLF2V / LoRA fine-tune; evidence-gated,
  do not start without a trigger — P7's verdict feeds this).

## 7. 🔨 Image-side root fix (oldest open quality item)
`held_with_phone` scenarios still genuinely render a 3rd hand (Qwen keeps the phone
AND adds two box-hands). QC now fails these correctly → they burn retries. Root fix =
step-2 prompt pattern for phone scenarios (likely: single box-hand only, never
instruct the phone hand).

## 8. 🧹 Housekeeping
- Delete the untracked temp helpers when the tuning round closes:
  `orchestrator/_qc_*.py`, `_show_script.py`, `_run_debug.py`, `_dump_prompts.py`,
  `_realism_paramtest.py`, `_video_paramtest.py`, `_p6_paramtest.py`,
  `_rerun_gym_test.py`, `orchestrator/_qc_audit/`.
- `supabase_pipeline.zip` appeared untracked in the repo root — owner to confirm
  whether it's needed or deletable.
- Replace the green-background test angle photo with a real industry 3/4 shot.
- GPU-host-from-DB (v3 item): orchestrator still reads GATEWAY_URL from `.env`;
  the web Settings GPU field is stored but unused. Update `.env` on every pod restart
  until wired.
- Web product-brief form: add a hint to enter REAL package measurements
  (re-saving the brief regenerates packaging).
