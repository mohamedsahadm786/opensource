"""
src/step_3_realism.py - Stage 3 photoreal realism via ComfyUI (RealVisXL +
skin LoRA + 1x skin detailer + low-denoise img2img + auto box-protect mask),
served by the isolated comfyui-realism server on port 8190.

DROP-IN REPLACEMENT for the previous local FLUX.1-Kontext-dev version. Same
public surface, same return-dict contract, same audit JSON, same out_path
write -> master.py and every downstream consumer (video pipeline) are unchanged.

WHY THIS REPLACED KONTEXT:
  Kontext gave only a weak realism lift. This stage runs a masked low-denoise
  SDXL pass that adds real skin texture while compositing the ORIGINAL product
  box back over the result (auto-segmented via SAM2/GroundingDINO), so the box
  text/graphics stay pixel-identical. Validated end-to-end over HTTP.

ARCHITECTURE (mirrors src/step_2_qwen_comfyui.py):
  load_pipeline()   -> ensure the 8190 ComfyUI server is up (start if needed)
  generate(...)     -> POST workflow, poll /history, fetch result, write out_path
  unload_pipeline() -> no-op (server stays resident)

Realism params are the validated values, overridable via env vars. The mask
prompt is read from assets/product.yaml (per-product) with safe fallbacks.

Return-dict contract (unchanged): local_path, fal_url(None), seed, request_id,
elapsed_seconds, endpoint, cost_usd(0.0), prompt_used, safety_tolerance(None),
model_paradigm.
"""

import io
import json
import os
import shutil
import socket
import subprocess
import time
import uuid
import urllib.request
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_TEMPLATE_PATH = REPO_ROOT / "comfyui_workflows" / "realvis_skin_auto.json"
PRODUCT_YAML_PATH = REPO_ROOT / "assets" / "product.yaml"

# -- comfyui-realism server (port 8190) --
COMFYUI_DIR    = Path(os.environ.get("REALISM_COMFYUI_DIR", "/workspace/comfyui-realism"))
COMFYUI_PYTHON = Path(os.environ.get("REALISM_COMFYUI_PYTHON",
                                     "/workspace/comfyui-realism/venv/bin/python"))
COMFYUI_HOST   = os.environ.get("REALISM_COMFYUI_HOST", "127.0.0.1")
COMFYUI_PORT   = int(os.environ.get("REALISM_COMFYUI_PORT", "8190"))
SERVER_ADDR    = f"{COMFYUI_HOST}:{COMFYUI_PORT}"
COMFYUI_INPUT_DIR = COMFYUI_DIR / "input"

ENDPOINT_LABEL = "local/comfyui-realvis-skin"
COST_PER_IMAGE_USD = 0.0

# -- validated realism params (env-overridable) --
DENOISE        = float(os.environ.get("REALISM_DENOISE", "0.30"))
CFG            = float(os.environ.get("REALISM_CFG", "5.0"))
STEPS          = int(os.environ.get("REALISM_STEPS", "30"))
LORA_STRENGTH  = float(os.environ.get("REALISM_LORA_STRENGTH", "0.7"))
MASK_THRESHOLD = float(os.environ.get("REALISM_MASK_THRESHOLD", "0.3"))
MASK_BLUR      = int(os.environ.get("REALISM_MASK_BLUR", "6"))
MASK_OFFSET    = int(os.environ.get("REALISM_MASK_OFFSET", "12"))

DEFAULT_MASK_PROMPT = "white product box package, white rectangular box"
POS_PROMPT = os.environ.get(
    "REALISM_POS_PROMPT",
    "candid amateur smartphone photo, natural detailed skin with visible pores "
    "and texture, subtle imperfections, photorealistic, sharp focus, natural light")
NEG_PROMPT = os.environ.get(
    "REALISM_NEG_PROMPT",
    "plastic skin, waxy, airbrushed, oversmooth, cgi, 3d render, doll, cartoon, "
    "illustration, blurry, deformed")

SERVER_BOOT_TIMEOUT_S = 240
JOB_TIMEOUT_S = 600


