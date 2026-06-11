# video.md — Video-stage quality overhaul: findings, plan, expectations

> **Purpose:** the master plan for taking the video stage from "moving pictures" to
> ad-grade output. Read this before touching anything video-related. It captures how
> the stage works today, the diagnosed root causes of bad motion (2026-06-11 audit),
> the researched facts about Wan's limits, the phased build plan with expected
> outcomes and verification, and — at the end — the "full Wan potential" config
> changes we deliberately deferred. Companion docs: `update.md` (project state),
> `PROMPT_TUNING_MAP.md` (per-stage prompt map), `claudeAI.md` (pod-side tasks).

---

## 1. How the video stage works TODAY (verified from code, 2026-06-11)

```
Run (web) → run_pipeline Phase C → run_video/RV per finished image:
  1. SCRIPT BRAIN  — Opus + rules/script.md + DB data
     (tenants.script_company_info / script_directives, products.product_info,
      tiktok_accounts persona, scenarios.spec, tenant_pipeline_config controls)
     → STRICT JSON: per-shot { dialogue, wan_motion_prompt, wan_negative_prompt }
     All shots are authored in ONE Opus call (this matters for Phase V2 below).
  2. F5-TTS        — speaks each dialogue line (~3 words/sec); audio length rules.
  3. Wan 2.2 I2V   — animates the SAME step3 photo per shot.
     768x1344, 16 fps, frames = audio length snapped to 4n+1, capped 81 (~5 s).
     Workflow: two-expert ladder (high-noise CFG 3.5 / low-noise CFG 1.0),
     euler, 20 steps, shift 5, lightx2v 4-step LoRA wired but DISABLED.
     Script negative is PREPENDED to BASE_NEGATIVE (video_pipeline/step_5_video_wan.py:57).
  4. LatentSync    — repaints the mouth to match the audio.
  5. Assembly      — multishot stitch (hard cuts) or silent-first frame-join
     (dHash threshold + punch-in); intro/outro/tail; final mp4 → videos bucket.
```

Key production fact: **every shot starts from the IDENTICAL still photo.** Wan has
no memory between shots — shot 2 cannot start where shot 1 ended (see §5).

---

## 2. Diagnosed root causes of "bullshit motion" (ranked, with evidence)

1. **BASE_NEGATIVE demands motion (smoking gun).** The hard-coded Wan template
   negative in `step_5_video_wan.py` contains 静态 (static), 静止 (still),
   静止不动的画面 (motionless frame). It was written to stop Wan producing boring
   still clips — the OPPOSITE of a calm ad presenter. Every render is pushed away
   from stillness, so Wan invents movement: lifting the product, leaving it in the
   air, restless hands. `rules/script.md` §6 even forbids static/frozen in the
   script negative — but the base it gets glued onto already contains them.
2. **The motion prompt is written blind.** Opus authors "she gently turns the
   product" without ever seeing the image: it does not know which hand holds the
   product, whether a phone occupies the other hand, or whether the product sits on
   a surface. We fixed this exact class of bug twice already (Step-1 persona build,
   Step-2 grip authoring from the Step-1 prompt) — the video brain never got the
   same treatment. Ungrounded instruction + eager model = product teleportation.
3. **The rule book permits the riskiest motion.** It suggests "gently turning it as
   if showing the label" (product manipulation = Wan's weakest skill) and never
   states the iron rule: the product never leaves the hand; the grip never changes;
   nothing is picked up or put down.
4. **Same-still multishot reads as jump cuts** — every cut snaps back to the
   identical pose. (Partly mitigated by silent-first's frame-join; also partly
   FINE: real TikTok/UGC ads are jump-cut grammar — see §5 perspective note.)
5. **16 fps** reads choppy/AI regardless of content (config layer, §7).

What is NOT the problem: prompt length (we are far below Wan's limits, §3) and the
sampler being a speed-distilled config (the 4-step LoRA is disabled; CFG 3.5 on the
high-noise expert gives moderate adherence).

---

## 3. Researched facts (June 2026 — keep these in mind when editing)

- **Token budget:** Wan's UMT5-XXL encoder hard-caps at **512 tokens** (silent
  truncation); community testing shows quality degrades past **~320–350 tokens**
  (motion slows, grid artifacts). Our motion prompts (~100 tokens) are safe.
  RULE: keep `wan_motion_prompt` under ~200 words; never stuff it.
