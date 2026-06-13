# pending.md — what is LEFT TO DO (rewritten 2026-06-13, end of session)

> **How to use:** next session, say "read update.md" (v7 section = how we got here +
> full context), then work this list top-down. COMPLETED work has been removed — this
> file is only open work. Items reference claudeAI.md TASK numbers (pod-side prompts),
> video.md P-numbers (roadmap), and finding.md (the RIFE write-up).

## Status legend
⏳ waiting on something   🔨 to build   👁 watch/judge (no building)   🧹 housekeeping

---

## 1. ⏳ ACTIVATE the browser-codec guard — ONE video-service restart left
The web-safety guard (`video_pipeline/web_normalize.py` → `ensure_web_playable`) is
**deployed to disk** on the pod (repo files curled; the guard call folded into the
COMMON return path of `/workspace/video-service/app.py`; `py_compile` clean — see
claudeAI.md TASK 5 + update.md v7). It is **NOT YET ACTIVE**: the running `:8195`
process predates the edit. Owner is bundling the restart with the new-ComfyUI pod work.
- **Restart command** (after the ComfyUI work, when no run is in-flight):
  ```
  kill $(ss -tlnp | grep ':8195' | grep -oP 'pid=\K[0-9]+' | head -1)
  cd /workspace/video-service && source /workspace/ai-toolkit/venv/bin/activate && ALLUVI_REPO=/workspace/alluvi-clean nohup python app.py > /workspace/video-service/service.log 2>&1 &
  ```
  Verify: `ss -tlnp | grep ':8195'` listening + `tail -n 20 service.log` clean boot.
- **⚠️ UNTIL the restart: keep 32 fps / RIFE OFF.** No-RIFE output is H.264 natively
  (proven — video d96010db renders in the browser), but a RIFE run before the restart
  writes mp4v → black screen again. After the restart, the guard is live and 32 fps
  can be turned back on (it then guarantees H.264 even with RIFE).
- After verifying: mark claudeAI.md TASK 5 DONE.

## 2. 🔨 Orchestrator robustness — stop the false "run complete"
`run_pipeline._poll` does `requests.get(...).raise_for_status()` with NO error
handling, and Phase C swallows the exception as "video ✗ FAILED" → `pipeline_run`
finishes `succeeded` even though no video was produced (update.md v7 #3; bit us when a
pod worker restart caused a transient gateway error mid-render). Fix: make `_poll`
TOLERATE transient gateway errors (retry/backoff on connection + 5xx, only give up
after N consecutive failures), so a pod hiccup never false-completes a run. Until then:
**"run complete" in the web = the `pipeline_run` job status, NOT proof a video exists**
— verify via the `videos` table / the video sub-job.

## 3. ⏳/👁 RIFE seam-morph artifact — design decision (knob OFF, safe)
**→ FULL write-up + 3 fix options in `finding.md` (read it before discussing).**
RIFE wiring is 100% green; the OUTPUT was rejected for the seam-morph artifact (RIFE
interpolating across hidden frame_join seams = a visible warp). Knob is OFF → pipeline
is back to proven pre-RIFE behavior; nothing broken/urgent. Options: (1) seam-aware
RIFE (surgical, keeps near-free cost), (2) Wan native 24 fps (+50% render, structurally
clean), (3) RIFE only on single-chunk footage (cheap stopgap). Recommended sequence:
keep OFF → finish the P7 InfiniteTalk verdict first (may change the whole video
strategy) → then choose Option 1 vs 2. (The mp4v half of finding.md is now item 1.)

