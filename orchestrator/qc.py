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

QC_MODEL = os.environ.get("QC_MODEL", "claude-sonnet-4-5-20250929")
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


def _prompts(product: dict) -> tuple[str, str]:
    raw = (RULES_DIR / "qc.md").read_text(encoding="utf-8")
    raw = raw.replace("{{PRODUCT_REFERENCE}}", _reference(product))
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


def _score(result: dict, product_strings: str) -> dict:
    defects: list[str] = []
    pc = result.get("person_count")
    if pc is not None and pc != 1:
        defects.append(f"show exactly one person (not {pc})")
    if result.get("has_extra_limbs"):
        defects.append("show exactly two arms, two hands and two legs — no extra limbs")
    if result.get("has_malformed_hand"):
        defects.append("each wrist has exactly one clean natural hand with one palm — no doubled or extra palms")
    if result.get("has_malformed_arm_or_leg"):
        defects.append("keep every arm and leg natural, separate, and attached at the correct place")
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
    if not result.get("product_text_legible", True):
        defects.append(f"the box text must clearly show {product_strings}")
    if not result.get("box_theme_ok", True):
        defects.append("the product box packaging must match the reference shape, colours and graphics")
    if result.get("product_scale_wrong"):
        defects.append("scale the box to the person's hand — roughly palm-width, never wider than the forearm")

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
    keys = ["person_count", "has_extra_limbs", "has_malformed_hand", "has_malformed_arm_or_leg",
            "has_blatant_finger_blunder", "face_grossly_distorted", "placement_illogical",
            "product_visible", "multiple_distinct_products", "product_shape_broken",
            "product_text_legible", "box_theme_ok", "product_scale_wrong"]
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


def validate(image_bytes: bytes, media_type: str, product: dict, api_key: str, scenario_id: str = "?") -> dict:
    """Run BALANCED QC. On any QC-infrastructure error, treat as PASS (don't punish the image)."""
    system, rubric = _prompts(product)
    print(f"[qc] {scenario_id}: validating via {QC_MODEL}…")
    try:
        resp = Anthropic(api_key=api_key).messages.create(
            model=QC_MODEL, max_tokens=1500, system=system,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.b64encode(image_bytes).decode()}},
                {"type": "text", "text": rubric},
            ]}])
        result = _parse_json(resp.content[0].text)
    except Exception as e:
        print(f"[qc] {scenario_id}: QC ERROR — {type(e).__name__}: {e} (treating as pass)")
        return {"passed": True, "score": 0.5, "checks": {}, "issues": [], "recommendation": "use",
                "confidence": 0.0, "error": f"{type(e).__name__}: {e}", "model": QC_MODEL}
    decision = _score(result, _product_strings(product))
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
    out = sb().table("outputs").select("step2_asset_id").eq("persona_id", persona[0]["id"]).eq("scenario_key", sys.argv[2]).limit(1).execute().data
    if not out or not out[0].get("step2_asset_id"):
        sys.exit(f"[qc] no step2 composite for scenario {sys.argv[2]!r} — run step2 first")
    asset = sb().table("media_assets").select("bucket,path,mime_type").eq("id", out[0]["step2_asset_id"]).limit(1).execute().data[0]
    image_bytes = sb().storage.from_(asset["bucket"]).download(asset["path"])
    product = sb().table("products").select("name,packaging,qc_brief,qc_max_retries").eq("tenant_id", tenant_id).limit(1).execute().data[0]
    api_key = sb().rpc("get_tenant_anthropic_key", {"p_tenant_id": tenant_id}).execute().data
    using = "stored qc_brief" if (product.get("qc_brief") or "").strip() else "FALLBACK packaging (no qc_brief yet — run generate_qc_brief.py for best accuracy)"
    print(f"[qc] product reference: {using} | qc_max_retries={product.get('qc_max_retries')}")
    decision = validate(image_bytes, asset.get("mime_type") or "image/jpeg", product, api_key, sys.argv[2])
    print("\n" + json.dumps({k: decision[k] for k in ("passed", "score", "recommendation", "confidence", "issues", "checks")}, indent=2))


if __name__ == "__main__":
    main()