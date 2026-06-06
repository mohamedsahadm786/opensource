"""
stage1-service/app.py — isolated FastAPI GPU worker for Phase A (portrait) and
Stage 1 (PuLID persona scene). REUSES the repo's proven code:
  - Stage 1  -> src.pulid_inference.pulid_pipeline.PulidWrapper  (guozinan/PuLID)
  - Phase A  -> diffusers FluxPipeline on /workspace/models/FLUX.1-dev

Design principles (per requirements):
  - STATELESS GPU worker. No DB, no persona.yaml, NO hardcoded persona.jpg.
    The caller passes the persona image PER REQUEST (base64 or a server-side path).
  - Identical output to the current in-process stages: same PulidWrapper call,
    same constants (ID_WEIGHT cap + ceiling, default negative prompt, max_seq),
    same FluxPipeline call.
  - Explicit VRAM control: /load and /free per model so the orchestrator decides
    what stays resident (load Stage-1 once, keep it across all scenarios; load
    Phase-A only at account setup then free it).

Nothing in /workspace/alluvi-pipeline is modified. This process just imports from
it and runs on its own port.

RUN (in the repo's venv, which already has torch/diffusers/PuLID):
  source /workspace/ai-toolkit/venv/bin/activate
  pip install fastapi "uvicorn[standard]" python-multipart   # one-time
  cd /workspace/stage1-service
  ALLUVI_REPO=/workspace/alluvi-pipeline uvicorn app:app --host 0.0.0.0 --port 8192
Then expose port 8192 in RunPod.
"""

from __future__ import annotations

import base64
import io
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ── make the repo importable so we reuse the EXACT validated code ───────────
ALLUVI_REPO = os.environ.get("ALLUVI_REPO", "/workspace/alluvi-pipeline")
if ALLUVI_REPO not in sys.path:
    sys.path.insert(0, ALLUVI_REPO)

# FLUX.1-dev diffusers folder (same default as phase_a_persona.py)
FLUX_DEV_DIR = os.environ.get("FLUX_DEV_MODEL_PATH", "/workspace/models/FLUX.1-dev")

# ── constants copied VERBATIM from src/step_1_pulid.py so Stage-1 output is
#    bit-for-bit equivalent to the current in-process behavior ──────────────
ID_WEIGHT_HARD_CAP = 1.0
PERSONA_ID_WEIGHT_CEILING = 0.6
DEFAULT_MAX_SEQUENCE_LENGTH = 512
DEFAULT_NEGATIVE_PROMPT = (
    "plastic skin, airbrushed skin, waxy skin, porcelain skin, "
    "doll-like features, smooth artificial skin, extra limbs, "
    "extra arms, extra legs, three legs, three arms, extra hands, "
    "extra fingers, six fingers, seven fingers, fused fingers, "
    "missing fingers, deformed hands, mutated hands, distorted face, "
    "asymmetric eyes, dead eyes, uncanny valley, perfect symmetry, "
    "model pose, stiff pose, magazine retouching, AI-generated look, "
    "3D render, CGI, illustration, cartoon, anime, painting, lowres, "
    "blurry, jpeg artifacts, watermark, text, signature, logo"
)

app = FastAPI(title="alluvi-stage1-service", version="0.1.0")

