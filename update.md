# update.md — Alluvi Console Web Build (handoff / context doc)

> **Purpose:** read this first when resuming work on the web app. It captures what
> was built, why, how it wires to the backend, the full file map, how to run it,
> how it was deployed, current status, and known issues. The pipeline backend
> (`orchestrator/`, schema `db/migrations/001`) already existed; this effort added
> the **web UI layer** plus the additive backend glue (Storage RLS, an audit
> table, a Vault RPC, and Edge Functions).

---

## v6 — knob-chain completion: realism verified, video quality knobs (P2+P3), config snapshot (P6), InfiniteTalk PoC launched (2026-06-12 session — READ THIS FIRST)

> This session closed TASK 2 (realism knobs) after finding+fixing the gateway bug that
> blocked it, built the FULL web-tunable video-quality chain (Wan sampling knobs + RIFE
> 16→32 fps, both video modes), built P6 (config snapshot — kills the Save→Run race),
> and launched the P7 InfiniteTalk PoC on the pod. Commits `b33b3fe` → `d14edb9` (+
> this handoff), all pushed. Where this disagrees with v5 below, this wins.
> **`pending.md` is REVIVED and CURRENT again — it is the to-do list; read it after this.**

### 1. TASK 2 (realism knobs) — root-caused, fixed, VERIFIED live
- The knobs never arrived because the GATEWAY's pydantic model dropped them:
  `Step3JobRequest` had no `realism_params` field and the handler stores
  `req.model_dump()` — **pydantic v2 silently discards unknown keys**. Diagnosed via
  DB evidence (config row correct + payload missing the key), confirmed by the pod
  Claude from its source copies, fixed as TASK 2b (one field + gateway restart).
- VERIFIED: `jobs.request_payload.realism_params = {denoise:0.45, lora_strength:0.75}`
  and pod log `[realism] denoise=0.45 lora=0.75 (per-request)`. TASK 2 + 2b DONE.
- **THE LESSON (now doctrine, baked into TASK 3):** every new payload key must be
  added at ALL THREE whitelist hops — gateway request model, worker forward body,
  service request model — or it dies silently at the first one.

### 2. P2+P3 — web-tunable video quality, shipped END-TO-END (repo + pod deployed)
- **Migration 023 (APPLIED)**: `tenant_pipeline_config.wan_steps / wan_cfg_high /
  wan_cfg_low / interp_target_fps` (nullable; NULL = byte-identical today-behavior).
- **Web**: Run-settings → "Show video quality (advanced)": Wan steps (placeholder 20),
  CFG motion expert / CFG detail expert (3.5), Frame interpolation dropdown (Off/32).
- **Chain**: `run_video.resolve_controls` → payload `wan_params{steps,cfg_high,cfg_low}`
  + `interp_fps` (keys OMITTED when unset) → gateway `VideoJobRequest` fields → worker
  conditional forward → video-service → `step_5_video_wan._apply_sampling` writes
  LITERAL values onto the two KSamplerAdvanced expert nodes (50% split recomputed;
  clamps steps [4,40], cfg [1.0,8.0]) + `multishot/stitch interp_fps`.
- **GROUND-TRUTH CORRECTION** (read from wan_api.json): both experts share ONE CFG=3.5
  via a switch node; the old "low expert 1.0" claim was the disabled lightx2v
  fast-mode CFG. Defaults: steps 20, split 10, CFG 3.5/3.5, 16 fps.
- **NEW `video_pipeline/interpolate_rife.py`**: Practical-RIFE subprocess per clip
  (temp-dir isolation, audio-mux insurance; env RIFE_DIR / RIFE_PYTHON).
  **RIFE placement doctrine**: multishot = per clip in stitch BEFORE concat (never
  across a cut → ghost frames); silentfirst = ONCE on the final lip-synced take,
  AFTER lipsync (else 2x LatentSync cost), BEFORE extend_tail (uniform fps).
