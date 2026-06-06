# stage1-service — isolated Phase-A + Stage-1 GPU worker

Reuses the repo's proven code (`PulidWrapper` + diffusers `FluxPipeline`) and
exposes it over HTTP on a port. Nothing in `/workspace/alluvi-pipeline` is changed.

## What it is
- Stateless GPU worker. Persona image is passed **per request** (base64 or path).
  No DB, no `persona.yaml`, no hardcoded `assets/persona.jpg`.
- Identical output to the current in-process stages (same `PulidWrapper` call,
  same constants, same `FluxPipeline` call).
- Explicit VRAM control via `/free` so the orchestrator decides residency.

## Endpoints
- `GET  /health`              -> what's loaded, cuda, paths
- `POST /phasea/portrait`     -> { portrait_prompt, width,height,steps,guidance,seed } -> { image_b64, seed, ... }
- `POST /stage1/generate`     -> { step_1_prompt, pulid_params, persona_image_b64|persona_image_path, scenario_id } -> { image_b64, seed, ... }
- `POST /free`                -> { target: phasea|stage1|all }

## Run
```bash
source /workspace/ai-toolkit/venv/bin/activate
pip install fastapi "uvicorn[standard]" python-multipart requests   # one-time
mkdir -p /workspace/stage1-service && cd /workspace/stage1-service
# drop app.py + client_test.py here, then:
ALLUVI_REPO=/workspace/alluvi-pipeline uvicorn app:app --host 0.0.0.0 --port 8192
```
Expose port **8192** in RunPod. From VS Code later, point clients at
`https://<podid>-8192.proxy.runpod.net`.

## Parity test (the gate before integration)
Use the SAME seed + prompt + persona face a current in-process run used, then
compare the JPGs:
```bash
python3 client_test.py health --url http://127.0.0.1:8192
python3 client_test.py stage1 --url http://127.0.0.1:8192 \
  --persona <per-account portrait>.jpg \
  --prompt "$(cat <a real step_1_image_prompt>.txt)" \
  --seed <same seed> --out _test_stage1.jpg
python3 client_test.py free --url http://127.0.0.1:8192 --target all
```
Identical model + params => matching identity/quality. Only after this passes do
we make `step_1_pulid.py` / `phase_a_persona.py` thin HTTP clients to this service.
