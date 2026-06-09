# ALLUVI — Prompt Tuning Map (where every prompt comes from)

## The mental model (read once)
Every AI prompt in the pipeline is assembled from three things:
- **RULE BOOK** — a generic, brand-neutral `.md` in `orchestrator/rules/`. This is the *methodology*
  (the system prompt): how to think, what to output, the JSON shape. **Edit this to change behavior
  for ALL tenants.**
- **DB DATA** — per-tenant rows (briefs converted to JSON, product packaging, mask prompt, etc.).
  **Edit this (or the web brief that generates it) to change behavior for ONE tenant.**
- **BRAIN** — an `orchestrator/run_*.py` file that reads the rule book + the DB data, calls the model
  (Opus for authoring, Sonnet for QC), and writes the result. You rarely edit this; it's the wiring.

So: *to tune a stage's prompt → edit its RULE BOOK; to tune one tenant's content → edit its DB data.*
The model-level knobs that are NOT prompts (steps, cfg, seed, denoise, LoRA) live either in
`tenant_pipeline_config` (the web Run-settings) or in the ComfyUI workflows on the pod — noted per stage.

Paths are relative to `orchestrator/`. After editing a rule book, just re-run the pipeline — no
redeploy; the brain reads the `.md` fresh each run.

---

## INDEX
1. Portrait (Phase A — the persona face)
2. Scene (Step 1 — persona in the scene, PuLID)
3. Product composite (Step 2 — Qwen)
4. Realism (Step 3 — RealVisXL)
5. Dialogue + motion (Script — the video brain)
6. TTS (F5 — the voice)
7. Wan video (the motion render)
8. Lip-sync (LatentSync)
9. QC (the image validator)
10. QC reference brief (one-time, per product)

---

## 1. Portrait — Phase A (the persona's permanent face)
- **Produces:** the locked face image for an account (reused forever).
- **Brain:** `run_portrait.py`
- **RULE BOOK (tune here):** `rules/phaseA.md`
- **DB inputs:** `tiktok_accounts` → `gender`, `age`, `country`, `name`, `identity_factors`
- **Model:** Opus (`OPUS_MODEL`) authors the appearance/FLUX prompt → then FLUX renders it.
- **Stored at:** `personas.appearance_spec` + `personas.prompt_used`; `llm_calls` (`purpose='phasea_prompt'`).
- **Tune:** the *look/quality of faces* → `rules/phaseA.md`. *One person's look* → that account's
  `identity_factors` / `gender` / `age` / `country`.

## 2. Scene — Step 1 (persona placed in the scenario, PuLID)
- **Produces:** the persona standing/sitting in the scene (no product yet).
- **Brain:** `run_scene.py`
- **RULE BOOK (tune here):** `rules/step1.md`
- **DB inputs:** `personas` (`appearance_spec`, portrait) + `scenarios.spec` (the scene brief) +
  `tiktok_accounts` (identity)
- **Model:** Opus authors the Step-1 scene prompt (positive + negative) → PuLID/FLUX renders.
- **Stored at:** `llm_calls` (`purpose='step1_prompt'`, full prompt in `parsed_json`) + `image_generations`
  (`stage_name='step1'`, `prompt`, `negative_prompt`, `seed`).
- **Tune:** *how scenes are framed/composed/lit* → `rules/step1.md`. *A specific scene* →
  that scenario's `scenarios.spec` (and the attribute vocab in `attributes.py`).

## 3. Product composite — Step 2 (Qwen places the real product)
- **Produces:** the scene with the actual product composited in (the box, with its printed text).
- **Brain:** `run_step2.py`
- **RULE BOOK (tune here):** `rules/step2_qwen.md`
- **DB inputs:** `products` → `name`, **`packaging`** (the box: `colors`, `shape`, `text_on_packaging`
  rendered verbatim, `graphics`, `dims`) + `scenarios.spec` + the Step-1 result (`llm_calls.parsed_json`)
- **Model:** Opus authors the Step-2 composite prompt → Qwen renders.
- **Stored at:** `llm_calls` (`purpose='step2_prompt'`) + `image_generations` (`stage_name='step2'`, `prompt`).
- **Tune:** *how the product is placed/held/integrated* → `rules/step2_qwen.md`. *How the box looks /
  its printed text* → `products.packaging` (i.e. the product brief → `convert-briefs`).