def _read_mask_prompt() -> str:
    """Per-product mask text: env override > product.yaml > built-in default."""
    env = os.environ.get("ALLUVI_MASK_PROMPT")
    if env and env.strip():
        return env.strip()
    try:
        import yaml
        data = yaml.safe_load(PRODUCT_YAML_PATH.read_text(encoding="utf-8")) or {}
        mp = data.get("mask_prompt")
        if not mp and isinstance(data.get("packaging"), dict):
            mp = data["packaging"].get("mask_prompt")
        if mp and str(mp).strip():
            return str(mp).strip()
    except Exception as e:
        print(f"[step_3_realism] could not read mask_prompt from product.yaml "
              f"({e}); using default")
    return DEFAULT_MASK_PROMPT


class ComfyUIHandle:
    def __init__(self, process, started_by_us):
        self.process = process
        self.started_by_us = started_by_us
        self.client_id = uuid.uuid4().hex


def _server_is_up() -> bool:
    try:
        with socket.create_connection((COMFYUI_HOST, COMFYUI_PORT), timeout=2):
            pass
    except OSError:
        return False
    try:
        with urllib.request.urlopen(f"http://{SERVER_ADDR}/system_stats", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def load_pipeline(model_path=None, **kwargs) -> ComfyUIHandle:
    """Ensure the comfyui-realism server (8190) is up; return a handle.
    `model_path` accepted for signature-compat with the old version; unused."""
    print(f"[step_3_realism] realism server target {SERVER_ADDR}")
    if _server_is_up():
        print(f"[step_3_realism] server already running on {SERVER_ADDR}")
        return ComfyUIHandle(None, False)
    if not COMFYUI_PYTHON.exists():
        raise FileNotFoundError(f"realism ComfyUI python not found at {COMFYUI_PYTHON}")
    if not (COMFYUI_DIR / "main.py").exists():
        raise FileNotFoundError(f"realism ComfyUI main.py not found in {COMFYUI_DIR}")
    print(f"[step_3_realism] starting realism ComfyUI on {SERVER_ADDR} ...")
    log_path = COMFYUI_DIR / "comfyui_realism_server.log"
    log_fh = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [str(COMFYUI_PYTHON), "main.py", "--listen", COMFYUI_HOST, "--port", str(COMFYUI_PORT)],
        cwd=str(COMFYUI_DIR), stdout=log_fh, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < SERVER_BOOT_TIMEOUT_S:
        if _server_is_up():
            print(f"[step_3_realism]   server up in {time.time()-t0:.1f}s")
            return ComfyUIHandle(proc, True)
        if proc.poll() is not None:
            raise RuntimeError(f"realism ComfyUI exited during boot - see {log_path}")
        time.sleep(2)
    proc.terminate()
    raise TimeoutError(f"realism ComfyUI did not boot in {SERVER_BOOT_TIMEOUT_S}s")


def unload_pipeline(handle=None) -> None:
    """No-op: the resident realism server stays running between images."""
    print("[step_3_realism] leaving realism ComfyUI server running")


def _build_workflow(input_name, pos, neg, mask_prompt, seed):
    raw = WORKFLOW_TEMPLATE_PATH.read_text(encoding="utf-8")
    for q, v in [('"__LORA_STRENGTH__"', LORA_STRENGTH), ('"__SEED__"', seed),
                 ('"__STEPS__"', STEPS), ('"__CFG__"', CFG), ('"__DENOISE__"', DENOISE),
                 ('"__MASK_THRESHOLD__"', MASK_THRESHOLD), ('"__MASK_BLUR__"', MASK_BLUR),
                 ('"__MASK_OFFSET__"', MASK_OFFSET)]:
        raw = raw.replace(q, str(v))
    wf = json.loads(raw)
    wf["3"]["inputs"]["image"]   = input_name
    wf["4"]["inputs"]["text"]    = pos
    wf["5"]["inputs"]["text"]    = neg
    wf["11"]["inputs"]["prompt"] = mask_prompt
    return wf


def _queue(workflow, client_id):
    payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
    req = urllib.request.Request(f"http://{SERVER_ADDR}/prompt", data=payload)
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if "error" in resp:
        raise RuntimeError(f"realism ComfyUI rejected workflow: {json.dumps(resp, indent=2)}")
    return resp["prompt_id"]


def _wait_and_fetch(pid):
    t0 = time.time()
    while time.time() - t0 < JOB_TIMEOUT_S:
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"http://{SERVER_ADDR}/history/{pid}", timeout=15) as r:
                hist = json.loads(r.read())
        except Exception:
            continue
        if pid in hist:
            st = hist[pid].get("status", {})
            for node_out in hist[pid].get("outputs", {}).values():
                if node_out.get("images"):
                    img = node_out["images"][0]
                    with urllib.request.urlopen(
                            f"http://{SERVER_ADDR}/view?{urllib.parse.urlencode(img)}",
                            timeout=30) as r:
                        return r.read()
            if st.get("status_str") == "error":
                raise RuntimeError(f"realism ComfyUI execution error: {json.dumps(st)}")
    raise TimeoutError(f"realism job {pid} timed out after {JOB_TIMEOUT_S}s")


