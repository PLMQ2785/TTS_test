"""Qwen3-TTS Voice Clone Demo API Server."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from qwen_tts import Qwen3TTSModel

from app.queue_worker import TTSQueue
from app.routers import tts
from app.schemas import HealthResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MODEL_PATH = os.getenv("MODEL_PATH", "./Qwen3-TTS-12Hz-1.7B-Base")
DEVICE_MAP = os.getenv("DEVICE_MAP", "cuda:0")
ATTN_IMPLEMENTATION = os.getenv("ATTN_IMPLEMENTATION", "flash_attention_2")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "1"))  # 1 = sequential, N = up to N concurrent TTS jobs


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load model on startup, release on shutdown."""
    logger.info(
        "Loading Qwen3-TTS model from %s with device=%s attn=%s ...",
        MODEL_PATH,
        DEVICE_MAP,
        ATTN_IMPLEMENTATION,
    )
    model = Qwen3TTSModel.from_pretrained(
        MODEL_PATH,
        device_map=DEVICE_MAP,
        dtype=torch.bfloat16,
        attn_implementation=ATTN_IMPLEMENTATION,
    )
    app.state.model = model
    logger.info("Model loaded successfully.")

    # Start TTS queue worker(s)
    tts_queue = TTSQueue(max_workers=MAX_WORKERS)
    app.state.tts_queue = tts_queue
    worker_tasks = tts_queue.start_workers()

    yield

    # Shutdown
    logger.info("Shutting down — stopping workers ...")
    await TTSQueue.stop(worker_tasks)

    logger.info("Releasing model ...")
    app.state.model = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("Model released.")


app = FastAPI(
    title="Qwen3-TTS Voice Clone API",
    description="Upload reference audio and synthesize speech that clones the voice.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — open for demo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(tts.router)


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Health check endpoint."""
    model_loaded = hasattr(app.state, "model") and app.state.model is not None
    queue_pending = 0
    if hasattr(app.state, "tts_queue"):
        queue_pending = app.state.tts_queue.pending_count
    return HealthResponse(status="ok", model_loaded=model_loaded, queue_pending=queue_pending)