## 4. ⏳ P6 config-snapshot — live verify still pending
Code shipped (`5b1f5a5`). REMAINING: restart `run_worker.py` on the PC (the old
process doesn't pass `--job-id`), hard-refresh the web (Ctrl+Shift+R), then one web run
must print `[pipeline] config source: snapshot (frozen at Run click)` in the worker
terminal and carry `config_snapshot` in the job payload. Then the "Save → wait → Run"
rule is obsolete → mark video.md P6 DONE.

## 5. 🔨 Close TASK 3/3b verification (A/B runs, one knob at a time)
- **Knobless control run**: all video-quality fields empty → NO `[rife]`/`sampling
  overrides` lines, `videos.fps`=16/25 (control + int fallback). Safe to run now.
- **Sampling run**: steps=28, CFG motion=4.5 → log shows `sampling overrides: {...}`,
  ~1.4x render. Safe to run now (no RIFE).
- **Silentfirst + interp run**: mode=silentfirst + steps + interp 32 → **MUST WAIT
  until the codec guard is active (item 1)** — else mp4v black. Then expect sampling
  lines on the silent renders + exactly ONE `[rife]` line at the end.
- Then mark claudeAI.md TASK 3 + 3b DONE and video.md P2+P3 DONE.

## 6. ⏳ TASK 4 / P7 — InfiniteTalk PoC (prompt already given to the pod Claude)
The isolated-experiment prompt (claudeAI.md TASK 4 + addendum) was handed to the pod
Claude; its reply was NOT yet reviewed. Next: paste its output to repo-Claude for
review. Owner side: upload `anchor.jpg` (a step3 image) + `dialogue.txt` into
`/workspace/infinitetalk-poc/input/` via Jupyter, run ONLY when the pipeline is idle.
Deliverable = one continuous video + verdict vs multishot (lipsync, gesture-speech
coupling, identity, PRODUCT stability, render cost) → adopt / evaluate more / reject.
This verdict feeds the RIFE decision (item 3) and P8.

## 7. 👁 video.md P1 leftovers (watch & judge)
- Watch recent rendered videos as pixels (patio, gym bench, office desk, pilates mat).
- One 20 s / 4-shot run to see the full shot-plan arc in a real render.
- Watch `qc_checks.hand_render_quality` across runs; tune `QC_HAND_QUALITY_MIN`
  (env, default 7) if it over/under-fires. (Recent runs took 2–3 step2 attempts — the
  stricter hand gate burning retries, as predicted. Expected until P4 ships.)

## 8. 🔨 The build queue after that (video.md order)
- **P4 — source-side hand refinement** (hand-detect → inpaint after step2/step3;
  raises the floor the QC hand gate measures; brings the fail rate back down).
- **P5 — video QC gate** (nothing judges rendered VIDEOS today; per-shot vs final-video
  gate — design choice).
- **P8 — parked** (last-frame chaining / FLF2V / LoRA fine-tune; evidence-gated; P7's
  verdict feeds this — do not start without a trigger).

## 9. 🔨 Image-side root fix (oldest open quality item)
`held_with_phone` scenarios still genuinely render a 3rd hand (Qwen keeps the phone AND
adds two box-hands). QC now fails these correctly → they burn retries. Root fix = step-2
prompt pattern for phone scenarios (likely: single box-hand only, never instruct the
phone hand).

## 10. 🧹 Housekeeping
- **Commit + push the doc updates** from this session if wanted (update.md v7,
  pending.md, finding.md, claudeAI.md TASK 5 status) — currently local-only; not needed
  on the pod, but keeps git history current.
- Pod `.bak` files left by TASK 5 curls (`stitch.py.bak3`, `interpolate_rife.py.bak`,
  `app.py.bak_*`) — delete once the guard restart is verified good.
- Delete untracked temp helpers when the tuning round closes:
  `orchestrator/_qc_*.py`, `_show_script.py`, `_run_debug.py`, `_dump_prompts.py`,
  `_realism_paramtest.py`, `_video_paramtest.py`, `_p6_paramtest.py`,
  `_rerun_gym_test.py`, `orchestrator/_qc_audit/`.
- `supabase_pipeline.zip` (untracked in repo root) — confirm needed or deletable.
- Replace the green-background test angle photo with a real industry 3/4 shot.
- GPU-host-from-DB (v3 item): orchestrator still reads GATEWAY_URL from `.env`; the web
  Settings GPU field is stored but unused. Update `.env` on every pod restart until wired.
- Web product-brief form: add a hint to enter REAL package measurements (re-saving the
  brief regenerates packaging).
