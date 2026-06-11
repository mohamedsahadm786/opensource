"""
orchestrator/script_gen.py — multi-shot video SCRIPT generation (Claude, data-driven).

Mirrors the proven video_pipeline/multishot/script_builder_multi.py, but every input is
per-tenant DB data and the rule book is the generic rules/script.md:

  BRAND KNOWLEDGE   <- tenants.script_company_info   (company/brand sections)
  PRODUCT KNOWLEDGE <- products.product_info          (product knowledge)
  SCRIPT DIRECTIVES <- tenants.script_directives       (dialogue rules + priorities; tenant-authored)
  PERSONA           <- tiktok_accounts (name/gender/country/language/age)
  SCENE             <- scenarios.spec
  Anthropic key     <- Vault (passed in by the caller)

Output schema (1:1 with the shots the video-service consumes):
  { "narrative_theme","language","continuity_block","hook_style","scene_mood",
    "shots":[ {dialogue, estimated_speech_seconds, wan_motion_prompt, wan_negative_prompt}, ... ] }

The pure helpers (slice_sections, assemble_inputs, build_user_message, parse_script,
validate_script) take plain dicts and are unit-testable without network or DB.
"""
from __future__ import annotations

import json
import re
from typing import Any

WORDS_PER_SECOND = 3.0   # F5 English pace (matches script_builder_multi)
DEFAULT_MAX_TOKENS = 1500

# Which (section, keys) come from which DB source. Mirrors the proven _BRAND_SECTIONS,
# but split across the tenant's 3 script inputs.
COMPANY_SECTIONS = [
    ("system_identity", ["brand_name", "product_name", "brand_positioning"]),
    ("brand_personality", ["brand_voice", "core_traits"]),
    ("marketing_language_engine", ["high_performing_phrases", "hook_styles", "conversation_styles"]),
    ("video_generation_preferences", ["camera_motion", "motion_behavior", "visual_style"]),
    ("scene_generation_system", ["scene_moods"]),
]
DIRECTIVE_SECTIONS = [
    ("dialogue_generation_rules", ["dialogue_style", "dialogue_requirements", "example_dialogues"]),
    ("ai_generation_priorities", ["highest_priority", "negative_generation_controls"]),
]
# product_info IS the product_knowledge dict; keep the script-relevant keys (omit the
# medical-leaning mechanism_summary / scientific_context to stay claim-safe).
PRODUCT_KEYS = ["product_name", "product_type", "wellness_associations", "positive_lifestyle_language"]


def slice_sections(src: dict, sections: list) -> dict:
    """Pick (section -> chosen keys) from a source dict, dropping empties."""
    src = src or {}
    out: dict[str, Any] = {}
    for section, keys in sections:
        block = src.get(section) or {}
        if not isinstance(block, dict):
            continue
        picked = {k: block.get(k) for k in keys if block.get(k) is not None}
        if picked:
            out[section] = picked
    return out


def assemble_inputs(company_info: dict, product_info: dict, directives: dict) -> dict:
    """Build the three labeled blocks the rule book expects, from the 3 DB sources."""
    brand = slice_sections(company_info, COMPANY_SECTIONS)
    product = {k: (product_info or {}).get(k) for k in PRODUCT_KEYS if (product_info or {}).get(k) is not None}
    directive = slice_sections(directives, DIRECTIVE_SECTIONS)
    return {"brand": brand, "product": product, "directives": directive}


def _scene_from_scenario(scenario_key: str, spec: dict) -> dict:
    spec = spec or {}
    return {
        "scenario_id": scenario_key,
        "scene": spec.get("scene") or spec.get("location") or spec.get("setting"),
        "mood": spec.get("mood"),
        "framing": spec.get("framing"),
        "archetype": spec.get("archetype") or spec.get("category"),
        "lighting": spec.get("lighting"),
        "palette": spec.get("palette"),
        "raw": spec,
    }


