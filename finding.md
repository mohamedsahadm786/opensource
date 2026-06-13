# finding.md — The RIFE frame-interpolation problem (2026-06-12, full write-up)

> **Purpose:** everything known about why the first RIFE-interpolated videos were
> rejected, with evidence, root causes, current state, and the fix options to
> discuss. Written at PC shutdown on 2026-06-12; resume the discussion from here.

---

## 0. The one-paragraph summary

We added RIFE frame interpolation (the "32 fps" Run-settings knob) to remove the
choppy stop-motion AI look. The wiring works perfectly end-to-end — but the owner
rejected the first two output videos for **two independent problems**: (1) a
"sudden artificial shifting" mid-shot, caused by RIFE drawing morph frames across
the **hidden frame_join seams inside shots**, and (2) the silentfirst video showed
**black with audio-only** in every browser preview, caused by Practical-RIFE
writing the **mp4v codec** that browsers cannot decode. Problem 2 is already fixed
in the repo (deploy pending). Problem 1 needs a design decision — three candidate
fixes below. The knob is **OFF** in Run settings, so the pipeline is back to the
exact pre-RIFE behavior; nothing is broken or urgent.

---

## 1. Background — what RIFE is and why we added it

- Wan 2.2 renders video at **16 frames per second**. Few frames per second =
  stop-motion choppiness = the single biggest "AI look" giveaway (video.md P2).
- RIFE is a small neural network whose only job: given picture A and picture B,
  **draw the picture halfway between them**. Doubling the frames this way is
  near-free (~40-55 s per clip) vs Wan diffusion (~9 min per clip).
- Chain shipped on 2026-06-12: web knob `interp_target_fps` →
  payload `interp_fps` → gateway → worker → video-service →
  `multishot/stitch.py` (per-clip, before concat) and `_build_silentfirst`
  (once, after lipsync) → `video_pipeline/interpolate_rife.py`
  (Practical-RIFE v4.25 at `/workspace/Practical-RIFE`, own weights, smoke-tested).

### Discovery made on the way (important context)
The clips RIFE receives are **25 fps, not 16**: LatentSync (lipsync) internally
works on a 25 fps timeline and re-writes the whole file at 25 fps, **duplicating**
Wan's 16 unique frames to fill the grid (that's why files said 25 fps but still
FELT like 16). RIFE can only multiply by whole numbers, so "target 32" from a
25 fps input → minimum x2 → **50 fps** final. The knob effectively means
"2x smoothness"; the DB now records the real measured fps (50).

---

## 2. What the owner observed (the rejection)

Two videos rendered with the knob on (both `videos.fps = 50`):
- `pilates_reformer_mirror_06 / final_multishot.mp4` (multishot, 2 shots)
- `yoga_home_practice_07 / final_silentfirst.mp4` (silentfirst, ~13 s)

Observations:
1. **"Sudden movement / artificial shifting"** felt in BOTH videos — and notably
   NOT (only) at the shot-to-shot changeovers; it felt like it happened *inside*
   shots. (This observation turned out to be exactly right.)
2. The **silentfirst** video showed a **black screen with working audio** in the
   web lightbox AND the Supabase storage preview — but **played fine after
   downloading** to the PC.

---

## 3. Problem A — the seam-morph artifact (the "sudden shifting")

### The hidden seams
Wan can only render ~5 s (81 frames) in one go. Any shot whose audio exceeds 5 s
(e.g. speech ~5 s + 1 s intro silence = 6 s) is covered by rendering **two
separate Wan clips and gluing them mid-shot** (`silentfirst/frame_join.py`, used
by BOTH modes). The glue point = a **seam**, located in the middle of a shot.
frame_join is smart: it cuts at the most-similar frame pair it can find
("framematch"), or falls back to a hard cut with a punch-in zoom when no good
match exists ("punchin_fallback").

### Why seams were invisible before RIFE
At a seam, one frame is followed by a *slightly different* frame (arm a few cm
off, head turned a hair). When that change happens **instantly** — one frame to
the next — the human brain reads it as "a cut" and forgives it completely (we've
watched cuts our whole lives). At 16/25 fps every seam was an instant blink.

### What RIFE does to a seam
RIFE doesn't know where seams are. It reaches the seam, sees frame A (end of clip
1) and frame B (start of clip 2), and obediently draws "the halfway picture" —
but halfway between two MISMATCHED poses is a **melted, stretched ghost frame**
(an arm half in one place, half in another; a half-zoomed frame at the punchin
seams). It flashes for 1/50 s. The brain forgives an instant cut but **never
forgives a warp** — flesh stretching reads as deeply wrong. That is the artifact.

### Why each mode suffers differently
- **Multishot**: RIFE runs per final clip (shot), so shot-to-shot cuts are SAFE —
  but each >5 s shot contains 1 internal frame_join seam that gets morphed.
- **Silentfirst**: the WHOLE take is frame_join'd silent clips (a 13 s video =
  ~3 clips = 2+ seams, some punchin_fallback with a zoom change), lipsynced once,
  then RIFE runs over **everything** → a morph at EVERY seam → felt worst here.
- The smoother everything else gets at 50 fps, the MORE the warps stand out by
  contrast.

### Why the design review missed it
The shipped design explicitly protected the shot-to-shot cuts (per-clip RIFE,
"never interpolate across a concat") and consciously accepted internal seams as
low-risk ("framematch seams blend near-identical frames"). That judgment was
wrong for two reasons: (a) framematch frames are *similar*, not identical — RIFE
amplifies the difference into a visible warp; (b) punchin_fallback seams (zoom
change) are nowhere near identical. Practical-RIFE has an internal SSIM-based
scene-change skip, but its threshold only catches *total* scene changes —
same-person-same-room seams sail straight through it.

