"""
orchestrator/engine.py — learning engine core (L1 scoring + L2 statistics).

Full-recompute design (the brief's recommendation): because asset_ratings is upsert-by-output_id
(a reviewer can re-rate), an incremental tally would double-count. So each run rebuilds
attribute_stats (per-tenant) and attribute_priors (global) from scratch:

  read all ratings -> explode each into (tenant, context, attribute, dimension) facts
  -> aggregate sufficient statistics (n, passes, sum_val, sum_sq)
  -> compute shrunk estimates (Beta-Bernoulli for gates, Bayesian-average for scores)
  -> upsert-overwrite the two stats tables.

Pure helpers (normalize, score_asset, explode, gate_estimate, score_estimate, add_estimates)
are unit-testable without DB. recompute(sb) does the I/O.
"""
from __future__ import annotations

from typing import Optional

# commercial score dimensions (everything else that's a score rolls into "craft")
COMMERCIAL_DIMS = {"img_ad_worthiness", "vid_hook_strength"}

# L2 priors (brief §13)
GATE_PRIOR_STRENGTH = 10     # s
SCORE_PSEUDO_COUNT = 15      # k
DEFAULT_C = 3.0              # global score-mean fallback (1..5 scale)


# ── L1: score a single asset ────────────────────────────────────────────────────
def normalize(s: float) -> float:        # 1..5 -> 0..1
    return (s - 1) / 4.0


def score_asset(rubric: dict) -> dict:
    gates = (rubric or {}).get("gates", {}) or {}
    scores = (rubric or {}).get("scores", {}) or {}
    usable = all(g.get("result") == "Pass" for g in gates.values()) if gates else True
    answered = [normalize(v) for v in scores.values() if isinstance(v, (int, float))]
    craft = [normalize(v) for d, v in scores.items()
             if isinstance(v, (int, float)) and d not in COMMERCIAL_DIMS]
    comm = [normalize(v) for d, v in scores.items()
            if isinstance(v, (int, float)) and d in COMMERCIAL_DIMS]
    return {
        "usable": usable,
        "failed_gates": [d for d, g in gates.items() if g.get("result") == "Fail"],
        "mean_score": sum(answered) / len(answered) if answered else None,
        "craft": sum(craft) / len(craft) if craft else None,
        "commercial": sum(comm) / len(comm) if comm else None,
    }


# ── the explode: one rating -> many (attribute, dimension) facts at two contexts ──
def explode(rating: dict, attributes: dict, country: Optional[str]):
    """Yield (tenant_id, context_key, attribute_key, dimension, kind, pass01, val) tuples."""
    ctxs = ["global"] + ([f"country={country}"] if country else [])
    facts = []
    for col in ("image", "video"):
        rub = rating.get(col) or {}
        for dim, g in (rub.get("gates") or {}).items():
            res = (g or {}).get("result")
            if res in ("Pass", "Fail"):
                facts.append(("gate", dim, 1 if res == "Pass" else 0, 0))
        for dim, v in (rub.get("scores") or {}).items():
            if isinstance(v, (int, float)) and 1 <= v <= 5:
                facts.append(("score", dim, 0, v))
    for attr_key, attr_val in (attributes or {}).items():
        if attr_val in (None, "", []):
            continue
        a = f"{attr_key}={attr_val}"
        for ctx in ctxs:
            for kind, dim, pas, val in facts:
                yield (rating["tenant_id"], ctx, a, dim, kind, pas, val)


def _bump(store: dict, key, kind: str, pas: int, val: float):
    s = store.get(key)
    if s is None:
        s = {"kind": kind, "n": 0, "passes": 0, "sum_val": 0.0, "sum_sq": 0.0}
        store[key] = s
    s["n"] += 1
    if kind == "gate":
        s["passes"] += pas
    else:
        s["sum_val"] += val
        s["sum_sq"] += val * val


# ── L2: shrunk estimates ─────────────────────────────────────────────────────────
def gate_estimate(passes: int, n: int, p_bar: float, s: int = GATE_PRIOR_STRENGTH) -> float:
    return (s * p_bar + passes) / (s + n)