def build_user_message(blocks: dict, persona: dict, scenario_key: str, scenario_spec: dict,
                       num_shots: int, target_seconds: int,
                       pose_prompt: str | None = None) -> tuple[str, dict]:
    total_target_seconds = num_shots * target_seconds
    target_total_words = round(total_target_seconds * WORDS_PER_SECOND)
    words_per_shot = max(1, round(target_total_words / max(1, num_shots)))

    persona_block = {k: persona.get(k) for k in ("name", "gender", "country", "language", "age")}
    scene_block = _scene_from_scenario(scenario_key, scenario_spec)

    # POSE GROUND TRUTH (V2, video.md): the real content of the photo every shot
    # animates — the step-2 image prompt + the scenario's placement intent. When
    # absent (legacy outputs), the block is omitted and behavior is unchanged.
    pose_parts = []
    grip = (scenario_spec or {}).get("grip_or_placement")
    if grip:
        pose_parts.append(f"Intended product placement in the photo: {grip}")
    if pose_prompt:
        pose_parts.append("The exact image-generation prompt that produced the photo "
                          "(this describes what the photo actually shows — hands, product, "
                          "phone, surfaces, framing):\n" + str(pose_prompt).strip())
    pose_block = ("\n\nPOSE GROUND TRUTH (the REAL content of the single photo that EVERY "
                  "shot animates — author all motion FROM this, never from imagination):\n"
                  + "\n\n".join(pose_parts)) if pose_parts else ""

    user_message = (
        "BRAND KNOWLEDGE (company/brand — content source):\n"
        + json.dumps(blocks.get("brand", {}), indent=2, ensure_ascii=False)
        + "\n\nPRODUCT KNOWLEDGE (the product to feature):\n"
        + json.dumps(blocks.get("product", {}), indent=2, ensure_ascii=False)
        + "\n\nSCRIPT DIRECTIVES (authoritative — what the dialogue must do/avoid):\n"
        + json.dumps(blocks.get("directives", {}), indent=2, ensure_ascii=False)
        + "\n\nPERSONA:\n" + json.dumps(persona_block, indent=2, ensure_ascii=False)
        + "\n\nSCENE:\n" + json.dumps(scene_block, indent=2, ensure_ascii=False)
        + pose_block
        + f"\n\nNUM_SHOTS: {num_shots}"
        + f"\n\nTARGET_SECONDS_PER_SHOT: {target_seconds}"
        + f"\n\nTOTAL_TARGET_SECONDS: {total_target_seconds}"
        + f"\n\nTARGET_TOTAL_WORDS: {target_total_words}"
        + f"\n\nWORDS_PER_SHOT: {words_per_shot}"
        + f"\n\nThe COMBINED dialogue across all {num_shots} shots must total about "
        + f"{target_total_words} words (hit this or slightly OVER — never significantly under), "
        + f"about {words_per_shot} words per shot. Name the brand "
        + "(BRAND KNOWLEDGE.system_identity.brand_name) naturally about twice across the whole "
        + "script and reference the product (PRODUCT KNOWLEDGE.product_name) at least once, per "
        + f"the rule book. Generate the JSON exactly as specified, with exactly {num_shots} shots. "
        + "STRICT JSON only."
    )
    meta = {"num_shots": num_shots, "target_seconds": target_seconds,
            "total_target_seconds": total_target_seconds, "target_total_words": target_total_words,
            "words_per_shot": words_per_shot}
    return user_message, meta


def parse_script(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            raise ValueError(f"no JSON object found in model output:\n{text[:400]}")
        return json.loads(m.group(0))


def validate_script(parsed: dict, num_shots: int) -> dict:
    shots = parsed.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("no 'shots' array in model output")
    total_words = 0
    for i, sh in enumerate(shots):
        for k in ("dialogue", "wan_motion_prompt", "wan_negative_prompt"):
            if not sh.get(k):
                raise ValueError(f"shot {i+1} missing '{k}'")
        wc = len(str(sh["dialogue"]).split())
        sh["_dialogue_word_count"] = wc
        total_words += wc
    return {"num_shots": len(shots), "requested_shots": num_shots, "total_words": total_words,
            "est_speech_seconds": round(total_words / max(0.1, WORDS_PER_SECOND), 1),
            "shot_count_matches": len(shots) == num_shots}


def generate_script(*, company_info: dict, product_info: dict, directives: dict, persona: dict,
                    scenario_key: str, scenario_spec: dict, num_shots: int, target_seconds: int,
                    api_key: str, rule_book: str, model: str,
                    pose_prompt: str | None = None) -> dict:
    """pick 3-source slices -> build prompt -> Claude (Opus) -> parse + validate.
    Returns {parsed, raw, usage, user_message, blocks, prompt_meta, stats}."""
    import anthropic  # lazy so the pure helpers test without the SDK

    blocks = assemble_inputs(company_info, product_info, directives)
    user_message, prompt_meta = build_user_message(
        blocks, persona, scenario_key, scenario_spec, num_shots, target_seconds,
        pose_prompt=pose_prompt)
    token_budget = min(32000, DEFAULT_MAX_TOKENS + num_shots * 900)

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(model=model, max_tokens=token_budget, system=rule_book,
                                  messages=[{"role": "user", "content": user_message}])
    raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    parsed = parse_script(raw)
    stats = validate_script(parsed, num_shots)
    usage = {"input_tokens": getattr(resp.usage, "input_tokens", None),
             "output_tokens": getattr(resp.usage, "output_tokens", None)} if getattr(resp, "usage", None) else {}
    return {"parsed": parsed, "raw": raw, "usage": usage, "user_message": user_message,
            "blocks": blocks, "prompt_meta": prompt_meta, "stats": stats}
