"""
src/step_1_prompt_builder.py — Stage 1 PuLID prompt generation via Opus 4.7.

Mirrors the shape of src/step_2_prompt_builder.py — same client, same JSON
validator, same context-loading pattern. The differences are Stage-1 specific:
  - System prompt file: prompts/master_prompt_step1.md
  - Function signature: build_step_1_prompt(scenario, gender=None)
  - Static context: persona.yaml + brand.yaml + do_dont.md (NO product.yaml,
    Stage 1 does not render the product)
  - Required JSON keys: ["step_1_image_prompt"]

GENDER-AWARENESS (added):
  The supabase flow generates a per-account, gender-correct persona (Phase A)
  and overrides PERSONA_YAML_PATH to the per-account persona.yaml. But the
  SCENARIO outfit + pronoun scaffolding were historically female-only. This
  builder now:
    - selects scenario["outfit"]["male"|"female"] by the account's gender when
      outfit is a {female, male} dict (collapsing it to ONE string before the
      scenario is shown to Opus, so the model never sees both),
    - sets the sentence-1 pronoun anchor to He/She by gender,
    - and adds a STRICT no-mixing instruction so no cross-gender garment,
      hairstyle, or pronoun leaks in.
  Backward-compatible: a flat-string outfit (legacy) and gender=None reproduce
  the original female-default behavior exactly. A gendered (dict) outfit with an
  unresolvable/missing gender raises loudly rather than silently guessing —
  that is the explicit guard against "wrong-gender features" recurring.

Production rules carried over from master_prompt_step1.md (post-edits):
  - Word budget: 200-250 standard, 240-280 close-up. Hard ceiling 290.
  - fal_pulid_params.max_sequence_length MUST be "512" (NOT "256").
  - fal_pulid_params.negative_prompt MUST be present in every envelope.
  - Persona descriptors and identity_lock lines from persona.yaml must be
    copied VERBATIM, never paraphrased.
  - Early + late photoreal anchors are mandatory (anti-cartoonish drift).
"""

import os
import json
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

CLAUDE_MODEL = "claude-opus-4-7"

# Project paths — repo root is two levels up from this file
REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT_PATH = REPO_ROOT / "prompts" / "master_prompt_step1.md"

PERSONA_YAML_PATH = REPO_ROOT / "assets" / "persona.yaml"
BRAND_YAML_PATH = REPO_ROOT / "brand" / "brand.yaml"
DO_DONT_MD_PATH = REPO_ROOT / "brand" / "do_dont.md"

_client: Anthropic | None = None
_static_context_cache: dict[str, str] | None = None


# ──────────────────────────────────────────────────────────────────────────
# Gender helpers (added)
# ──────────────────────────────────────────────────────────────────────────

_PRONOUNS = {
    "male":   {"subject": "He",  "object": "him", "possessive": "his"},
    "female": {"subject": "She", "object": "her", "possessive": "her"},
}


def _normalize_gender(gender) -> str | None:
    """Map an account gender value to 'male' / 'female', or None if unknown."""
    g = (gender or "").strip().lower()
    if g in ("m", "male", "man", "men", "masculine", "he", "him", "boy"):
        return "male"
    if g in ("f", "female", "woman", "women", "feminine", "she", "her", "girl"):
        return "female"
    return None


def _resolve_outfit(scenario: dict, norm_gender: str | None) -> dict:
    """
    Return a COPY of the scenario with `outfit` collapsed to a single string
    for the given gender. Never mutates the caller's dict and never shows Opus
    both gender variants.

    - outfit is a {female, male} dict  -> require the matching gender variant;
      raise loudly if gender is unresolved or the variant is missing.
    - outfit is a plain string         -> leave as-is (legacy single-persona).
    - outfit is missing / empty        -> leave as-is (persona-absent scenario).
    """
    sc = dict(scenario)
    outfit = sc.get("outfit")
    sid = sc.get("id", "?")

    if isinstance(outfit, dict):
        if norm_gender is None:
            raise ValueError(
                f"scenario {sid}: outfit is gendered (keys={sorted(outfit)}) but the "
                f"persona gender could not be resolved — refusing to guess and risk "
                f"the wrong-gender outfit. Pass gender='male'/'female' to "
                f"build_step_1_prompt."
            )
        chosen = outfit.get(norm_gender)
        if not chosen or not str(chosen).strip():
            raise ValueError(
                f"scenario {sid}: outfit has no non-empty '{norm_gender}' variant "
                f"(keys={sorted(outfit)}). Add the {norm_gender} outfit to "
                f"scenarios.yaml for this scenario."
            )
        sc["outfit"] = str(chosen).strip()

    return sc


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment (.env)")
        _client = Anthropic(api_key=api_key)
    return _client


def _load_static_context() -> dict[str, str]:
    """Load and cache the three static context files (no product.yaml for Stage 1)."""
    global _static_context_cache
    if _static_context_cache is not None:
        return _static_context_cache

    paths = {
        "persona_yaml": PERSONA_YAML_PATH,
        "brand_yaml": BRAND_YAML_PATH,
        "do_dont_md": DO_DONT_MD_PATH,
    }
    missing = [(k, p) for k, p in paths.items() if not p.exists()]
    if missing:
        msg = "Missing required context files:\n" + "\n".join(
            f"  - {k}: {p}" for k, p in missing
        )
        raise FileNotFoundError(msg)

    _static_context_cache = {k: p.read_text(encoding="utf-8") for k, p in paths.items()}
    return _static_context_cache


def _parse_json(text: str) -> dict:
    """
    Defensive JSON parse for Opus output. Uses src/json_utils.validate_json_output
    which strips fences/prose/trailing commas and checks required keys.
    """
    from src.json_utils import validate_json_output

    return validate_json_output(
        text,
        required_keys=["step_1_image_prompt"],
    )