def score_estimate(sum_val: float, n: int, C: float, k: int = SCORE_PSEUDO_COUNT) -> float:
    return (sum_val + k * C) / (n + k)


def _global_anchors(rows: dict, dim_of):
    """Per-dimension global pass-rate (gates) and mean (scores) over the rebuilt totals."""
    agg = {}
    for key, st in rows.items():
        dim = dim_of(key)
        a = agg.setdefault(dim, {"kind": st["kind"], "n": 0, "passes": 0, "sum_val": 0.0})
        a["n"] += st["n"]; a["passes"] += st["passes"]; a["sum_val"] += st["sum_val"]
    anchors = {}
    for dim, a in agg.items():
        if a["kind"] == "gate":
            anchors[dim] = a["passes"] / a["n"] if a["n"] else 0.8
        else:
            anchors[dim] = a["sum_val"] / a["n"] if a["n"] else DEFAULT_C
    return anchors


def add_estimates(rows: dict, dim_of) -> None:
    """Fill st['estimate'] for every row, using per-dimension global anchors."""
    anchors = _global_anchors(rows, dim_of)
    for key, st in rows.items():
        dim = dim_of(key)
        if st["kind"] == "gate":
            st["estimate"] = round(gate_estimate(st["passes"], st["n"], anchors.get(dim, 0.8)), 6)
        else:
            st["estimate"] = round(score_estimate(st["sum_val"], st["n"], anchors.get(dim, DEFAULT_C)), 6)


# ── full recompute (I/O) ──────────────────────────────────────────────────────────
def _fetch_all(sb, table: str, select: str = "*", page: int = 1000, flt=None) -> list:
    out, start = [], 0
    while True:
        q = sb.table(table).select(select)
        if flt:
            q = flt(q)
        rows = q.range(start, start + page - 1).execute().data or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def recompute(sb, page: int = 1000) -> dict:
    ratings = _fetch_all(sb, "asset_ratings", "*", page)
    expected = sb.table("asset_ratings").select("id", count="exact").limit(1).execute().count
    if expected is not None and len(ratings) != expected:
        raise RuntimeError(f"read {len(ratings)} ratings but count={expected} — aborting (partial read)")

    outs = {o["id"]: o for o in _fetch_all(sb, "outputs", "id,persona_id,scenario_id,tenant_id", page)}
    personas = {p["id"]: p.get("tiktok_account_id") for p in _fetch_all(sb, "personas", "id,tiktok_account_id", page)}
    accounts = {a["id"]: a.get("country") for a in _fetch_all(sb, "tiktok_accounts", "id,country", page)}
    scen_attr = {s["id"]: (s.get("composed_attributes") or {}) for s in _fetch_all(sb, "scenarios", "id,composed_attributes", page)}

    stat, prior = {}, {}
    for r in ratings:
        out = outs.get(r["output_id"]) or {}
        attrs = r.get("composed_attributes") or scen_attr.get(out.get("scenario_id")) or {}
        country = accounts.get(personas.get(out.get("persona_id")))
        for t, ctx, a, dim, kind, pas, val in explode(r, attrs, country):
            _bump(stat, (t, ctx, a, dim), kind, pas, val)
            _bump(prior, (ctx, a, dim), kind, pas, val)

    add_estimates(stat, lambda k: k[3])
    add_estimates(prior, lambda k: k[2])

    stat_rows = [{"tenant_id": k[0], "context_key": k[1], "attribute_key": k[2], "dimension": k[3],
                  "kind": v["kind"], "n": v["n"], "passes": v["passes"], "sum_val": v["sum_val"],
                  "sum_sq": v["sum_sq"], "estimate": v["estimate"]} for k, v in stat.items()]
    prior_rows = [{"context_key": k[0], "attribute_key": k[1], "dimension": k[2],
                   "kind": v["kind"], "n": v["n"], "passes": v["passes"], "sum_val": v["sum_val"],
                   "sum_sq": v["sum_sq"], "estimate": v["estimate"]} for k, v in prior.items()]
    for i in range(0, len(stat_rows), 500):
        sb.table("attribute_stats").upsert(stat_rows[i:i+500],
            on_conflict="tenant_id,context_key,attribute_key,dimension").execute()
    for i in range(0, len(prior_rows), 500):
        sb.table("attribute_priors").upsert(prior_rows[i:i+500],
            on_conflict="context_key,attribute_key,dimension").execute()
    return {"ratings": len(ratings), "stat_rows": len(stat_rows), "prior_rows": len(prior_rows)}


