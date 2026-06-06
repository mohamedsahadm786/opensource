"""
supabase_pipeline/phase_a_persona.py — Phase A portrait generation via the
stage1-service HTTP worker (port 8192), endpoint /phasea/portrait.

Thin HTTP client. The FLUX.1-dev txt2img model now lives in the persistent
stage1-service (/workspace/stage1-service/app.py), NOT in this process — so the
orchestrator no longer loads ~24 GB of FLUX to mint a portrait.

PUBLIC SURFACE PRESERVED (referenced by run_supabase_resident.py):
  FLUX_DEV_DIR                                   — still defined (preflight checks it)
  load_pipeline()                                — health-checks the service, no VRAM here
  generate(pipeline, portrait_prompt, out_path, *, width, height,
           num_inference_steps, guidance_scale, max_sequence_length,
           seed, account_id)  -> dict            — same signature + return-dict
  unload_pipeline(pipeline)                      — frees the service's FLUX (target=phasea)

Return-dict and the {stem}_request.json audit match the in-process version, so
ensure_persona / _audit_image_gen / DB writes need no changes.

ENV:
  STAGE1_SERVICE_URL     default http://127.0.0.1:8192   (same service as Stage 1)
  FLUX_DEV_MODEL_PATH    default /workspace/models/FLUX.1-dev
  STAGE1_TIMEOUT_S       default 1800
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from pathlib import Path

import requests

# Kept for compatibility — run_supabase_resident._preflight() checks this path.
FLUX_DEV_DIR = os.environ.get("FLUX_DEV_MODEL_PATH", "/workspace/models/FLUX.1-dev")

SERVICE_URL = os.environ.get("STAGE1_SERVICE_URL", "http://127.0.0.1:8192")
ENDPOINT_LABEL = "local/flux-dev-txt2img"
COST_PER_IMAGE_USD = 0.0
REQUEST_TIMEOUT_S = int(os.environ.get("STAGE1_TIMEOUT_S", "1800"))

# Defaults identical to the in-process version (so portraits match).
DEFAULT_WIDTH = 768
DEFAULT_HEIGHT = 1024
DEFAULT_NUM_STEPS = 30
DEFAULT_GUIDANCE = 3.5
DEFAULT_MAX_SEQUENCE_LENGTH = 512


def load_pipeline():
    """Verify the stage1-service is reachable. Loads NO model in this process."""
    try:
        r = requests.get(f"{SERVICE_URL}/health", timeout=30)
        r.raise_for_status()
        info = r.json()
    except Exception as e:
        raise RuntimeError(
            f"stage1-service not reachable at {SERVICE_URL} ({e}).\n"
            f"  Start it: cd /workspace/stage1-service && "
            f"ALLUVI_REPO=/workspace/alluvi-pipeline "
            f"uvicorn app:app --host 0.0.0.0 --port 8192"
        )
    print(f"[phase_a_persona] stage1-service OK at {SERVICE_URL} "
          f"(flux_loaded={info.get('flux_loaded')})")
    return {"service_url": SERVICE_URL}


def generate(
    pipeline,
    portrait_prompt: str,
    out_path,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    num_inference_steps: int = DEFAULT_NUM_STEPS,
    guidance_scale: float = DEFAULT_GUIDANCE,
    max_sequence_length: int = DEFAULT_MAX_SEQUENCE_LENGTH,
    seed: int | None = None,
    account_id: str = "unknown",
) -> dict:
    """Render one reference portrait via the service. Same contract as before."""
    out_path = Path(out_path)
    url = pipeline.get("service_url", SERVICE_URL) if isinstance(pipeline, dict) else SERVICE_URL

    body = {
        "portrait_prompt": portrait_prompt,
        "width": width,
        "height": height,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "max_sequence_length": max_sequence_length,
        "seed": seed,
        "account_id": account_id,
    }

    print(f"[phase_a_persona] [{account_id}] -> {url}/phasea/portrait "
          f"(size={width}x{height}, steps={num_inference_steps})")
    t0 = time.time()
    r = requests.post(f"{url}/phasea/portrait", json=body, timeout=REQUEST_TIMEOUT_S)
    if r.status_code != 200:
        raise RuntimeError(f"stage1-service /phasea error {r.status_code}: {r.text[:500]}")
    data = r.json()
    elapsed = time.time() - t0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(data["image_b64"]))

    used_seed = data.get("seed", seed)

    # audit sidecar — same convention/keys as the in-process version
    audit = {
        "endpoint": ENDPOINT_LABEL,
        "service_url": url,
        "arguments": {
            "prompt": portrait_prompt,
            "width": width,
            "height": height,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "max_sequence_length": max_sequence_length,
            "seed": used_seed,
            "negative_prompt": None,      # guidance-distilled — not used
            "model_path": FLUX_DEV_DIR,
        },
    }
    (out_path.parent / f"{out_path.stem}_request.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[phase_a_persona] [{account_id}]   portrait in {elapsed:.1f}s "
          f"(service {data.get('elapsed_seconds', 0):.1f}s) -> {out_path}")

    return {
        "local_path": str(out_path),
        "fal_url": None,
        "seed": used_seed,
        "request_id": data.get("request_id", f"local-{uuid.uuid4().hex[:8]}"),
        "elapsed_seconds": elapsed,
        "endpoint": ENDPOINT_LABEL,
        "cost_usd": COST_PER_IMAGE_USD,
        "params_used": {
            "width": width, "height": height,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "max_sequence_length": max_sequence_length,
            "seed": used_seed,
        },
    }


def unload_pipeline(pipeline=None) -> None:
    """Free the service's FLUX VRAM after Phase A0 (matches the in-process intent
    of unloading FLUX once portraits are done). Non-fatal on error."""
    url = pipeline.get("service_url", SERVICE_URL) if isinstance(pipeline, dict) else SERVICE_URL
    try:
        requests.post(f"{url}/free", json={"target": "phasea"}, timeout=120)
        print("[phase_a_persona] requested stage1-service /free (phasea FLUX)")
    except Exception as e:
        print(f"[phase_a_persona] unload /free failed (non-fatal): {e}")