- **Native specs:** Wan 2.2 A14B supports 480P/720P at **24 fps** native; our
  768x1344 is already ≈ the 720P pixel budget. 16 fps / 81 frames is a Wan-2.1-era
  convention kept for throughput.
- **Render cost:** ~9 min per 5 s 720P clip on H100-class (fp8 + offload: 2–4 min).
  Full-potential settings ≈ 2–3x today's render time per shot.
- **Last-frame chaining drift:** continuing from a clip's last frame accumulates
  color shift + quality drift per hop ("garbage in, longer garbage out"); keep
  chains ≤ 3–4 hops, color-match, anchor on the original frame.
- **FLF2V:** Wan 2.2 first+last-frame conditioning is open and works in ComfyUI —
  enables storyboarded camera paths between authored keyframe images.
- **InfiniteTalk** (open, Wan-based, ICLR 2026; successor LongCat-Video-Avatar-1.5,
  May 2026): image + full audio → ONE continuous video in 81-frame chunks with
  25-frame carry-over. Built-in continuity, identity preservation, and
  audio-driven body/head motion. Replaces Wan+LatentSync for talking shots if the
  PoC wins. Camera control is weaker than prompted Wan; punch-ins/cuts in post.
- **Fine-tuning reality:** Wan 2.2 LoRA training is supported (DiffSynth-Studio /
  diffusion-pipe; the two experts can be trained separately) and needs **VIDEO
  clips** (50–300 short captioned clips), not images — motion lives in the temporal
  dimension. A LoRA teaches our narrow domain (calm presenter, steady grip); it
  does NOT upgrade base-model physics to Seedance level. Independent leaderboards
  put Seedance/Kling/Veo-3 a tier above open models overall — but a mostly-still
  presenter is where Wan is already strongest, so the practical gap for OUR domain
  is mostly config + prompts, not model ceiling. Ratings (`asset_ratings`
  vid_* ids) are quietly accumulating the future training set.

---

## 4. THE PLAN — phases, why, expected outcome, implementation

### Phase V1 — motion-logic fixes (prompt layer; cheap; DO FIRST)

**Why:** root causes #1 and #3. Highest expected quality gain per line changed.

**V1a. Fix `BASE_NEGATIVE`** (`video_pipeline/step_5_video_wan.py`; pod copy
updated via curl — it is NOT a git clone):
- REMOVE the anti-stillness terms: 静态, 静止, 静止不动的画面.
- KEEP all artifact/anatomy/teeth terms.
- ADD anti-float terms: `product floating, object levitating, hand releasing the
  product, grip changing, object detaching from hand, picking up, putting down`.

**V1b. Harden `rules/script.md` §5 (motion doctrine)** — add iron rules:
- THE PRODUCT NEVER MOVES INDEPENDENTLY: it never leaves the hand (or surface),
  the grip never changes, nothing is picked up, put down, lifted, or handed over.
  The hand and product move together or not at all.
- Replace "gently turning it as if showing the label" with a SAFE showing gesture:
  a small tilt of the wrist (hand+product as one unit), label kept facing camera.
- Explicit camera grammar (Wan 2.2 understands it): name the move precisely —
  "slow steady dolly-in toward the subject", "gentle lateral drift left, keeping
  the subject centered". One move per shot, named, calm.
- Hard cap: motion prompt ≤ 200 words (token-safety, §3).
- Example (5 s shot, the style we expect Opus to emit):
  > "The woman holds the product steady at chest level in her right hand and looks
  > calmly into the camera. She blinks naturally and her expression softens into a
  > small warm smile. Her wrist tilts the product a few degrees toward the lens —
  > hand and product moving as one — then settles. She holds still, breathing
  > easily. Slow steady dolly-in toward her. calm natural human motion, the rest of
  > the body still and relaxed, product clearly visible, mouth closed, face stable,
  > identity preserved, product label stable, cinematic realism"

