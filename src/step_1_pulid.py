"""
src/step_1_pulid.py — Stage 1 via the local stage1-service HTTP worker (port 8192).

Thin HTTP client. The heavy FLUX.1-dev + PuLID model now lives in the persistent
stage1-service (/workspace/stage1-service/app.py), NOT in this process — so the
orchestrator no longer holds ~28 GB of PuLID VRAM. Public surface is unchanged.

Public surface (matches run_supabase_resident.py + the orchestration flows):
  load_pipeline()                              — checks the service is up (no VRAM here)
  generate(pipeline, step_1_prompt, fal_pulid_params, out_path, scenario_id)
  unload_pipeline(pipeline)                    — no-op (service stays warm) unless
                                                 STAGE1_FREE_ON_UNLOAD=1

PERSONA REFERENCE (no hardcoding):
  Reads module-level PERSONA_IMAGE_PATH, which the runner overrides per account
  (run_supabase_resident.py:636  step_1_pulid.PERSONA_IMAGE_PATH = info["portrait_path"]).
  That per-account portrait path (a file on this pod) is sent to the service per
  request. The default below is a last-resort only and is normally overridden
  before any call.

Return-dict contract is unchanged (local_path, fal_url=None, seed, request_id,
elapsed_seconds, endpoint, cost_usd=0.0, fal_pulid_params_used) and the
{stem}_request.json audit is still written, so DB writes / HTML viewers / the
parity tooling need no changes.

ENV:
  STAGE1_SERVICE_URL     default http://127.0.0.1:8192
  STAGE1_TIMEOUT_S       default 1800
  STAGE1_FREE_ON_UNLOAD  "1" to free the service VRAM on unload_pipeline (default off)
"""

import base64
import json
import os
import time
import uuid
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]

# Normally overridden per-account by the runner BEFORE each account's scenarios.
PERSONA_IMAGE_PATH = REPO_ROOT / "assets" / "persona.jpg"

SERVICE_URL = os.environ.get("STAGE1_SERVICE_URL", "http://127.0.0.1:8192")
ENDPOINT_LABEL = "local/flux-pulid"
COST_PER_IMAGE_USD = 0.0
REQUEST_TIMEOUT_S = int(os.environ.get("STAGE1_TIMEOUT_S", "1800"))
FREE_ON_UNLOAD = os.environ.get("STAGE1_FREE_ON_UNLOAD", "0") == "1"


def load_pipeline():
    """Verify the stage1-service is reachable. Loads NO model in this process."""
    try:
        r = requests.get(f"{SERVICE_URL}/health", timeout=30)
        r.raise_for_status()
        info = r.json()
    except Exception as e:
        raise RuntimeError(
            f"stage1-service not reachable at {SERVICE_URL} ({e}).\n"
            f"  Start it in a separate terminal:\n"
            f"    cd /workspace/stage1-service && "
            f"ALLUVI_REPO=/workspace/alluvi-pipeline "
            f"uvicorn app:app --host 0.0.0.0 --port 8192"
        )
    if not info.get("cuda", False):
        print("[step_1_pulid] WARNING: service reports cuda=false")
    print(f"[step_1_pulid] stage1-service OK at {SERVICE_URL} "
          f"(pulid_loaded={info.get('pulid_loaded')})")
    return {"service_url": SERVICE_URL}


def generate(pipeline, step_1_prompt, fal_pulid_params, out_path, scenario_id="unknown"):
    out_path = Path(out_path)
    persona_path = Path(PERSONA_IMAGE_PATH)
    if not persona_path.exists():
        raise FileNotFoundError(
            f"persona reference not found at {persona_path} — the runner should set "
            f"step_1_pulid.PERSONA_IMAGE_PATH to the per-account portrait before calling."
        )

    url = pipeline.get("service_url", SERVICE_URL) if isinstance(pipeline, dict) else SERVICE_URL
    body = {
        "step_1_prompt": step_1_prompt,
        "persona_image_path": str(persona_path),   # same-pod path (no base64 needed)
        "pulid_params": fal_pulid_params or {},
        "scenario_id": scenario_id,
    }

    print(f"[step_1_pulid] [{scenario_id}] -> {url}/stage1/generate "
          f"(persona={persona_path.name})")
    t0 = time.time()
    r = requests.post(f"{url}/stage1/generate", json=body, timeout=REQUEST_TIMEOUT_S)
    if r.status_code != 200:
        raise RuntimeError(f"stage1-service error {r.status_code}: {r.text[:500]}")
    data = r.json()
    elapsed = time.time() - t0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(data["image_b64"]))

    used = data.get("params_used", {})
    isize = used.get("image_size", {})

    # audit next to the image — same {stem}_request.json convention as before
    audit = {
        "endpoint": ENDPOINT_LABEL,
        "service_url": url,
        "arguments": {
            "prompt": step_1_prompt,
            "persona_image_path": str(persona_path),
            "width": isize.get("width"),
            "height": isize.get("height"),
            "num_inference_steps": used.get("num_inference_steps"),
            "guidance_scale": used.get("guidance_scale"),
            "id_weight": used.get("id_weight"),
            "true_cfg": used.get("true_cfg"),
            "negative_prompt": (fal_pulid_params or {}).get("negative_prompt"),
            "max_sequence_length": used.get("max_sequence_length"),
            "seed": data.get("seed"),
        },
    }
    (out_path.parent / f"{out_path.stem}_request.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[step_1_pulid] [{scenario_id}]   done in {elapsed:.1f}s "
          f"(service {data.get('elapsed_seconds', 0):.1f}s), seed={data.get('seed')}")

    return {
        "local_path": str(out_path),
        "fal_url": None,
        "seed": data.get("seed"),
        "request_id": data.get("request_id", f"local-{uuid.uuid4().hex[:8]}"),
        "elapsed_seconds": elapsed,
        "endpoint": ENDPOINT_LABEL,
        "cost_usd": COST_PER_IMAGE_USD,
        "fal_pulid_params_used": used,
    }


def unload_pipeline(pipeline=None):
    """No-op by default — the service keeps PuLID warm for the next scenario/run.
    Set STAGE1_FREE_ON_UNLOAD=1 to release the service's VRAM here instead."""
    if not FREE_ON_UNLOAD:
        return
    try:
        requests.post(f"{SERVICE_URL}/free", json={"target": "stage1"}, timeout=120)
        print("[step_1_pulid] requested stage1-service /free")
    except Exception as e:
        print(f"[step_1_pulid] unload /free failed (non-fatal): {e}")