---

## 4. Problem B — black video in browsers (mp4v codec)

**Evidence (codec fourcc read from the downloaded files):**
- `final_multishot.mp4` → `avc1` (H.264) → browsers play it.
- `final_silentfirst.mp4` → `mp4v` (MPEG-4 Part 2) → **browsers cannot decode
  mp4v in a <video> tag** → black frame + audio only (audio is AAC = decodable).
  Local players (VLC / Windows) decode mp4v fine → "plays after download".

**Root cause:** Practical-RIFE writes its output via OpenCV with the `mp4v`
fourcc. In **multishot** the punch-in step happened to re-encode every clip with
libx264 AFTER RIFE — masking the problem. In **silentfirst** RIFE runs last
(extend_tail was skipped because outro > 0), so the raw mp4v file went straight
to the bucket.

**Status: FIXED in the repo + HARDENED beyond the RIFE case (2026-06-13).**
The original point-fix (commit `0ae13f8`) made `interpolate_rife.py` re-encode its
output to H.264. But mp4v was only the most-visible leak: the multishot copy-concat
(single shot / punch-off) and the silentfirst lipsync output ALSO ship their source
codec unchanged, so a non-h264 source would slip through even without RIFE. The
proper fix is a single web-safety guard at the end of every path:
- **NEW `video_pipeline/web_normalize.py`** → `ensure_web_playable()`: probes the
  real streams; already-h264/yuv420p/AAC → instant stream-copy remux (+faststart,
  zero quality loss); anything else → re-encode to H.264/yuv420p + AAC. Idempotent.
- Wired into `stitch.stitch()` (auto-covers multishot, which the pod calls) and the
  silentfirst CLI `build_one()`.
**Deploy = claudeAI.md TASK 5** (curl `web_normalize.py` + `stitch.py` +
`interpolate_rife.py`, add ONE guard call to the pod's `_build_silentfirst`, restart
video-service). Safe to deploy NOW even with the RIFE knob off — it only normalizes
the final container/codec; already-good files are untouched. Acceptance test: the
video PLAYS in the web lightbox + Supabase preview (no black screen).

---

## 5. Current state (safe)

- **Frame interpolation = OFF** in Run settings → every run is byte-identical to
  the proven pre-RIFE behavior. Nothing else regressed; all other knobs
  (realism, steps/CFG, config snapshot) are unaffected and verified.
- RIFE stays installed at `/workspace/Practical-RIFE` (weights + patched
  sk-video), ready if/when a seam fix lands. The whole chain (web → DB → payload
  → service → module) is proven working — only the OUTPUT quality at seams is
  the problem.

---

## 6. Fix options to discuss (with honest trade-offs)

### Option 1 — Seam-aware RIFE (surgical fix, keeps the near-free cost)
`frame_join.join()` already RETURNS a report with the exact seam timestamps
(`{seam, method, cutA, cutB}` per join). Today that report is discarded in
multishot's `_render_capped_footage` and kept only as metadata in silentfirst.
The fix: thread the seam times through to the interpolation step → **split the
clip at each seam → RIFE each segment separately → re-concat with clean cuts**.
Seams stay instant cuts (forgivable), real motion still gets doubled.
- Cost: plumbing work across stitch/silentfirst paths + the seam times must
  survive lipsync (LatentSync re-times 16→25 fps, so seam timestamps must be
  scaled accordingly — careful work, very testable).
- Risk: low (pure post-processing); complexity: medium.

### Option 2 — Wan native 24 fps (the P3 row we parked; structurally clean)
Have Wan RENDER 24 fps directly (121 frames for 5 s). No interpolation tool, no
seams problem (seams stay hard cuts), genuinely new motion frames everywhere.
- Cost: **+50% diffusion render time** per clip, every clip, forever.
- Note: LatentSync will still re-time to 25 fps — from a 24 fps source that's
  nearly 1:1 (no duplicated-frame cadence), so the result should FEEL smooth.
- Risk: low; this is just a frame-count/fps change in the same workflow — but it
  needs A/B to confirm Wan's quality holds at 121-frame windows.

### Option 3 — RIFE only on single-chunk footage (cheap partial fix)
Skip interpolation for any clip that frame_join built from >1 chunk (n_chunks is
already tracked). Single-chunk shots (≤5 s incl. padding) get smooth; longer
shots stay 25 fps. Silentfirst would effectively never interpolate.
- Cost: trivial to build; benefit: partial and inconsistent (mixed-smoothness
  videos may feel odd). Best as a stopgap only.

### Recommendation to discuss tomorrow
Option 1 is the "right" engineering fix and keeps RIFE's near-free economics.
Option 2 is the simplest *correct* result if +50% render time is acceptable —
and may also be the better baseline for judging P7 (InfiniteTalk). A sane
sequence: keep the knob OFF → finish the P7 PoC verdict first (it may change the
whole video strategy) → then choose between Option 1 and Option 2 with that
verdict in hand.

---

## 7. Related facts worth remembering

- `videos.fps` is an INTEGER column; the worker now writes the real measured fps.
- The "32 fps" web label is misleading (output is 50); rename to "2x (smooth)"
  whenever the knob returns.
- LatentSync 25 fps re-timing is ALSO why pre-RIFE videos said 25 fps but felt
  16 — the body has 16 unique poses/s with duplicates; the mouth genuinely moves
  at 25 (it's generated against the audio).
- Verification runs still owed for the rest of TASK 3 (knobless control run +
  steps/CFG sampling run) — see pending.md #3. The silentfirst+interp run that
  produced the yoga video already served as the silentfirst wiring proof
  (exactly ONE [rife] line at the end — correct placement).