**Expected outcome:** the floating/teleporting product disappears in most shots;
overall motion becomes calm-with-intent instead of restless.

**Verify:** generate 2–3 videos on burned-test scenarios; eyeball product behavior;
compare `media_generations.negative_prompt` (no static terms, anti-float present).

### Phase V2 — motion grounding (the proven pattern, third time)

**Why:** root cause #2. The script brain must author motion against the REAL pose.

**Implementation** (orchestrator only — `run_video.py` / `script_gen.py`):
- Pass into `build_user_message`:
  - the scenario's `grip_or_placement` + `archetype` (already in spec; today only
    a generic scene block is passed),
  - the Step-2 image prompt (`image_generations.prompt` for the output's
    step2 asset, highest attempt) — it describes exactly which hand holds what,
    where the phone is, how the product sits.
- New rule-book section: "POSE GROUND TRUTH — author every motion FROM this; never
  instruct a hand that is holding the phone; never instruct motion for a product
  resting on a surface beyond ambient stillness."

**Expected outcome:** no more impossible instructions ("she raises the product"
when it lies on the nightstand); per-scenario motion that matches the picture.

**Verify:** `llm_calls.purpose='video_script'` user_message contains the pose
block; motion prompts reference the correct hand/placement on 3 archetypes
(held / held_with_phone / placed_on_surface).

### Phase V3 — the SHOT PLAN (cross-shot consistency by prompt)

**Why:** all shots are authored in one Opus call, so a coordinated arc is free.
Limit (physics): every shot restarts from the same frame — "editing continuity",
not positional continuity (that's §5).

**Implementation** (`rules/script.md`):
- New section: the model first writes a `shot_plan` — one line per shot naming the
  camera move and energy, designed as a sequence (e.g. 1: slow dolly-in /
  2: gentle drift left / 3: closer hold, most intimate line / 4: slow pull-back,
  brand landing). Moves vary but never contradict; gaze and mood stay consistent;
  energy follows the narrative arc. Then each `wan_motion_prompt` implements its
  plan line. Add `"shot_plan"` to the output JSON (additive — the video service
  ignores unknown fields).

**Expected outcome:** a 20 s video feels like one directed piece with deliberate
cuts, not 4 random clips.

**Verify:** read the shot_plan in `llm_calls`; watch a 4-shot video for arc.

### Phase V4 — InfiniteTalk proof-of-concept (the strategic bet)

**Why:** solves in one move what prompts cannot: true continuity (no snap-backs),
audio-driven body/head motion (the "no relation with the audio" complaint), and
identity stability over 20 s+. Open-source, Wan-based, ComfyUI-supported.

**Implementation (pod experiment first, NOT pipeline):** install on the pod, run
ONE scenario manually: step3 image + the full F5-TTS audio → continuous video.
Compare side-by-side against the current multishot output. If it wins → design the
pipeline integration (new video mode `infinitetalk` next to multishot/silentfirst;
LatentSync skipped for talking shots; punch-ins/cuts in post via the silent-first
machinery).

**Expected outcome (hypothesis to test):** presenter ads one tier above current
quality; gestures that follow the speech.

### Phase V5 — last-frame chaining experiment (OPTIONAL, only if continuous-camera
looks are wanted before V4 lands)

Shot N+1's first frame = shot N's last frame. ≤ 4 hops, color-match each hop,
QC the chain tail for drift. Pipeline change in the pod video-service. Park unless
needed — V4 likely supersedes it.

### Phase V6 — fine-tuning (LAST; only with evidence)

Trigger: ratings still show a motion-quality gap after V1–V4. Data: the
highest-rated videos (50–300 clips) + captions. Train Wan 2.2 LoRA
(DiffSynth-Studio / diffusion-pipe), possibly low-noise expert only first.
Expectation: domain-style gains (calm presenting), NOT a Seedance-level base-model
upgrade. Do not start here.

---

## 5. Continuity decision matrix (the §"20-second video" question)

