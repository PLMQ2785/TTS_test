"""On-demand model manager — keeps one Qwen3-TTS model in GPU at a time."""

import asyncio
import logging
from enum import Enum

import torch
from qwen_tts import Qwen3TTSModel

logger = logging.getLogger(__name__)


class ModelType(str, Enum):
    BASE = "base"
    CUSTOM_VOICE = "custom_voice"
    VOICE_DESIGN = "voice_design"


# Model paths (relative to project root)
MODEL_PATHS: dict[ModelType, str] = {
    ModelType.BASE: "./Qwen3-TTS-12Hz-1.7B-Base",
    ModelType.CUSTOM_VOICE: "./Qwen3-TTS-12Hz-1.7B-CustomVoice",
    ModelType.VOICE_DESIGN: "./Qwen3-TTS-12Hz-1.7B-VoiceDesign",
}


class ModelManager:
    """
    Manages Qwen3-TTS models with on-demand loading.

    Only one model is kept in GPU memory at a time. When a different
    model type is requested, the current model is unloaded first.
    An asyncio.Lock prevents concurrent model swaps.
    """

    def __init__(self, device: str = "cuda:1", dtype=torch.bfloat16):
        self._device = device
        self._dtype = dtype
        self._current_type: ModelType | None = None
        self._model: Qwen3TTSModel | None = None
        self._lock = asyncio.Lock()

    @property
    def current_model_type(self) -> ModelType | None:
        return self._current_type

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _unload(self) -> None:
        """Unload the current model and free GPU memory."""
        if self._model is not None:
            logger.info("Unloading model: %s", self._current_type)
            del self._model
            self._model = None
            self._current_type = None
            torch.cuda.empty_cache()
            logger.info("Model unloaded, GPU cache cleared.")

    def _load(self, model_type: ModelType) -> Qwen3TTSModel:
        """Load a model into GPU."""
        path = MODEL_PATHS[model_type]
        logger.info("Loading model: %s from %s ...", model_type, path)
        model = Qwen3TTSModel.from_pretrained(
            path,
            device_map=self._device,
            dtype=self._dtype,
            attn_implementation="flash_attention_2",
        )
        self._model = model
        self._current_type = model_type
        logger.info("Model loaded: %s", model_type)
        return model

    async def get_model(self, model_type: ModelType) -> Qwen3TTSModel:
        """
        Get the requested model, loading/swapping if necessary.

        This is safe to call from async context — the actual loading
        runs in a thread to avoid blocking the event loop.
        """
        async with self._lock:
            if self._current_type == model_type and self._model is not None:
                return self._model

            # Swap: unload current, load requested
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._unload)
            model = await loop.run_in_executor(None, self._load, model_type)
            return model

    async def preload(self, model_type: ModelType) -> None:
        """Preload a model at startup."""
        await self.get_model(model_type)

    async def shutdown(self) -> None:
        """Unload model and release resources."""
        async with self._lock:
            self._unload()
            logger.info("ModelManager shut down.")