def build_step_1_prompt(scenario: dict, gender: str | None = None) -> dict:
    """
    Build the Step 1 PuLID prompt envelope.

    Args:
        scenario: parsed scenarios.yaml entry for this scenario. `outfit` may be
                  a plain string (legacy) or a {female, male} dict (gendered).
        gender:   the account's gender ('male'/'female' or m/f/man/woman...).
                  REQUIRED when the scenario's outfit is a gendered dict; for a
                  legacy flat-string outfit it may be omitted (defaults to the
                  historical female scaffolding).

    Returns:
        dict with keys: step_1_image_prompt (str), word_count (int),
        fal_pulid_params (with max_sequence_length="512" + negative_prompt), etc.
    """
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(f"missing system prompt: {SYSTEM_PROMPT_PATH}")

    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    ctx = _load_static_context()

    # ── Resolve gender + outfit BEFORE Opus sees the scenario ────────────
    norm = _normalize_gender(gender)
    scenario = _resolve_outfit(scenario, norm)          # collapses outfit to one string
    gender_label = norm or "female"                     # legacy default when unknown
    pron = _PRONOUNS[gender_label]
    subj, obj, poss = pron["subject"], pron["object"], pron["possessive"]

    user_message = "\n".join(
        [
            "=== persona.yaml (use prompt_descriptors VERBATIM — never paraphrase) ===",
            ctx["persona_yaml"],
            "",
            "=== brand.yaml ===",
            ctx["brand_yaml"],
            "",
            "=== do_dont.md (compliance) ===",
            ctx["do_dont_md"],
            "",
            "=== SCENARIO ===",
            json.dumps(scenario, indent=2),
            "",
            "=== PERSONA GENDER (STRICT — NEVER MIX GENDERS) ===",
            f"This persona is {gender_label}. Pronouns: {subj}/{obj} (possessive {poss}).",
            f"  - Use {subj}/{obj} consistently in EVERY sentence of step_1_image_prompt.",
            f"  - The persona.yaml descriptors are already {gender_label} — copy them verbatim.",
            f"  - The scenario's `outfit` field is the correct {gender_label} outfit. Render it",
            "    exactly as written. Do NOT substitute, add, or blend in any garment, hairstyle,",
            "    jewellery, footwear, or body-language cue associated with another gender.",
            f"  - If any scenario field (pose, lighting, grip) uses 'she'/'her', treat it as legacy",
            f"    wording and convert it to {subj}/{obj} for this {gender_label} persona.",
            "  - The face, body, hair, outfit, and pronouns must all read as ONE consistent",
            f"    gender ({gender_label}). Cross-gender features are a hard failure.",
            "",
            "=== TASK ===",
            "Build the Step 1 prompt envelope per the system prompt above.",
            "",
            "WORD BUDGET (STRICT):",
            "  step_1_image_prompt: 200-250 words for standard framing,",
            "  240-280 for close-up scenarios (framing field contains 'close-up').",
            "  Hard ceiling 290 words. Every word must earn its place. Do not pad.",
            "",
            "PERSONA DESCRIPTOR (VERBATIM):",
            "  Copy face_descriptor_short OR face_descriptor_full from persona.yaml",
            "  exactly. Do not paraphrase. Choose:",
            "    - face_descriptor_short + identity_lock_strong for medium framing",
            "    - face_descriptor_full + identity_lock_close_up for close-up framing",
            "    - face_descriptor_short + identity_lock_minimal for full-body wide",
            "",
            "PHOTOREAL ANCHORS (MANDATORY):",
            "  Early anchor in sentence 1 (immediately after persona descriptor):",
            "    'captured in a candid amateur smartphone snapshot with natural skin",
            f"    texture and visible pores. {subj} is wearing...'",
            "  Late anchor in sentence 5 (before identity-lock line):",
            "    'Shot on iPhone 15 Pro [camera variant]. Real photograph, not",
            "    AI-generated, no model pose, candid moment.'",
            "",
            "fal_pulid_params (HARD REQUIREMENTS — ALL MUST BE PRESENT):",
            "  - max_sequence_length: \"512\" (NEVER \"256\" — \"256\" truncates the",
            "    sentence-5 camera anchor and identity-lock line on every call)",
            "  - negative_prompt: anti-defect comma-separated keyword list including",
            "    'plastic skin, airbrushed skin, extra limbs, six fingers, fused",
            "    fingers, deformed hands, distorted face, AI-generated look,",
            "    illustration, 3D render, watermark, text, signature'",
            "  - id_weight: 1.0 (fal API cap) for persona shots, 0.5 for flat-lays",
            "  - true_cfg: 1.5 medium, 1.7 close-up, 1.2 full-body wide",
            "  - guidance_scale: 3.5 default",
            "  - num_inference_steps: 30",
            "  - image_size: {width: 768, height: 1344}",
            "  - num_images: 1, output_format: \"jpeg\", enable_safety_checker: true",
            "",
            "NO PRODUCT in Step 1. Never mention 'Alluvi', 'Tirzepatide', 'the box',",
            "  'the product', 'the packaging'. The hand reserved for the product",
            "  must be described as 'currently empty' with a relaxed open position.",
            "",
            "Output JSON only. No preamble. No markdown fences.",
        ]
    )

    scenario_id = scenario.get("id", "?")
    print(f"[step_1_prompt_builder] Step 1 -> Opus 4.7 for scenario {scenario_id} "
          f"(gender={gender_label})")
    response = _get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    output = _parse_json(response.content[0].text)

    wc = output.get("word_count", 0)
    print(f"[step_1_prompt_builder]   Step 1 done: word_count={wc}")
    return output