| Approach | Continuity | Cost | Risk | Status |
|---|---|---|---|---|
| V3 shot plan (prompt only) | editing-style (planned cuts) | free | none | DO NOW |
| V5 last-frame chaining | true positional | medium | color/quality drift per hop | optional |
| FLF2V keyframe storyboard | planned camera path | high (keyframes need their own QC) | identity per keyframe | shelf |
| V4 InfiniteTalk | native (one continuous take) | medium (pod install + new mode) | weaker camera control | PoC next |

Perspective note (ad production): real UGC/TikTok ads are jump-cut grammar — cuts
every 3–5 s back to similar framing. Positional continuity is cinematically nice
but is NOT what makes an ad convert; calm logical motion + a planned arc + speech-
coupled gestures matter more. Hence the order V1→V2→V3→V4.

---

## 6. What we will NOT do (and why) — decisions already made

- No "static, frozen" terms in ANY negative (they fight the stillness we want) —
  but equally never ask for a freeze: stillness comes from holds + restraint words.
- No product-form guesses in motion prompts (box/bottle/pen) — generic "the
  product" only; Wan distorts named forms (existing rule, keep).
- No frame-accurate gesture-to-audio sync via prompting — architecturally
  impossible with decoupled TTS+Wan; that is exactly what V4 exists for.
- No fine-tuning before V1–V4 are measured (cheaper levers first; ratings become
  the training set later anyway).

---

## 7. END-STATE: getting Wan's FULL potential (config layer — deliberately last)

These raise render cost ~2–3x per shot, so they ship as a per-tenant "video
quality mode" (same pattern as the realism knobs / migration 022), AFTER V1–V3
prove the motion logic. Throughput mode (today's settings) stays the default.

| Knob | Today | Full-potential target | Note |
|---|---|---|---|
| Steps | 20 | 25–30 (euler community-optimal) | sharper, more coherent frames |
| CFG (high-noise expert) | 3.5 | ~4.5–5.5 | stronger prompt adherence |
| CFG (low-noise expert) | 1.0 | ~3–4 | today's 1.0 barely listens in the detail phase |
| FPS | 16 | 24 native — OR keep 16 and RIFE-interpolate to 32 | RIFE is near-free vs +50% diffusion cost; try RIFE FIRST |
| Frames/shot | 81 @16 | 121 @24 (if going native 24) | tied to fps choice |
| Resolution | 768x1344 | keep | already ≈720P pixel budget |
| lightx2v 4-step LoRA | disabled | keep disabled in quality mode | it's the throughput lever, not quality |

Implementation sketch (when its turn comes): expose `video_quality_mode`
('throughput' | 'quality') on `tenant_pipeline_config` → orchestrator payload →
pod worker → video-service picks the param set + optional RIFE pass. Same
ship-order-safe chain as `realism_params` (claudeAI.md TASK 2 pattern).
RIFE interpolation alone (16→32 fps on today's renders) can ship earlier as a
post-step in the video-service — biggest perceptual win per GPU-second.

---

## 8. Verification ledger (update as phases land)

- [x] V1a BASE_NEGATIVE fixed + pod curl'd + service restarted (2026-06-11; render eyeball pending owner review)
- [x] V1b script.md motion doctrine hardened + dry-run reviewed (2026-06-11)
- [x] V2 pose ground truth in the script user message — implemented in script_gen/run_video/run_pipeline;
      dry-run verified: 392w step-2 prompt reached the brain, all shots use the real pose (2026-06-11)
- [x] V3 shot_plan emitted — PLUS performance-continuity doctrine (§4b: a gesture happens at most
      ONCE across the video; most beats are presence-only; energy follows the arc; camera provides
      the variety). Dry-run verified: 4 distinct named moves, wrist-tilt only in the product beat,
      one pure-presence beat (2026-06-11). 4-shot rendered video watch still pending.
- [ ] V4 InfiniteTalk PoC side-by-side verdict: ______
- [ ] V7(§7) quality mode + RIFE measured: render-time x___, verdict: ______
- [ ] V6 fine-tune go/no-go decision with rating evidence: ______