# ════════════════════════════════════════════════════════════════════════════════
# L8: exploration lifecycle gate  +  L3: Thompson selection
# ════════════════════════════════════════════════════════════════════════════════
import random as _random
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── lifecycle: flip exploration -> active once the curated set is worked through ──
def exploration_progress(sb, tenant_id: str) -> dict:
    rows = (sb.table("v_tenant_exploration_progress").select("*")
            .eq("tenant_id", tenant_id).limit(1).execute().data) or []
    return rows[0] if rows else {}


def maybe_flip_engine(sb, tenant_id: str) -> dict:
    """Idempotent: flip exploration->active (engine ON) once pct_complete >= min_coverage_pct.
    Never flips back. Enterprise-robust: creates a missing state row, and the progress view
    counts QC-skipped scenarios so a stubborn scenario can't block the gate forever."""
    st = (sb.table("tenant_learning_state").select("*")
          .eq("tenant_id", tenant_id).limit(1).execute().data) or []
    state = st[0] if st else None
    if state is None:
        sb.table("tenant_learning_state").insert({"tenant_id": tenant_id}).execute()
        state = {"phase": "exploration", "engine_enabled": False, "min_coverage_pct": 100}
    if state.get("phase") == "active" and state.get("engine_enabled"):
        return {"phase": "active", "engine_enabled": True, "flipped": False}

    prog = exploration_progress(sb, tenant_id)
    pct = prog.get("pct_complete") or 0
    minc = state.get("min_coverage_pct") or 100
    if pct >= minc:
        sb.table("tenant_learning_state").update({
            "phase": "active", "engine_enabled": True,
            "engine_enabled_at": _now_iso(), "updated_at": _now_iso(),
        }).eq("tenant_id", tenant_id).execute()
        return {"phase": "active", "engine_enabled": True, "flipped": True,
                "pct_complete": pct, "resolved": prog.get("resolved_curated"),
                "active_curated": prog.get("active_curated")}
    return {"phase": "exploration", "engine_enabled": False, "flipped": False,
            "pct_complete": pct, "min_coverage_pct": minc,
            "resolved": prog.get("resolved_curated"), "active_curated": prog.get("active_curated")}


def engine_state(sb, tenant_id: str) -> dict:
    st = (sb.table("tenant_learning_state").select("phase,engine_enabled,min_coverage_pct")
          .eq("tenant_id", tenant_id).limit(1).execute().data) or []
    return st[0] if st else {"phase": "exploration", "engine_enabled": False, "min_coverage_pct": 100}


# ── L3: Thompson sampling over attribute beliefs ─────────────────────────────────
def load_attribute_stats(sb, tenant_id: str, contexts: list) -> dict:
    rows = (sb.table("attribute_stats").select("*")
            .eq("tenant_id", tenant_id).in_("context_key", contexts).execute().data) or []
    return {(r["context_key"], r["attribute_key"], r["dimension"]): r for r in rows}