- **Pod (TASK 3 + 3b, deployed + restarted)**: gateway/worker/video-service updated
  by the pod Claude, repo-reviewed (2 real bugs caught: `videos.fps` hardcoded 16 —
  now measured via ffprobe and **int-rounded** (videos.fps is an INTEGER column);
  assembly `media_generations.params` now records wan_params/interp_fps). Practical-
  RIFE installed at `/workspace/Practical-RIFE` with **v4.25 weights** — use the
  README "Trained Model" Google-Drive links, NOT the "Model training" links (those
  are code-only, no .pkl; cost us one wrong 88KB download). Smoke test passed
  (24.98→49.96 fps, audio merged).
- Verified by `orchestrator/_video_paramtest.py` (7 cases incl. byte-identical no-op
  on the real graph) + web build. **Verification runs still pending — see pending.md #1/#3.**

### 3. P6 — config snapshot at Run click (Save→Run race KILLED) — built, restart pending
- `Dashboard.handleRun` already awaited `saveRunConfig`; its upsert RESPONSE (= the
  committed row) is now passed to `run(savedRow)` → `trigger-pipeline` stores it as
  `request_payload.config_snapshot` (server-side row fetch = fallback for old
  bundles; function REDEPLOYED) → `run_worker` passes `--job-id` → `run_pipeline.
  get_run_config` prefers the snapshot; fail-open to live DB read (CLI/legacy/error
  = today's behavior). `''` values dropped defensively. Proof line in the worker
  terminal: `[pipeline] config source: snapshot (frozen at Run click)`.
- Save button/DB row UNCHANGED (row stays the persistent store; snapshot = frozen
  copy per run = free audit trail). Verified by `_p6_paramtest.py` (4 cases).
- **⏳ NOT live yet**: the PC `run_worker.py` PROCESS predates the commit (file
  replaced ≠ process running!) — restart it after the in-flight run, hard-refresh
  the browser, then one run must show the snapshot line (pending.md #2).

### 4. P7 — InfiniteTalk PoC launched (claudeAI.md TASK 4)
- Full isolated-experiment prompt written + handed to the pod Claude (reply not yet
  reviewed). Isolation contract: everything under `/workspace/infinitetalk-poc/`
  incl. a DEDICATED venv; no shared-venv installs; no existing-file modifications;
  no daemons; disk/VRAM gates; "blocked because X" = a valid outcome.
- Owner inputs via Jupyter: `input/anchor.jpg` (a step3 image) + `dialogue.txt`
  (audio synthesized with the pipeline's own F5 voice for a fair comparison).

### Operational discoveries (2026-06-12 — will save hours)
- **Supabase secret key from PowerShell**: `Invoke-RestMethod` is rejected
  ("Forbidden use of secret API key in browser" — browser-like UA); use `curl.exe`
  with BOTH `Authorization: Bearer` and `apikey:` headers.
- **The gateway pydantic whitelist** is where payload keys silently die (TASK 2's
  root cause; pre-empted at all three hops in TASK 3).
- **RIFE/stitch logs print only at the END of video assembly** — a silent
  service.log mid-render is normal, not a wiring break (cost us one false alarm).
- **Shared-venv poisoning**: Practical-RIFE's `requirements.txt` pin DOWNGRADED
  numpy 1.26.4→1.23.5 in `/workspace/ai-toolkit/venv` (would break pandas/
  scikit/albumentations consumers). Fix: `pip install numpy==1.26.4` back, then
  patch sk-video's dead `np.float`/`np.int` aliases via sed instead. THIS is why
  TASK 4's isolation contract mandates a dedicated venv.
- Pod has no `unzip` (use `python -m zipfile -e`) and no `fuser` (kill by port:
  `kill $(ss -tlnp | grep ':8195' | grep -oP 'pid=\K[0-9]+' | head -1)`).
- **Jupyter uploads are async** — verify the byte size matches the PC file before
  extracting (a mid-upload zip gave BadZipFile; same size = upload finished).
- `pgrep -f "python app.py"` finds ONLY the video-service; find the realism service
  by port (`ss -tlnp | grep 8194`); its log is `/workspace/realism_service_8194.log`.
- Today's run took 2 step2 attempts (hand gate burning a retry) — expected until P4.
- Repo root = the GitHub repo root (`raw.githubusercontent.com/mohamedsahadm786/
  opensource/main/<path>` works directly for pod curls).

### Where to resume (priority order)
**→ `pending.md` is the canonical list now (rewritten this session).** Short form:
1. Audit the in-flight RIFE run (fps=32 in DB, [rife] lines, eyeball smoothness).
2. Restart PC run_worker + browser hard-refresh → verify P6 snapshot line.
3. TASK 3/3b closure runs (knobless control / sampling / silentfirst).
4. Review the pod Claude's TASK 4 (InfiniteTalk) reply; run the PoC when idle.
5. Then: P1 leftovers (20s run, hand-gate tuning) → P4 hand refinement → P5 video QC.

---

## v5 — QC debug tooling + QC rebalance + realism knobs + VIDEO overhaul (2026-06-11 session)

> This session: built the failed-image archive + per-attempt debugging, audited and
> REBALANCED QC (it was over-strict in two ways and under-strict in one), made the
> realism stage web-tunable, and executed the first three phases of the video-quality
> overhaul (motion doctrine, pose grounding, shot plan) plus a scored hand-quality QC
> gate. Commits `0a13f77` → `81e62e8`, all pushed. Where this disagrees with v4 below,
> this wins. **The video-stage roadmap now lives in `video.md` (PENDING-only, P1–P8) —
> read it after this.** `pending.md` is STALE — ignore it.

### 1. Per-attempt debugging infrastructure (migration 021 — APPLIED)
- **Pod TASK 1 DONE + verified**: `image_generations` now gets one row PER Step-2 QC
  attempt (correct `attempt_number`, fresh seeds) — pod worker.py fix by pod-Claude.
- **`qc-failed` bucket (private, RLS read policy)**: every QC-FAILED composite is archived
  by `run_pipeline._archive_qc_fail` BEFORE the pod overwrites the deterministic
  `.../step2.jpg`. `qc_checks` gained `step2_prompt` (the exact Qwen prompt of that
  attempt) + `image_evaluated_path` (the archived image). TEMP tuning aid — strip at
  deployment (migration header has instructions).
- **Web**: Image-debug shows per-attempt prompts (`attempt N (FINAL — made the stored
  image)`) + a "QC attempts" section with the rejected-image previews, issues, and
  per-attempt prompts. Lightbox bottom line shows `QC: <status> (N attempts)`
  (`outputs.attempts` was already written; `usePublishing` now selects it).

### 2. QC audit (16 attempts) → strictness REBALANCE (`7e73ed8` + `71903ee`)
- Audit verdicts vs owner's eye: the judge **hallucinated a 3rd hand** in
  `held_with_phone` MIRROR scenes (counted from intent left/right logic, census even said
  "connects to NO VISIBLE ARM") and **hallucinated text garble** on clean wordmarks (the
  tiny pen-inset label is ALWAYS garbled at render size and gets attributed to the
  headline; self-contradictory issues like "'TIRZEPATIDE' rendered as 'TIRZEPATIDE'" are
  the tell). The proportions gate worked perfectly (matched the owner both times).
- **Rebalance (rules/qc.md + qc.py)**: TUNING-PHASE text rule — brand/product wordmarks
  at ~85-90% fidelity (recognizable English letters; "ALUVI"/"TIRZEPATDE" pass), judge
  must TRANSCRIBE the wrong letters as evidence or it passes; pen inset/seal/badge only
  need similar shapes; explicit `<!-- AFTER FINE-TUNING -->` re-hardening notes in the
  file. Mirror-scene reflections get NO census number (qc.py census arithmetic also
  filters reflection entries); count pixels never pose-logic; left-vs-right hand swap
  never matters; grip-sense check (palm/finger contact must make mechanical sense;
  torso-brace with one hand = valid); surface scenes also check furniture coherence.
  ZERO tolerance unchanged for real leftover hands/limbs/floating products.
- **Scored hand gate (owner-flagged: clustered/clubbed fingers passed QC)**: QC now
  attaches two deterministic ~3x ZOOM TILES of the torso band to every judge call
  (full-frame hands are ~80px — fusion is invisible; a model-located bbox HALLUCINATED
  and was discarded). New `hand_render_quality` 1-10 scored on the tiles;
  `< QC_HAND_QUALITY_MIN` (env, default 7) fails. The binary "malformed?" question gets
  rationalized on borderline clusters; the scale does not (patio image honestly scored
  6/10 → FAIL; clean yoga hands PASS; real 3-hand gym composite still FAILS).
- VERIFIED regressions on archived images each step. Expect a higher QC fail rate until
  P4 (source-side hand refinement, see video.md) ships.

### 3. Realism stage web-tunable (migration 022 — APPLIED; TASK 2 deployed, verify pending)
- Chain: `tenant_pipeline_config.realism_denoise / realism_lora_strength` (nullable;
  null = pod defaults 0.30/0.7) → Run-settings fields under the Stage-3 toggle →
  `run_pipeline` `REALISM_PARAMS` → step3 payload `realism_params` (omitted when unset —
  payload-equality tested) → pod worker forwards → realism service sets
  `step_3_realism.DENOISE/LORA_STRENGTH` UNCONDITIONALLY per request (clamped
  [0.05,0.95]/[0,2]; startup defaults captured once; `[realism] denoise=… (per-request|
  defaults)` log line). Pod files deployed (worker.py + realism app.py).
- **⏳ NOT yet verified live**: the pod gateway-worker PROCESS predates the new file
  (started 11:35, file written 16:41 — `file replaced ≠ process running`!). RESTART the
  pod worker, then Run (knobs already saved: 0.45/0.75) → expect
  `[pipeline] realism knobs: {...}` on the PC worker terminal +
  `[realism] denoise=0.45 lora=0.75 (per-request)` on the pod → then mark claudeAI.md
  TASK 2 DONE.
- **Run-vs-Save RACE (bit us twice)**: the pipeline reads Run-settings ONCE at launch;
  a Save committing 1-2 s after the Run click is silently missed. Rule: **Save → then
  Run.** Proper fix queued as P6 in video.md (config snapshot into the job payload).

### 4. VIDEO overhaul V1–V3 executed (full roadmap + research facts in `video.md`)
- **V1 (`2e7aa2e`)**: `BASE_NEGATIVE` in `video_pipeline/step_5_video_wan.py` — REMOVED
  the Wan template's anti-stillness terms (静态/静止/静止不动的画面 were literally
  demanding motion → products lifted/floating), ADDED anti-float terms. Pod copy
  curl'd + video-service (:8195) restarted. `rules/script.md` §5: iron rule (the product
  NEVER moves independently; hand+product one unit), safe wrist-tilt-only showing
  gesture, NAMED camera grammar (explicit cinematography language, dynamic — not a fixed
  list), ≤200-word motion prompts (Wan encoder cliff ~320 tokens), calibration example.
- **V2 (`6476683`)**: POSE GROUND TRUTH — the script brain now receives the step-2 image
  prompt of the stored composite (`RV.get_pose_prompt`: image_generations highest
  attempt, fallback qc_checks.step2_prompt) + the scenario's `grip_or_placement`. Wired
  in BOTH paths (run_video CLI + run_pipeline Phase C). Tightened after a leak on its
  first real run (`71903ee`): state EVERY hand EXACTLY as the ground truth describes —
  never simplify a two-handed hold to one (dry-run verified: "right hand cupping the
  bottom edge, left hand steadying the top corner, exactly as in the photo").
- **V3 (`6476683`)**: `script.md` §4b SHOT PLAN — the model directs the sequence as ONE
  performance: each gesture happens AT MOST ONCE per video (in the beat whose dialogue
  calls for it), most beats are presence-only ("real creators just stand and talk"),
  energy follows the narrative arc, the camera provides the variety (a different named
  move per shot). `shot_plan` added to the output JSON (additive). 4-shot dry-run
  verified; first real web run (patio, 2 shots) confirmed all of it live.
- **Stall-pill fix (`49e5f5b`)**: `useRunProgress` — video/script stage gets a 3 h stall
  window (was 30 min → false "Run stalled" during every normal video render);
  the stall flag self-clears when activity resumes.

### Operational discoveries (save hours tomorrow)
- **`file replaced ≠ process running`** — every pod .py swap needs its service/worker
  process restarted; check with `ls -l --time-style=full-iso <file>` vs
  `ps -o lstart= -p <PID>`.
- The video-service runs as plain `python app.py` — `ps aux | grep 8195` finds NOTHING;
  use `pkill -f "python app.py"` (it's the only one) and restart per the startup notes
  (`nohup … &` — don't forget the `&`; recover a foreground start with Ctrl-Z → `bg &&
  disown`, NEVER Ctrl-C).
- **Dry runs write `llm_calls` rows too** — real-renders-only SQL must join
  `media_generations`/`videos`, not `llm_calls`.
- "All accounts" production type = 1 video × EVERY account per run; duration rounds UP
  to shot multiples (17 s → 4 shots ≈ 20 s); intro/outro seconds work in BOTH video
  modes (audio silence padding).
- Temp/untracked helpers to delete when the tuning round closes: `orchestrator/_qc_*.py`,
  `_show_script.py`, `_run_debug.py`, `_dump_prompts.py`, `_realism_paramtest.py`,
  `_rerun_gym_test.py`, `orchestrator/_qc_audit/` (audit images), `pending.md` (stale).

### Where to resume (priority order)
1. **video.md P1** — open verifications: restart the pod gateway worker → knob run
   (closes TASK 2) → watch the rendered videos (V1–V3 as pixels) → one 20 s/4-shot run →
   watch `hand_render_quality` live and tune `QC_HAND_QUALITY_MIN` if needed.
2. **video.md P2–P8** — RIFE 16→32 fps (pod TASK 3, biggest win/cost), quality-mode
   sampling toggle, source-side hand refinement (P4 — brings the QC fail rate back
   down), video QC gate, config-snapshot race fix, InfiniteTalk PoC; parked: chaining /
   FLF2V / LoRA fine-tune.
3. Image-side leftover: the `held_with_phone` Step-2 ROOT fix (Qwen still renders 3rd
   hands; QC is now fair about mirrors but generation still produces real ones).
4. Housekeeping: temp files above; real angled product photo; GPU-host-from-DB (v3).

---

## v4 — image-quality + QC overhaul (2026-06-10 session)

> This session fixed both pending.md problems, added the second-product-angle (Picture 3)
> feature end-to-end, and rebuilt QC into a zero-tolerance, intent-aware gate with a
> fail=skip pipeline flow. Commits `42e4cff` → `930d5c9`, all pushed. Where this disagrees
> with v3/v2 below, this wins. `pending.md` has the per-problem detail; `PROMPT_TUNING_MAP.md`
> + README §8 are still the prompt-tuning map (note: QC model/states changed — see #4).

### 1. Step-1 (PuLID) — locked BODY BUILD now reaches the scene (`42e4cff`)
- Root cause of pending.md Problem 1: `run_scene.py` passed only `prompt_descriptors`
  (face-only); `identity.body_type` never reached Opus, and `rules/step1.md` banned body
  words + had six all-slim calibration examples. FLUX defaulted every body to slim/fit.
- Fix: `run_scene.build_user_message` now emits an authoritative `=== PERSONA BUILD ===`
  block (body_type + body_proportions + height_impression; omitted if absent → behavior
  unchanged). `step1.md` got principle **2b** (build phrase mandatory in sentence 1, even
  in athletic scenarios), a narrowed ban (contradictions only), and **diversified examples**
  (lean young F, heavier/plus-size 55yo F in the gym, stocky 48yo M, average 31yo F) +
  Anti-Example F. VERIFIED on GPU: heavier 55yo persona held through the gym scenario.
- Also: hand language is **positive-only** ("not in her pockets" planted the pocket token —
  FLUX rendered it; observed). Each hand gets an explicit visible place; the words
  pocket/hidden/tucked/crossed are banned from emitted prompts.

### 2. Step-2 (Qwen) — v7 rule book (`de05aa2`, `24e0124`, `ebccc52`)
- Research-verified facts: ComfyUI's encoder prepends literal **"Picture 1/2/3:"** labels
  (prompts now use those, never "first image"); no truncation at our lengths (1024-pos cap);
  the real enemy is attention dilution; FLUX negation-blindness is real, Qwen edit-constraint
  negations ("do not add visible fingers") are fine but content negation is not.
- **Scale doctrine**: Opus converts the packaging's real dims into a body-relative size tier
  (large carton = forearm-length spanning the hands; medium = hand-span; small = palm), always
  with upper+lower bounds + "big enough that its front-face text reads clearly" (scale IS the
  text-fidelity lever). **Thickness anchor**: proportion from dims but **Picture 3 is the
  visual authority — round DOWN** ("SLIM flat carton ~one tenth of its long side").
- **Presentation hold**: two-handed (cup bottom edge + steady top corner) preferred when both
  hands free; single-hand lower-edge grip when a phone occupies one; explicit **arm re-pose**
  when Picture 1's hand is occupied/crossed (fixes box-on-forearm).
- Budgets: 110–185 (210 ceiling), 140–200/230 with Picture 3; Opus can't count words so the
  BRAIN overwrites `word_count` with the real count (`run_scene`/`run_step2.parse_json`).
  Real prompts land ~280–310 words — over target, inside all hard caps, quality verified.
- Compliance: injection/needle/syringe banned even for printed box graphics ("pen device").

### 3. Second product angle — Picture 3 for 3D depth (`48fae96`, `7356462`, `c057f0a`)
- Why: one front photo carries text but zero depth → flat-card boxes. Hard limit is 3 images
  total (ComfyUI node: image1/2/3), so persona + product-front + ONE angle fits exactly.
- Chain (all optional/additive — no angle = byte-identical old behavior):
  migration **020** `products.reference_angle_asset_id` (APPLIED) → web upload
  "Product photo 2 — angled 3/4 view (optional)" on Setup+Product pages → orchestrator adds
  `product_angle_asset_id` to the step2 payload + a SECOND PRODUCT ANGLE available/none line
  for Opus → gateway worker downloads it → qwen-service writes temp + monkey-patches
  `q.PRODUCT_ANGLE_IMAGE_PATH` (unconditionally, multi-tenant-safe) → `step_2_qwen_comfyui.py`
  auto-selects `qwen_edit_2511_product_3ref.json` when the file exists.
- **Pod facts**: `/workspace/alluvi-clean` is NOT a git clone — update it by curl-ing raw
  GitHub files (backup at `src/step_2_qwen_comfyui.py.bak`). The gateway worker forwards
  images as base64 to the qwen-service which uses a fresh temp dir per job. Pod-side
  app.py/worker.py/qwen-service edits were made by the owner's pod-Claude (not in this repo).
- VERIFIED end-to-end: payload carried the id, qwen-service log showed
  `using 3-reference workflow`, the box rendered with real side/top faces.
- Picture-3 prompt doctrine: "Pictures 2 and 3 show the SAME single box", thickness anchor,
  strengthened uniqueness. CLI seeder: `seed_product_angle.py --image <path>` / `--remove`
  (= instant off-switch back to one-photo flow). Test angle photo = green-background 3/4 shot.
- DATA CAVEAT: `packaging.dims` came from convert-briefs ESTIMATING depth (said 4cm, real
  ~2cm → fat boxes). Fixed for the test tenant (18x8x2cm). Real tenants must put real
  measurements in the product brief (re-saving the brief regenerates packaging!).

### 4. QC overhaul — zero-tolerance anatomy, fail=skip (`561f09d`, `930d5c9`)
- **Pipeline flow**: Qwen → QC → retries (1+`qc_max_retries`) → pass → realism → video;
  ALL retries fail → `qc_status='failed'` → **no realism, no video**, scenario is CONSUMED
  (never re-picked — was an infinite-retry bug in `pick_scenarios`; active-phase engine
  selection also excludes it) and still counts toward the 60-scenario engine flip
  (the coverage view counts 'failed' as resolved). Terminal states: **passed | failed** only
  ('exhausted' is dead; null = QC off/pending).
- **Rubric** (`rules/qc.md`): numbered whole-frame HAND CENSUS + held-objects cross-check +
  "one arm has ONE hand — never merge entries"; `qc.py` does code-side arithmetic (regex-counts
  the census's numbered entries; >2 hands fails even if the model's bool/count rationalizes);
  truncated-limb gate (image-border crops = normal framing; mid-frame stumps = fail); 6+
  fingers; intent-aware placement via `{{INTENDED_PLACEMENT}}` from `scenarios.spec`
  (held intent must be IN a hand; surface placement NEVER fails for not-held; floating/dumped
  always fails; flat_lay expects person_count 0); proportion gate (rectangle never square,
  ~90%); two-tier text (brand+product name STRICT, secondary lenient); colors ~85%.
- **Judge = `claude-opus-4-7`** (QC_MODEL default; env-overridable). Empirical: Sonnet 4.6
  rationalized a 3-hand leftover away TWICE; Opus counted it correctly with surgical feedback.
  (`claude-sonnet-4-6` DOES exist on the tenant key — verified via /v1/models; earlier
  "no such model" was a date-suffixed ID.) Fail-open on QC infra errors is preserved.
- **Reference = brief-only** (owner's call, zero per-call image cost): `qc_brief_builder.md`
  now bakes in the aspect-ratio class + designates the strict brand/product-name pair;
  the test tenant's qc_brief was REGENERATED with it.
- **Retry feedback**: QC issues are short imperative content phrases → AVOID section for
  Opus → woven into the next Qwen prompt as plain content constraints; the rule book FORBIDS
  retry-meta words (previous/attempt/retry/error/QC) in `step_2_image_prompt`. Verified live:
  zero leakage, constraint embedded ("Two hands total in the frame, no other hand anywhere").
- VERIFIED on 4 stored composites: 3-hand sahad gym FAIL, fat gym mirror FAIL (real 3rd
  hand + garbled headline), patio two-hand PASS, desk surface placement passes placement
  (fails only strict text — per spec).

### Operational discoveries (will save you hours)
- **Gateway job idempotency**: `scene:<persona>:<scenario>` (NO attempt — a finished scene
  can never re-run via payload; null the `jobs.idempotency_key` row to force) vs
  `step2:<persona>:<scenario>:a<attempt>` (bump attempt to force). Helper:
  `orchestrator/_rerun_gym_test.py` (TEMP, untracked — reuses last logged prompts, verifies
  the returned job was created TODAY instead of trusting "succeeded"; delete when done).
- A "succeeded in 5s" job = the gateway echoing an OLD job. Always check `created_at`.
- Storage REST downloads need BOTH headers: `Authorization: Bearer <key>` AND `apikey:`.
- Windows: PYTHONUTF8=1 for any orchestrator CLI that prints ✓/—; Git-Bash heredocs eat
  backslashes in inline python (write a temp .py instead).

### Known issues / next steps
1. **`held_with_phone` scenarios systematically render a 3rd hand** (Qwen keeps the phone AND
   adds two box-hands). Hardened QC now correctly fails these → they'll burn retries. Root fix
   = Step-2 prompt pattern for phone scenarios (likely: single box-hand only, never instruct
   the phone hand). NEXT TUNING TARGET.
2. Step-2 rule book says "box/carton" throughout — works for any product via packaging data,
   but bottles/jars/pouches would fight the wording. Generalize when a non-box tenant arrives.
3. Web product-brief form: add a hint to include REAL measurements (see dims caveat above).
4. `orchestrator/_rerun_gym_test.py` is temp — delete when the tuning round closes.
5. GPU-host-from-DB (v3 item) still pending: orchestrator reads GATEWAY_URL from .env.

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
