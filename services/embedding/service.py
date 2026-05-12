"""
BHN local embedding service.

Serves BAAI/bge-small-en-v1.5 (384-dim, English, BGE family) over localhost.
Runs via fastembed (ONNX runtime, no pytorch) so the footprint stays small —
~250-345 MB RAM steady state, ~6 ms per /embed call on the 2 vCPU LA hub.

Bound to 127.0.0.1 only — n8n on the same host calls it via loopback.
No external network exposure.

Endpoints:
  GET  /health           — liveness check
  POST /embed            — body: {"text": "..."}, returns {"vector": [384 floats], "ms": int}
  POST /embed/batch      — body: {"texts": ["...", ...]}, returns {"vectors": [...], "ms": int}

Deployed via /etc/systemd/system/eh-embed.service (LA-side unit name kept until migration).
Repo-side source filename: bhn-embed.service (renamed 2026-05-11). See that file in this folder.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastembed import TextEmbedding
import time
from typing import List

MODEL_NAME = "BAAI/bge-small-en-v1.5"
CACHE_DIR = "/opt/eh-embed/cache"

print(f"[eh-embed] loading {MODEL_NAME}...", flush=True)
model = TextEmbedding(model_name=MODEL_NAME, cache_dir=CACHE_DIR)
# Warmup — forces model download/load before first request
_ = list(model.embed(["warmup"]))
DIM = len(_[0])
print(f"[eh-embed] ready. dim={DIM}", flush=True)

app = FastAPI(title="EH Embedding Service", version="1.0")


class EmbedRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


class EmbedBatchRequest(BaseModel):
    texts: List[str] = Field(..., min_length=1, max_length=64)


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "dim": DIM}


@app.post("/embed")
def embed(req: EmbedRequest):
    t0 = time.time()
    try:
        vecs = list(model.embed([req.text]))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"embedding failed: {e}")
    return {
        "vector": vecs[0].tolist(),
        "model": MODEL_NAME,
        "ms": int((time.time() - t0) * 1000),
    }


@app.post("/embed/batch")
def embed_batch(req: EmbedBatchRequest):
    t0 = time.time()
    try:
        vecs = list(model.embed(req.texts))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"embedding failed: {e}")
    return {
        "vectors": [v.tolist() for v in vecs],
        "model": MODEL_NAME,
        "ms": int((time.time() - t0) * 1000),
    }