def sample_scenario_utility(attrs: dict, stats: dict, context: str,
                            w_craft: float = 0.6, w_comm: float = 0.4) -> float:
    """One Thompson draw of a candidate scenario's utility: P(all gates pass) * weighted score.
    Gates -> Beta posterior (reconstructed from the stored shrunk estimate + concentration n+s);
    scores -> Normal(estimate, sd/sqrt(n+k)). Untested ingredients get wide beliefs -> explored."""
    attr_keys = {f"{k}={v}" for k, v in (attrs or {}).items() if v not in (None, "", [])}
    gate_probs, craft, comm = [], [], []
    for (ctx, a, dim), st in stats.items():
        if ctx != context or a not in attr_keys:
            continue
        if st["kind"] == "gate":
            n = st["n"] or 0
            est = float(st["estimate"]) if st.get("estimate") is not None else 0.8
            conc = n + GATE_PRIOR_STRENGTH
            a0 = max(est * conc, 1e-3); b0 = max((1 - est) * conc, 1e-3)
            gate_probs.append(_random.betavariate(a0, b0))
        else:
            n = st["n"] or 0
            est = float(st["estimate"]) if st.get("estimate") is not None else DEFAULT_C
            mean = (st["sum_val"] / n) if n else est
            var = max((st["sum_sq"] / n) - mean * mean, 1e-6) if n else 1.0
            sd = (var ** 0.5) / ((n + SCORE_PSEUDO_COUNT) ** 0.5)
            draw = _random.gauss(est, sd)
            (comm if dim in COMMERCIAL_DIMS else craft).append(draw)
    gate_pass = 1.0
    for p in gate_probs:
        gate_pass *= p
    if not gate_probs:
        gate_pass = 0.5
    craft_e = sum(craft) / len(craft) if craft else 3.0
    comm_e = sum(comm) / len(comm) if comm else 3.0
    return gate_pass * (w_craft * craft_e + w_comm * comm_e)


def plan_jobs(candidates: list, stats: dict, context: str, k: int,
              explore: float = 0.10, w_craft: float = 0.6, w_comm: float = 0.4) -> list:
    """Pick k candidates: Thompson-rank, with a diversity nudge (no repeat scene_category in the
    batch where avoidable) and a ~10% random exploration floor so fresh ingredients always enter.
    Each candidate is {'row':..., 'attrs': composed_attributes}."""
    if k <= 0 or not candidates:
        return []
    ranked = sorted(candidates,
                    key=lambda c: sample_scenario_utility(c["attrs"], stats, context, w_craft, w_comm),
                    reverse=True)
    n_explore = max(1, int(k * explore)) if len(candidates) > k else 0
    chosen, seen_scenes = [], set()
    for c in ranked:
        if len(chosen) >= (k - n_explore):
            break
        sc = (c["attrs"] or {}).get("scene_category")
        if sc in seen_scenes and len([x for x in ranked if (x["attrs"] or {}).get("scene_category") != sc]) > 0:
            continue  # nudge variety while candidates of other scenes remain
        chosen.append(c); seen_scenes.add(sc)
    # backfill if the diversity nudge left us short
    for c in ranked:
        if len(chosen) >= (k - n_explore):
            break
        if c not in chosen:
            chosen.append(c)
    pool = [c for c in candidates if c not in chosen]
    if n_explore and pool:
        chosen += _random.sample(pool, min(n_explore, len(pool)))
    return chosen[:k]


def select_scenarios(sb, tenant_id: str, persona_id: str, country, n: int,
                     explore: float = 0.10) -> list:
    """ACTIVE-phase scenario picker: candidates = active curated scenarios not yet done for this
    persona; rank via Thompson sampling over the tenant's attribute beliefs. Returns scenario rows
    (id, scenario_key, spec, composed_attributes). Uses the country context if it has data, else global."""
    contexts = ["global"] + ([f"country={country}"] if country else [])
    stats = load_attribute_stats(sb, tenant_id, contexts)
    ctx_country = f"country={country}" if country else None
    context = ctx_country if (ctx_country and any(k[0] == ctx_country for k in stats)) else "global"

    done = {r["scenario_id"] for r in
            (sb.table("outputs").select("scenario_id").eq("persona_id", persona_id).execute().data or [])
            if r.get("scenario_id")}
    scen = (sb.table("scenarios").select("id,scenario_key,spec,composed_attributes")
            .eq("is_active", True).neq("source", "generated").execute().data) or []
    cands = [{"row": s, "attrs": s.get("composed_attributes") or {}} for s in scen if s["id"] not in done]
    if not cands:
        return []
    return [c["row"] for c in plan_jobs(cands, stats, context, n, explore)]