def generate(pipeline, step_2_local_path, out_path, *, scenario_id="unknown",
             extra_lighting_hint=None, num_inference_steps=None, guidance_scale=None,
             seed=None, safety_tolerance=None, strength=None, image_size=None,
             mask_prompt=None, **kwargs) -> dict:
    """Run the RealVis masked realism pass on the Stage-2 output via 8190.

    Signature preserved from the Kontext version; num_inference_steps /
    guidance_scale / safety_tolerance / strength / image_size are accepted for
    backward-compat and intentionally ignored (this stage uses its own
    validated SDXL params). Aspect ratio is preserved automatically because the
    workflow VAE-encodes the real input image (no fixed-size latent)."""
    step_2_local_path = str(step_2_local_path)
    out_path = Path(out_path)
    if not Path(step_2_local_path).exists():
        raise FileNotFoundError(
            f"Step 2 image not found at {step_2_local_path} - "
            f"can't run realism pass on a missing input.")
    if not WORKFLOW_TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"realism workflow template missing: {WORKFLOW_TEMPLATE_PATH}")
    if not _server_is_up():
        raise RuntimeError(
            f"realism server not reachable on {SERVER_ADDR} - call load_pipeline() first")

    client_id = pipeline.client_id if pipeline else uuid.uuid4().hex
    if seed is None:
        seed = uuid.uuid4().int % (2 ** 31)
    seed = int(seed)

    pos = POS_PROMPT
    if extra_lighting_hint:
        pos += f", {str(extra_lighting_hint).strip()}"
    mp = mask_prompt or _read_mask_prompt()

    COMFYUI_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{scenario_id}_{uuid.uuid4().hex[:8]}".replace("#", "_").replace("/", "_")
    input_name = f"alluvi_realism_in_{tag}.jpg"
    shutil.copy(step_2_local_path, COMFYUI_INPUT_DIR / input_name)

    workflow = _build_workflow(input_name, pos, NEG_PROMPT, mp, seed)

    resolved_args = {
        "positive_prompt": pos, "negative_prompt": NEG_PROMPT, "mask_prompt": mp,
        "denoise": DENOISE, "cfg": CFG, "steps": STEPS, "lora_strength": LORA_STRENGTH,
        "mask_threshold": MASK_THRESHOLD, "mask_blur": MASK_BLUR, "mask_offset": MASK_OFFSET,
        "seed": seed, "input_image": str(Path(step_2_local_path).resolve()),
        "backend": "comfyui", "workflow_template": WORKFLOW_TEMPLATE_PATH.name,
        "comfyui_server": SERVER_ADDR,
    }
    audit_path = out_path.parent / f"{out_path.stem}_request.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps({"endpoint": ENDPOINT_LABEL, "arguments": resolved_args},
                   indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"[step_3_realism] [{scenario_id}]   request audit -> {audit_path.name}")
    print(f"[step_3_realism] [{scenario_id}] submitting (denoise={DENOISE}, cfg={CFG}, "
          f"steps={STEPS}, lora={LORA_STRENGTH}, seed={seed}, mask='{mp}')")

    t0 = time.time()
    pid = _queue(workflow, client_id)
    image_bytes = _wait_and_fetch(pid)
    elapsed = time.time() - t0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.open(io.BytesIO(image_bytes)).convert("RGB").save(out_path, "JPEG", quality=95)

    try:
        (COMFYUI_INPUT_DIR / input_name).unlink(missing_ok=True)
    except Exception:
        pass

    print(f"[step_3_realism] [{scenario_id}]   refined in {elapsed:.1f}s")

    return {
        "local_path": str(out_path),
        "fal_url": None,
        "seed": seed,
        "request_id": f"local-{uuid.uuid4().hex[:8]}",
        "elapsed_seconds": elapsed,
        "endpoint": ENDPOINT_LABEL,
        "cost_usd": COST_PER_IMAGE_USD,
        "prompt_used": pos,
        "safety_tolerance": None,
        "model_paradigm": "sdxl_img2img_masked_realism",
    }