# Lazily-loaded heavy pipelines (one of each at most).
_pulid = None          # PulidWrapper (Stage 1)
_flux = None           # diffusers FluxPipeline (Phase A)


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────
def _img_to_b64(image) -> str:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=95)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _resolve_persona_image(persona_image_b64: Optional[str],
                           persona_image_path: Optional[str]) -> str:
    """Return a server-side file path to the reference face for this request.

    Accepts either base64 bytes (preferred for remote callers) OR an existing
    path on the pod. NEVER falls back to a hardcoded default — a missing
    persona image is a hard error, by design.
    """
    if persona_image_b64:
        from PIL import Image
        raw = base64.b64decode(persona_image_b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        tmp = Path("/tmp") / f"persona_{uuid.uuid4().hex[:10]}.jpg"
        img.save(tmp, "JPEG", quality=95)
        return str(tmp)
    if persona_image_path:
        p = Path(persona_image_path)
        if not p.exists():
            raise HTTPException(400, f"persona_image_path does not exist: {p}")
        return str(p)
    raise HTTPException(400, "persona image required: send persona_image_b64 or persona_image_path")


def _parse_image_size(value, fallback=(768, 1344)) -> tuple[int, int]:
    if isinstance(value, dict):
        w, h = value.get("width"), value.get("height")
        if w and h:
            return int(w), int(h)
    presets = {
        "square_hd": (1024, 1024), "square": (512, 512),
        "portrait_4_3": (768, 1024), "portrait_16_9": (576, 1024),
        "landscape_4_3": (1024, 768), "landscape_16_9": (1024, 576),
    }
    if isinstance(value, str) and value in presets:
        return presets[value]
    return fallback


def _load_pulid():
    global _pulid
    if _pulid is None:
        from src.pulid_inference.pulid_pipeline import PulidWrapper
        print("[svc] loading PuLID (FLUX.1-dev + PuLID + InsightFace) ...")
        t0 = time.time()
        _pulid = PulidWrapper(model_name="flux-dev", version="v0.9.1")
        print(f"[svc]   PuLID ready in {time.time() - t0:.1f}s")
    return _pulid


def _load_flux():
    global _flux
    if _flux is None:
        import torch
        from diffusers import FluxPipeline
        print(f"[svc] loading FLUX.1-dev txt2img from {FLUX_DEV_DIR} ...")
        t0 = time.time()
        pipe = FluxPipeline.from_pretrained(FLUX_DEV_DIR, torch_dtype=torch.bfloat16)
        _flux = pipe.to("cuda")
        print(f"[svc]   FLUX txt2img ready in {time.time() - t0:.1f}s")
    return _flux


def _free(target: str) -> dict:
    """Unload a pipeline and reclaim VRAM. target in {phasea, stage1, all}."""
    global _pulid, _flux
    import gc
    freed = []
    if target in ("stage1", "all") and _pulid is not None:
        try:
            if hasattr(_pulid, "to"):
                _pulid.to("cpu")
        except Exception as e:
            print(f"[svc] pulid .to(cpu): {e}")
        _pulid = None
        freed.append("stage1")
    if target in ("phasea", "all") and _flux is not None:
        try:
            _flux.to("cpu")
        except Exception as e:
            print(f"[svc] flux .to(cpu): {e}")
        _flux = None
        freed.append("phasea")
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return {"freed": freed}


# ──────────────────────────────────────────────────────────────────────────
# request models
# ──────────────────────────────────────────────────────────────────────────
class PhaseARequest(BaseModel):
    portrait_prompt: str
    width: int = 768
    height: int = 1024
    num_inference_steps: int = 30
    guidance_scale: float = 3.5
    max_sequence_length: int = 512
    seed: Optional[int] = None
    account_id: str = "unknown"


class Stage1Request(BaseModel):
    step_1_prompt: str
    # the params envelope (accepts the existing "fal_pulid_params" shape)
    pulid_params: dict = {}
    persona_image_b64: Optional[str] = None
    persona_image_path: Optional[str] = None
    scenario_id: str = "unknown"


class FreeRequest(BaseModel):
    target: str = "all"   # phasea | stage1 | all


# ──────────────────────────────────────────────────────────────────────────
# endpoints
# ──────────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    import torch
    return {
        "ok": True,
        "pulid_loaded": _pulid is not None,
        "flux_loaded": _flux is not None,
        "cuda": torch.cuda.is_available(),
        "repo": ALLUVI_REPO,
        "flux_dir": FLUX_DEV_DIR,
    }


@app.post("/free")
def free(req: FreeRequest):
    return _free(req.target)


@app.post("/phasea/portrait")
def phasea_portrait(req: PhaseARequest):
    import torch
    pipe = _load_flux()
    seed = req.seed if req.seed is not None else (uuid.uuid4().int % (2 ** 31))
    seed = int(seed)
    gen = torch.Generator("cuda").manual_seed(seed)

    t0 = time.time()
    image = pipe(
        prompt=req.portrait_prompt,
        width=req.width,
        height=req.height,
        num_inference_steps=req.num_inference_steps,
        guidance_scale=req.guidance_scale,
        max_sequence_length=req.max_sequence_length,
        generator=gen,
    ).images[0]
    elapsed = time.time() - t0

    return {
        "image_b64": _img_to_b64(image),
        "seed": seed,
        "elapsed_seconds": elapsed,
        "endpoint": "local/flux-dev-txt2img",
        "request_id": f"local-{uuid.uuid4().hex[:8]}",
    }


@app.post("/stage1/generate")
def stage1_generate(req: Stage1Request):
    p = req.pulid_params or {}
    persona_path = _resolve_persona_image(req.persona_image_b64, req.persona_image_path)

    width, height = _parse_image_size(p.get("image_size"))
    num_steps = int(p.get("num_inference_steps", 30))
    guidance = float(p.get("guidance_scale", 3.5))
    true_cfg = float(p.get("true_cfg", 1.5))

    try:
        max_seq = int(p.get("max_sequence_length", DEFAULT_MAX_SEQUENCE_LENGTH))
    except (TypeError, ValueError):
        max_seq = DEFAULT_MAX_SEQUENCE_LENGTH

    negative_prompt = p.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT

    seed = p.get("seed")

    # id_weight: hard cap 1.0 then realism ceiling 0.6 — identical to step_1_pulid
    try:
        id_weight = float(p.get("id_weight", 1.0))
        if id_weight > ID_WEIGHT_HARD_CAP:
            id_weight = ID_WEIGHT_HARD_CAP
    except (TypeError, ValueError):
        id_weight = 1.0
    id_weight = min(id_weight, PERSONA_ID_WEIGHT_CEILING)

    pipe = _load_pulid()
    t0 = time.time()
    image, used_seed = pipe.generate(
        prompt=req.step_1_prompt,
        persona_image_path=persona_path,
        width=width,
        height=height,
        num_inference_steps=num_steps,
        guidance_scale=guidance,
        id_weight=id_weight,
        true_cfg=true_cfg,
        negative_prompt=negative_prompt,
        max_sequence_length=max_seq,
        seed=seed,
    )
    elapsed = time.time() - t0

    return {
        "image_b64": _img_to_b64(image),
        "seed": used_seed,
        "elapsed_seconds": elapsed,
        "endpoint": "local/flux-pulid",
        "request_id": f"local-{uuid.uuid4().hex[:8]}",
        "params_used": {
            "image_size": {"width": width, "height": height},
            "num_inference_steps": num_steps,
            "guidance_scale": guidance,
            "true_cfg": true_cfg,
            "id_weight": id_weight,
            "max_sequence_length": max_seq,
            "seed": used_seed,
        },
    }