## 4. Realism — Step 3 (RealVisXL polish + box protection)
- **Produces:** the photoreal final still (the video's first frame).
- **Brain:** `run_step3.py`
- **Prompt source:** **no rule book / no Opus.** The only tenant-authored text is
  `products.mask_prompt` (the box-protect detector prompt). All other realism settings
  (denoise / cfg / LoRA / positive-negative) are **hardcoded on the pod** in the realism service /
  ComfyUI workflow — not in a rule book.
- **DB inputs:** `products.mask_prompt`
- **Stored at:** `image_generations` (`stage_name='step3'`, `mask_prompt`, `seed`).
- **Tune:** *what the box-protect mask targets* → `products.mask_prompt` (web field, with the helper).
  *Realism strength/denoise/LoRA* → the **realism-service / ComfyUI workflow on the pod** (not here).

## 5. Dialogue + motion — Script (the video brain)
- **Produces:** for each shot: the spoken **dialogue**, the **`wan_motion_prompt`**, and the
  **`wan_negative_prompt`**. This single stage feeds both TTS (#6) and Wan (#7).
- **Brain:** `script_gen.py` (assembles 3 DB sources) + `run_video.py` (orchestrates)
- **RULE BOOK (tune here):** `rules/script.md` (generic scriptwriting + motion-prompt methodology)
- **DB inputs (the 3 sources):**
  - `tenants.script_company_info` → `system_identity`, `brand_personality`, `marketing_language_engine`,
    `video_generation_preferences`, `scene_generation_system`
  - `products.product_info` → `product_name`, `product_type`, `wellness_associations`, `positive_lifestyle_language`
  - `tenants.script_directives` → `dialogue_generation_rules`, `ai_generation_priorities`
- **Model:** Opus (`OPUS_MODEL`).
- **Stored at:** `videos` (`dialogue`, `wan_motion_prompt`, `wan_negative_prompt`) +
  `media_generations` (`stage_name='shot_1..n'`, `prompt`=motion, `params.dialogue`) +
  `llm_calls` (`purpose='script'`).
- **Tune:** *script style / motion-prompt style for everyone* → `rules/script.md`. *One brand's voice,
  hooks, what to say/avoid, camera/motion preferences* → that tenant's `script_company_info` +
  `script_directives` (i.e. the company/script briefs → `convert-briefs`).

## 6. TTS — F5 (the spoken voice)
- **Produces:** the audio of the dialogue.
- **Prompt source:** the **dialogue text from #5** is what gets spoken — there is no separate text
  prompt. The *voice* is chosen by: `tiktok_accounts.voice_reference_asset_id` + `voice_reference_text`
  if set, otherwise a gender default (`VOICE_BY_GENDER`: `female_02.wav` / `male_01.wav`) in the
  video service.
- **Tune:** *what is said / pacing* → `rules/script.md` (#5). *Which voice* → upload a voice reference
  on the account (when that flow is integrated) or change the default wav in the video service.

## 7. Wan video — the motion render
- **Produces:** the moving silent clip per shot.
- **Prompt source:** `wan_motion_prompt` + `wan_negative_prompt` authored in #5 (`rules/script.md`).
- **Non-prompt knobs:** `tenant_pipeline_config` → `inference_steps`, `seed`, `punch_in` (silent-first),
  `intro_seconds`/`outro_seconds`/`tail_seconds`; deeper model params live in the Wan ComfyUI workflow on the pod.
- **Tune:** *how it moves (camera, motion description)* → `rules/script.md` (the motion-prompt section)
  and the brand's `video_generation_preferences`. *Steps/seed/punch-in* → Run-settings (web).

## 8. Lip-sync — LatentSync
- **Produces:** mouth movement matched to the audio.
- **Prompt source:** **none** — driven by audio + the `lips_expression` knob.
- **Tune:** `tenant_pipeline_config.lips_expression` (Run-settings → "Lips expression").

## 9. QC — the image validator (gate before realism)
- **Produces:** pass/fail + retry decision on the Step-2 composite.
- **Brain:** `qc.py`
- **RULE BOOK (tune here):** `rules/qc.md` — note its format: the file is split on `===RUBRIC===`
  into the system prompt (top) and the rubric (bottom). Edit the system half to change *how strict /
  what it checks*, the rubric half to change *the checklist*.
- **DB inputs:** `products` → `name`, `packaging`, **`qc_brief`** (the ground-truth reference),
  `qc_max_retries` (attempt budget).
- **Model:** Sonnet (`QC_MODEL`, default `claude-sonnet-4-5-…`).
- **Stored at:** `qc_checks` (`passed`, `qc_reason`, `scores`, per attempt).
- **Tune:** *strictness / what QC enforces* → `rules/qc.md`. *Per-product retry budget* →
  `products.qc_max_retries` (web Run-settings, "QC retry loop count").

## 10. QC reference brief — one-time per product
- **Produces:** `products.qc_brief` — the precise description of the real box that QC (#9) checks against.
- **Brain:** `generate_qc_brief.py` (CLI) and/or the `generate-qc-brief` Edge Function (web setup).
- **RULE BOOK (tune here):** `rules/qc_brief_builder.md` (the vision system prompt).
- **DB inputs:** the product photo (`products.reference_asset_id` → `media_assets`) + `products.name`,
  `packaging`, `mask_prompt`.
- **Model:** Opus vision.
- **Tune:** *how the QC reference is written* → `rules/qc_brief_builder.md`, then regenerate with
  `python generate_qc_brief.py --tenant <id> --force`.

---

## Quick "I want to change ___ → edit ___" table
| I want to change… | Edit this | Scope |
|---|---|---|
| How faces look | `rules/phaseA.md` | all tenants |
| How scenes are framed/lit | `rules/step1.md` | all tenants |
| How the product is placed/composited | `rules/step2_qwen.md` | all tenants |
| The box-protect mask | `products.mask_prompt` (web) | one tenant |
| Realism strength / LoRA / denoise | realism ComfyUI workflow (pod) | global (pod) |
| Script tone, hooks, motion-prompt style | `rules/script.md` | all tenants |
| One brand's voice / what to say / avoid | `script_company_info` + `script_directives` (briefs) | one tenant |
| Which TTS voice | account voice reference / default wav | one tenant / global |
| Camera/motion knobs, steps, seed, lips | `tenant_pipeline_config` (web Run-settings) | one tenant |
| QC strictness / checklist | `rules/qc.md` | all tenants |
| QC reference accuracy | `rules/qc_brief_builder.md` → regenerate `qc_brief` | all / one |

## How to debug a bad result (which prompt actually ran)
Per output, read the stored prompts (these are the real strings that hit the models):
- images → `image_generations` (`stage_name`, `prompt`, `negative_prompt`, `mask_prompt`, `seed`)
- video shots → `media_generations` (`stage_name='shot_n'`, `prompt`=motion, `params.dialogue`)
- the Opus authoring step → `llm_calls` (`purpose`, `user_message`, `raw_response`, `parsed_json`)
Find the weak stage there, edit its rule book (or the tenant's DB data) per the table above, re-run, compare.
