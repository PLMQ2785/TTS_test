"""Qwen3-TTS Multi-Model API Server."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.model_manager import ModelManager, ModelType
from app.queue_worker import TTSQueue
from app.routers import tts
from app.schemas import HealthResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MAX_WORKERS = 1  # 1 = sequential (required for on-demand model swapping)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load model manager on startup, release on shutdown."""
    # Initialize model manager and preload Base model
    model_manager = ModelManager(device="cuda:1")
    await model_manager.preload(ModelType.BASE)
    app.state.model_manager = model_manager

    # Start TTS queue worker(s)
    tts_queue = TTSQueue(max_workers=MAX_WORKERS)
    app.state.tts_queue = tts_queue
    worker_tasks = tts_queue.start_workers()

    yield

    # Shutdown
    logger.info("Shutting down — stopping workers ...")
    await TTSQueue.stop(worker_tasks)

    logger.info("Releasing models ...")
    await model_manager.shutdown()
    logger.info("Shutdown complete.")


app = FastAPI(
    title="Qwen3-TTS API",
    description="Multi-model TTS API: Voice Clone, Voice Design, Custom Voice with instruct-based style control.",
    version="0.2.0",
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
    mm = getattr(app.state, "model_manager", None)
    model_loaded = mm is not None and mm.is_loaded
    current_model = mm.current_model_type.value if mm and mm.current_model_type else None
    queue_pending = 0
    if hasattr(app.state, "tts_queue"):
        queue_pending = app.state.tts_queue.pending_count
    return HealthResponse(
        status="ok",
        model_loaded=model_loaded,
        current_model=current_model,
        queue_pending=queue_pending,
    )
