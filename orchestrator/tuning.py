"""
orchestrator/tuning.py — L4 prompt/script tuning loop.

Turns accumulated ratings into concrete, validated prompt edits:

  1. CREDIT ASSIGNMENT  find_weak_attributes(stats) — for each rubric dimension, find the
     attributes whose learned estimate sits well below that dimension's cross-attribute mean
     (with enough evidence). Those ingredients are dragging the dimension down.
  2. HYPOTHESIS         propose_edit(opus, weak) — ask Opus *why* and for a concrete, scoped
     prompt edit, OR a MODEL_LIMIT_NO_EDIT verdict when no wording can fix it.
  3. STORE              store_candidates() — upsert as tuning_suggestions(status='candidate').
  4. TEST               promote_to_testing() — capture the baseline estimate, start applying the
     edit at generation (get_active_edits returns testing+validated edits for a scenario's attrs).
  5. VALIDATE           ab_validate() — once enough NEW ratings accrued, compare the dimension's
     estimate to the captured baseline; delta >= +0.15 -> 'validated' (fold in forever), else 'rejected'.

Only get_active_edits feeds the generators, so candidates never silently change output; an edit
must earn 'testing' then prove itself before it sticks. Pure helpers are DB-free and unit-tested.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Optional

# thresholds (all tunable)
MIN_EVIDENCE_N = 15      # ratings on an attribute+dimension before we'll judge it
SCORE_DEFICIT = 0.40     # 1..5 points below the dimension mean to count as "weak"
GATE_DEFICIT = 0.15      # pass-probability points below the dimension mean
AB_DELTA = 0.15          # improvement (1..5 pts, or pass-prob pts) required to validate
MIN_NEW_EVIDENCE = 10    # new ratings required after promotion before validating

COMMERCIAL_DIMS = {"img_ad_worthiness", "vid_hook_strength"}

# human-readable rubric (the exact 25 the web asks) — gives Opus grounding
DIMENSION_MEANING = {
    "img_product_present": "Product present & correctly placed",
    "img_color_fidelity": "Product colour fidelity",
    "img_shape_dimensions": "Product shape & dimensions",
    "img_brand_text": "Brand text legible & correct",
    "img_productname_text": "Product-name text legible & correct",
    "img_grip_logic": "Grip / placement logic",
    "img_persona_identity": "Persona identity match",
    "img_scene_logic": "Scene logic & reflections",
    "img_scene_adherence": "Scene / prompt adherence",
    "img_aesthetic": "Aesthetic quality",
    "img_detail_realism": "Detail & realism",
    "img_lighting": "Lighting execution",
    "img_ad_worthiness": "Ad-worthiness / scroll-stop",
    "vid_product_identity": "Product identity preserved through motion",
    "vid_persona_identity": "Persona identity preserved through motion",
    "vid_no_artifacts": "No catastrophic artifacts",
    "vid_grip_maintained": "Grip maintained through motion",
    "vid_brand_text_motion": "Brand & product text legible through motion",
    "vid_motion_smoothness": "Motion smoothness",
    "vid_temporal_stability": "Temporal stability",
    "vid_dynamic_degree": "Dynamic degree",
    "vid_camera_motion": "Camera motion quality",
    "vid_physical_plausibility": "Physical plausibility",
    "vid_imaging_quality": "Imaging quality",
    "vid_hook_strength": "Hook strength / ad-worthiness",
}
# which generator each dimension's edit should target (used as a hint to Opus / for folding)
STAGE_OF_DIM = {d: ("video" if d.startswith("vid_") else "image") for d in DIMENSION_MEANING}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 1. credit assignment (pure) ───────────────────────────────────────────────────
def find_weak_attributes(stat_rows: list, min_n: int = MIN_EVIDENCE_N, top_k: int = 20) -> list:
    """Per (context, dimension), anchor = mean estimate across attributes; flag attributes whose
    estimate is >= margin below the anchor with >= min_n evidence. Returns worst-first."""
    groups = defaultdict(list)
    for r in stat_rows:
        if r.get("estimate") is None:
            continue
        groups[(r["context_key"], r["dimension"], r["kind"])].append(r)
    weak = []
    for (ctx, dim, kind), rows in groups.items():
        ests = [float(r["estimate"]) for r in rows]
        if len(ests) < 2:
            continue
        anchor = sum(ests) / len(ests)
        margin = GATE_DEFICIT if kind == "gate" else SCORE_DEFICIT
        for r in rows:
            if (r.get("n") or 0) < min_n:
                continue
            deficit = anchor - float(r["estimate"])
            if deficit >= margin:
                weak.append({
                    "context_key": ctx, "dimension": dim, "kind": kind,
                    "scope_key": r["attribute_key"], "estimate": round(float(r["estimate"]), 3),
                    "anchor": round(anchor, 3), "deficit": round(deficit, 3), "n": r["n"],
                })
    weak.sort(key=lambda w: w["deficit"], reverse=True)
    return weak[:top_k]


# ── 2. hypothesis (Opus) ───────────────────────────────────────────────────────────
def build_messages(w: dict, brand_hint: str = "") -> tuple:
    dim_desc = DIMENSION_MEANING.get(w["dimension"], w["dimension"])
    stage = STAGE_OF_DIM.get(w["dimension"], "image")
    attr_name, _, attr_val = w["scope_key"].partition("=")
    system = (
        "You are a creative-operations engineer tuning text-to-image and text-to-video prompts for "
        "short-form product ads. A specific scene/style attribute is statistically dragging down one "
        "rubric dimension. Propose ONE concrete, reusable prompt edit (a short phrase or instruction "
        "to ADD to the " + stage + " generation prompt whenever this attribute is present) that would "
        "plausibly raise that dimension WITHOUT harming others. If no wording change could fix it "
        "(a model/capability limit), say so instead. Reply with ONLY a JSON object, no prose."
    )
    user = (
        f"Attribute: {attr_name} = {attr_val}\n"
        f"Weak dimension: {w['dimension']} — {dim_desc} ({stage} stage)\n"
        f"Its learned score {w['estimate']} sits {w['deficit']} below the {dim_desc} average "
        f"({w['anchor']}) over {w['n']} ratings.\n"
        + (f"Brand context: {brand_hint}\n" if brand_hint else "")
        + 'Return either:\n'
        '{"scope_type":"attribute","cause":"<1 sentence>","suggested_edit":"<imperative prompt phrase>"}\n'
        'or {"decision":"MODEL_LIMIT_NO_EDIT","cause":"<1 sentence>"}'
    )
    return system, user


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def propose_edit(opus_call: Callable[[str, str], str], w: dict, brand_hint: str = "") -> Optional[dict]:
    """opus_call(system, user) -> raw text. Returns a candidate dict or None (no-edit / unparseable)."""
    data = _parse_json(opus_call(*build_messages(w, brand_hint)))
    if not data or data.get("decision") == "MODEL_LIMIT_NO_EDIT":
        return None
    edit = (data.get("suggested_edit") or "").strip()
    if not edit:
        return None
    return {
        "scope_type": data.get("scope_type", "attribute"),
        "scope_key": w["scope_key"],
        "dimension": w["dimension"],
        "context_key": w["context_key"],
        "cause": (data.get("cause") or "").strip()[:500],
        "suggested_edit": edit[:1000],
        "estimate": w["estimate"], "n": w["n"],
    }


# ── 3-5. storage / promotion / fold / validation (DB) ──────────────────────────────
def store_candidates(sb, tenant_id: str, candidates: list) -> int:
    rows = [{
        "tenant_id": tenant_id, "scope_type": c["scope_type"], "scope_key": c["scope_key"],
        "dimension": c["dimension"], "context_key": c["context_key"], "cause": c["cause"],
        "suggested_edit": c["suggested_edit"], "status": "candidate",
        "evidence_n": c["n"], "updated_at": _now_iso(),
    } for c in candidates]
    if rows:
        sb.table("tuning_suggestions").upsert(
            rows, on_conflict="tenant_id,scope_type,scope_key,dimension,context_key").execute()
    return len(rows)


def promote_to_testing(sb, tenant_id: str, suggestion_id: str, stats: dict) -> dict:
    """Capture the current estimate as the baseline, flip candidate->testing so it starts applying."""
    r = (sb.table("tuning_suggestions").select("*").eq("id", suggestion_id).limit(1).execute().data or [None])[0]
    if not r:
        return {"ok": False, "reason": "not found"}
    st = stats.get((r.get("context_key", "global"), r["scope_key"], r["dimension"]))
    base_est = float(st["estimate"]) if st and st.get("estimate") is not None else None
    base_n = (st["n"] if st else 0) or 0
    sb.table("tuning_suggestions").update({
        "status": "testing", "baseline_estimate": base_est, "baseline_n": base_n, "updated_at": _now_iso(),
    }).eq("id", suggestion_id).execute()
    return {"ok": True, "baseline_estimate": base_est, "baseline_n": base_n}


def get_active_edits(sb, tenant_id: str, attrs: dict, stage: Optional[str] = None,
                     statuses=("testing", "validated")) -> list:
    """GENERATION HOOK: edits to inject for a scenario with these composed_attributes.
    stage='image'|'video' restricts to that generator's dimensions; None = all."""
    attr_keys = {f"{k}={v}" for k, v in (attrs or {}).items() if v not in (None, "", [])}
    rows = (sb.table("tuning_suggestions").select("*").eq("tenant_id", tenant_id)
            .in_("status", list(statuses)).execute().data) or []
    out = []
    for r in rows:
        if r.get("scope_type") != "attribute" or r.get("scope_key") not in attr_keys:
            continue
        if stage and STAGE_OF_DIM.get(r.get("dimension"), "image") != stage:
            continue
        if r.get("suggested_edit"):
            out.append(r["suggested_edit"])
    return out


def ab_validate(sb, tenant_id: str, stats: dict) -> dict:
    """For each 'testing' suggestion with enough NEW ratings, compare estimate to baseline:
    delta >= AB_DELTA -> validated; else rejected. Returns counts."""
    rows = (sb.table("tuning_suggestions").select("*").eq("tenant_id", tenant_id)
            .eq("status", "testing").execute().data) or []
    res = {"validated": 0, "rejected": 0, "waiting": 0}
    for r in rows:
        st = stats.get((r.get("context_key", "global"), r["scope_key"], r["dimension"]))
        base = r.get("baseline_estimate")
        if not st or st.get("estimate") is None or base is None:
            res["waiting"] += 1; continue
        new_n = st["n"] or 0
        if (new_n - (r.get("baseline_n") or 0)) < MIN_NEW_EVIDENCE:
            res["waiting"] += 1; continue
        delta = float(st["estimate"]) - float(base)
        status = "validated" if delta >= AB_DELTA else "rejected"
        sb.table("tuning_suggestions").update({
            "status": status, "score_delta": round(delta, 4), "evidence_n": new_n, "updated_at": _now_iso(),
        }).eq("id", r["id"]).execute()
        res[status] += 1
    return res
