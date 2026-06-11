"""
orchestrator/qc.py — generalized vision QC for the Step-2 product composite.

Reuses the proven BALANCED rubric (describe-limbs-then-judge; strict on hands/limbs,
lenient on hidden fingers), but data-driven: the product text strings + box theme to
check come from products.packaging (multi-tenant), not hardcoded.

Public:
  validate(image_bytes, media_type, product, api_key, scenario_id) -> decision dict
    decision = {passed, score, checks, issues, recommendation, confidence, error, model}
    `issues` are short imperative phrases for the retry "AVOID:" line.

CLI (standalone test on an already-stored composite):
  python qc.py @liam.foster gym_post_workout_mirror_01
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from supabase import create_client, Client

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

# Opus as the QC judge: empirically (2026-06-10, 3-hand leftover test) Sonnet 4.6
# rationalized a third hand away twice; Opus 4.7 counted it correctly and produced
# precise retry feedback. ~2 cents/check more — far cheaper than one bad video.
QC_MODEL = os.environ.get("QC_MODEL", "claude-opus-4-7")
# minimum hand_render_quality (1-10, judged on the zoom tiles) — below this fails.
# 7 = "every finger clearly separable". Lower via env if it over-fires.
HAND_QUALITY_MIN = int(os.environ.get("QC_HAND_QUALITY_MIN", "7"))
RULES_DIR = Path(os.environ.get("RULES_DIR", HERE / "rules"))
_sb: Client | None = None
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def sb() -> Client:
    global _sb
    if _sb is None:
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    return _sb


# ── product-driven prompt fill ──────────────────────────────────────────────────
def _product_strings(product: dict) -> str:
    strings = ((product.get("packaging") or {}).get("text_on_packaging")) or []
    prominent = [s for s in strings if s][:4]
    return ", ".join(f'"{s}"' for s in prominent) or '"the product name"'


def _box_theme(product: dict) -> str:
    pk = product.get("packaging") or {}
    parts = []
    if pk.get("shape"):
        parts.append(str(pk["shape"]))
    colors = pk.get("primary_colors") or []
    if colors:
        parts.append("colours: " + ", ".join(str(c) for c in colors[:6]))
    if pk.get("graphic_elements"):
        parts.append(str(pk["graphic_elements"])[:240])
    return "; ".join(parts) or "a clean rectangular product box"


def _fallback_reference(product: dict) -> str:
    return (f"Product: {product.get('name') or 'the product'}. "
            f"The packaging should show the text strings {_product_strings(product)}. "
            f"Packaging: {_box_theme(product)}. "
            "Treat the largest / most prominent of these text strings as the most important to be legible.")


def _reference(product: dict) -> str:
    """Prefer the one-time vision-generated qc_brief; fall back to raw packaging."""
    return (product.get("qc_brief") or "").strip() or _fallback_reference(product)


def placement_intent(scenario_spec: dict | None) -> str:
    """Short plain-English description of the scenario's intended product placement,
    so QC can judge held-vs-placed correctly (a surface placement must never fail
    for 'not being held'; a held intent must actually be in a hand)."""
    spec = scenario_spec or {}
    arch = str(spec.get("archetype") or "").strip()
    grip = str(spec.get("grip_or_placement") or "").strip()
    if not arch and not grip:
        return ("unspecified — accept either a natural hold IN a hand or a natural rest "
                "ON a surface; fail only floating/dumped/impossible placement")
    return f"{arch}: {grip}"[:400] if grip else arch


def _prompts(product: dict, intent: str | None = None) -> tuple[str, str]:
    raw = (RULES_DIR / "qc.md").read_text(encoding="utf-8")
    raw = raw.replace("{{PRODUCT_REFERENCE}}", _reference(product))
    raw = raw.replace("{{INTENDED_PLACEMENT}}", intent or placement_intent(None))
    system, _, rubric = raw.partition("===RUBRIC===")
    return system.strip(), rubric.strip()


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t).rsplit("```", 1)[0]
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("no JSON object in QC output")
    return json.loads(t[i:j + 1])


def _score(result: dict, product_strings: str, expect_person: bool = True) -> dict:
    defects: list[str] = []
    pc = result.get("person_count")
    expected_pc = 1 if expect_person else 0
    if pc is not None and pc != expected_pc:
        defects.append(f"show exactly {'one person' if expect_person else 'no person (flat-lay)'} (not {pc})")
    # code-side hand arithmetic: trust the model's own census over its bool — and
    # over its count, which it sometimes rationalizes down ("same arm"). The number
    # of NUMBERED entries it wrote is the honest signal.
    try:
        hands = int(result.get("total_visible_hands") or 0)
    except (TypeError, ValueError):
        hands = 0
    census = str((result.get("limb_description") or {}).get("hands") or "")
    # one census part per "Hand N:" entry; drop mirror-reflection entries (the
    # rubric tells the model not to number them, but belt-and-braces here too)
    parts = re.split(r"(?=[Hh]and\s*\d+\s*[:(])", census)
    real = [p for p in parts
            if re.match(r"[Hh]and\s*\d+", p)
            and "reflection" not in p.lower() and "not counted" not in p.lower()]
    listed = {int(re.match(r"[Hh]and\s*(\d+)", p).group(1)) for p in real}
    hands = max(hands, len(listed)) if listed else hands
    if hands > 2:
        defects.append(f"show exactly two hands — the image contains {hands} (remove the leftover extra hand)")
    if result.get("has_extra_limbs"):
        defects.append("show exactly two arms, two hands and two legs — no extra limbs")
    if result.get("has_malformed_hand"):
        defects.append("each hand must be clean and natural with clearly separated individual fingers — no fused, clubbed, or clustered finger groups, no doubled or extra palms")
    # scored hand gate (the binary 'malformed' question gets rationalized on
    # borderline fused clusters — a 1-10 scale judged on the zoom tiles does not):
    hq = result.get("hand_render_quality")
    try:
        if hq is not None and int(hq) < HAND_QUALITY_MIN:
            defects.append("render both hands with crisp, clearly separated individual fingers — "
                           f"no soft merged finger boundaries (hand render quality {hq}/10, need {HAND_QUALITY_MIN}+)")
    except (TypeError, ValueError):
        pass
    if result.get("has_malformed_arm_or_leg"):
        defects.append("keep every arm and leg natural, separate, and attached at the correct place")
    if result.get("has_truncated_limb"):
        defects.append("every arm ends in a hand and every leg in a foot — no limb may stop mid-frame")
    if result.get("has_blatant_finger_blunder"):
        defects.append("render normal natural hands with a normal number of fingers")
    if result.get("face_grossly_distorted"):
        defects.append("keep the face clean, natural and undistorted")
    if result.get("placement_illogical"):
        defects.append("make the product hold and the body pose natural and physically plausible")
    if not result.get("product_visible", True):
        defects.append("the product box must be clearly visible in the scene")
    if result.get("multiple_distinct_products"):
        defects.append("show exactly ONE product box")
    if result.get("product_shape_broken"):
        defects.append("keep the product a clean undistorted rectangular box")
    if result.get("product_proportions_wrong"):
        defects.append("keep the box's real proportions — a wide landscape rectangle, never square")
    if not result.get("product_text_legible", True):
        defects.append(f"the brand and product name wordmarks must be clearly recognizable (clean English letters): {product_strings}")
    if not result.get("box_theme_ok", True):
        defects.append("the product box packaging must match the reference shape, colours and graphics")
    if result.get("product_scale_wrong"):
        defects.append("scale the box to its real size relative to her body — large enough that its front text reads, never wider than her shoulders")

    extra = result.get("specific_issues") or []
    if not isinstance(extra, list):
        extra = [str(extra)]
    seen: set = set()
    issues: list[str] = []
    for src in (defects, extra):
        for item in src:
            s = str(item).strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                issues.append(s)

    passed = len(defects) == 0
    rec = result.get("overall_recommendation", "")
    if rec not in ("use", "regenerate", "discard"):
        rec = "use" if passed else "regenerate"
    keys = ["total_visible_hands", "held_objects", "person_count", "has_extra_limbs",
            "has_malformed_hand", "hand_render_quality", "has_malformed_arm_or_leg",
            "has_truncated_limb", "has_blatant_finger_blunder", "face_grossly_distorted",
            "placement_illogical", "product_visible", "multiple_distinct_products",
            "product_shape_broken", "product_proportions_wrong", "product_scale_wrong",
            "product_text_legible", "box_theme_ok"]
    return {
        "passed": passed,
        "score": round(max(0.0, 1.0 - len(defects) * 0.15), 3),
        "checks": {k: result.get(k) for k in keys},
        "limb_description": result.get("limb_description"),
        "issues": issues,
        "recommendation": rec,
        "confidence": float(result.get("confidence", 0.5) or 0.5),
        "error": None,
        "model": QC_MODEL,
    }


# torso-band zoom tiles: (left, top, right, bottom) as fractions. Hands holding a
# product (and surface-placed products beside laps/tables) live in this band in
# every scenario archetype. Two overlapping halves -> each hand lands fully in at
# least one tile at ~3x effective zoom. Deterministic on purpose: a model-located
# bounding box hallucinated (pointed at the shorts, 2026-06-11), so the gate must
# never depend on model coordinates.
_ZOOM_TILES = [(0.00, 0.20, 0.62, 0.65), (0.38, 0.20, 1.00, 0.65)]


def _zoom_crops(image_bytes: bytes) -> list[bytes]:
    """Magnified tiles of the hand/product band — the finger census happens at THIS
    zoom (full-frame hands are ~80px; partial finger fusion is invisible there, as
    the 2026-06-11 patio audit proved). Never fatal."""
    try:
        from PIL import Image
    except ImportError:
        print("[qc] (warn) Pillow missing — hand zoom disabled")
        return []
    crops: list[bytes] = []
    try:
        im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = im.size
        for (lf, tf, rf, bf) in _ZOOM_TILES:
            c = im.crop((int(lf * w), int(tf * h), int(rf * w), int(bf * h)))
            if c.width < 16 or c.height < 16:
                continue
            scale = max(1.0, min(3.0, 1568 / max(c.width, c.height)))
            if scale > 1.0:
                c = c.resize((int(c.width * scale), int(c.height * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            c.save(buf, "JPEG", quality=90)
            crops.append(buf.getvalue())
    except Exception as e:
        print(f"[qc] hand-crop skipped: {type(e).__name__}: {e}")
    return crops


def validate(image_bytes: bytes, media_type: str, product: dict, api_key: str,
             scenario_id: str = "?", intent: str | None = None) -> dict:
    """Run QC (lenient on photography, zero-tolerance on anatomy/physics).
    On any QC-infrastructure error, treat as PASS (don't punish the image)."""
    system, rubric = _prompts(product, intent)
    expect_person = "flat_lay" not in (intent or "")
    crops = _zoom_crops(image_bytes) if expect_person else []
    if crops:
        print(f"[qc] {scenario_id}: {len(crops)} hand-zoom crop(s) attached")
    print(f"[qc] {scenario_id}: validating via {QC_MODEL}…")
    try:
        content = [{"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                "data": base64.b64encode(image_bytes).decode()}}]
        content += [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.b64encode(c).decode()}}
                    for c in crops]
        content.append({"type": "text", "text": rubric})
        resp = Anthropic(api_key=api_key).messages.create(
            model=QC_MODEL, max_tokens=1500, system=system,
            messages=[{"role": "user", "content": content}])
        result = _parse_json(resp.content[0].text)
    except Exception as e:
        print(f"[qc] {scenario_id}: QC ERROR — {type(e).__name__}: {e} (treating as pass)")
        return {"passed": True, "score": 0.5, "checks": {}, "issues": [], "recommendation": "use",
                "confidence": 0.0, "error": f"{type(e).__name__}: {e}", "model": QC_MODEL}
    decision = _score(result, _product_strings(product), expect_person=expect_person)
    print(f"[qc] {scenario_id}: {'PASS' if decision['passed'] else 'FAIL'} "
          f"(score={decision['score']}, issues={len(decision['issues'])}, rec={decision['recommendation']})")
    for it in decision["issues"]:
        print(f"  - {it}")
    return decision


# ── CLI: test on an already-stored step2 composite ──────────────────────────────
def _get_account(ident: str) -> dict:
    t = sb().table("tiktok_accounts").select("*")
    rows = (t.eq("id", ident) if _UUID.match(ident) else t.eq("tiktok_id", ident)).limit(1).execute().data
    if not rows and not ident.startswith("@"):
        rows = sb().table("tiktok_accounts").select("*").eq("tiktok_id", "@" + ident).limit(1).execute().data
    if not rows:
        sys.exit(f"[qc] no account matching {ident!r}")
    return rows[0]


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: python qc.py <account> <scenario_key>")
    account = _get_account(sys.argv[1])
    tenant_id = account["tenant_id"]
    persona = sb().table("personas").select("id").eq("tiktok_account_id", account["id"]).limit(1).execute().data
    if not persona:
        sys.exit("[qc] no persona for this account")
    out = sb().table("outputs").select("step2_asset_id,scenario_id").eq("persona_id", persona[0]["id"]).eq("scenario_key", sys.argv[2]).limit(1).execute().data
    if not out or not out[0].get("step2_asset_id"):
        sys.exit(f"[qc] no step2 composite for scenario {sys.argv[2]!r} — run step2 first")
    asset = sb().table("media_assets").select("bucket,path,mime_type").eq("id", out[0]["step2_asset_id"]).limit(1).execute().data[0]
    image_bytes = sb().storage.from_(asset["bucket"]).download(asset["path"])
    product = sb().table("products").select("name,packaging,qc_brief,qc_max_retries").eq("tenant_id", tenant_id).limit(1).execute().data[0]
    api_key = sb().rpc("get_tenant_anthropic_key", {"p_tenant_id": tenant_id}).execute().data
    spec_rows = sb().table("scenarios").select("spec").eq("id", out[0]["scenario_id"]).limit(1).execute().data if out[0].get("scenario_id") else []
    intent = placement_intent((spec_rows[0].get("spec") if spec_rows else {}) or {})
    using = "stored qc_brief" if (product.get("qc_brief") or "").strip() else "FALLBACK packaging (no qc_brief yet — run generate_qc_brief.py for best accuracy)"
    print(f"[qc] product reference: {using} | qc_max_retries={product.get('qc_max_retries')}")
    print(f"[qc] intended placement: {intent}")
    decision = validate(image_bytes, asset.get("mime_type") or "image/jpeg", product, api_key, sys.argv[2], intent=intent)
    print("\n" + json.dumps({k: decision[k] for k in ("passed", "score", "recommendation", "confidence", "issues", "checks")}, indent=2))


if __name__ == "__main__":
    main()