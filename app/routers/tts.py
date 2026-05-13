"""Voice Clone TTS router."""

import asyncio

import io
import json
import tempfile
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import torch
import soundfile as sf
from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Request
from fastapi.responses import Response, FileResponse

from app.schemas import VoiceInfo, VoiceListResponse, VoiceRegisterResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tts", tags=["TTS"])

# Allowed audio extensions
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".wma"}

# Voices storage directory
VOICES_DIR = Path("voices")


def _ensure_voices_dir():
    """Create voices directory if it doesn't exist."""
    VOICES_DIR.mkdir(exist_ok=True)


def _save_upload_to_temp(content: bytes, ext: str) -> str:
    """Save uploaded bytes to a temporary file and return the path."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(content)
        return tmp.name


def _validate_audio_extension(filename: str | None) -> str:
    """Validate and return the file extension."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    return ext


def _get_model(request: Request):
    """Get model from app state or raise 503."""
    model = request.app.state.model
    if model is None:
        raise HTTPException(status_code=503, detail="Model is not loaded yet.")
    return model


def _get_queue(request: Request):
    """Get TTS queue from app state."""
    return request.app.state.tts_queue


# ═══════════════════════════════════════════════════════════════════
#  Voice Clone (direct — upload every time)
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/voice-clone",
    summary="Voice Clone TTS",
    description="Upload a reference audio file and get TTS output that clones the voice.",
    responses={
        200: {"content": {"audio/wav": {}}, "description": "Generated WAV audio"},
        400: {"description": "Invalid input"},
        500: {"description": "Generation failed"},
    },
)
async def voice_clone(
    request: Request,
    ref_audio: UploadFile = File(..., description="Reference audio file (wav/mp3/flac)"),
    text: str = Form(..., description="Text to synthesize"),
    ref_text: str = Form(default="", description="Transcript of reference audio (optional, enables ICL mode)"),
    language: str = Form(default="Auto", description="Language: Auto, Chinese, English, Japanese, Korean, etc."),
):
    """Generate voice-cloned TTS audio (one-shot, no voice saving)."""
    ext = _validate_audio_extension(ref_audio.filename)
    model = _get_model(request)
    queue = _get_queue(request)

    tmp_path = None
    try:
        content = await ref_audio.read()
        tmp_path = _save_upload_to_temp(content, ext)

        use_ref_text = ref_text.strip() if ref_text else None
        x_vector_only = use_ref_text is None

        logger.info("voice-clone | text=%r | lang=%s | x_vector_only=%s", text[:80], language, x_vector_only)

        wavs, sr = await queue.submit(
            model.generate_voice_clone,
            text=text,
            language=language,
            ref_audio=tmp_path,
            ref_text=use_ref_text,
            x_vector_only_mode=x_vector_only,
        )

        buf = io.BytesIO()
        sf.write(buf, wavs[0], sr, format="WAV")
        audio_bytes = buf.getvalue()

        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="voice_clone_output.wav"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Voice clone generation failed")
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}") from e
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════
#  Voice Registration (save clone prompt for reuse)
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/voices",
    summary="Register Voice",
    description="Upload reference audio to create and save a voice profile for later TTS use.",
    response_model=VoiceRegisterResponse,
)
async def register_voice(
    request: Request,
    ref_audio: UploadFile = File(..., description="Reference audio file (wav/mp3/flac)"),
    name: str = Form(..., description="Name for this voice profile"),
    ref_text: str = Form(default="", description="Transcript of reference audio (optional, enables ICL mode)"),
):
    """Register a new voice profile from reference audio."""
    ext = _validate_audio_extension(ref_audio.filename)
    model = _get_model(request)

    _ensure_voices_dir()

    tmp_path = None
    started_at = datetime.now(timezone.utc)
    try:
        content = await ref_audio.read()
        tmp_path = _save_upload_to_temp(content, ext)

        use_ref_text = ref_text.strip() if ref_text else None
        x_vector_only = use_ref_text is None

        logger.info("Registering voice '%s' | ref_text=%r | x_vector_only=%s", name, (use_ref_text or "")[:40], x_vector_only)

        # Build voice clone prompt (extracts speaker embedding + codes)
        prompt_items = model.create_voice_clone_prompt(
            ref_audio=tmp_path,
            ref_text=use_ref_text,
            x_vector_only_mode=x_vector_only,
        )

        # Save prompt item
        voice_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)

        prompt_item = prompt_items[0]
        save_data = {
            "ref_code": prompt_item.ref_code,
            "ref_spk_embedding": prompt_item.ref_spk_embedding,
            "x_vector_only_mode": prompt_item.x_vector_only_mode,
            "icl_mode": prompt_item.icl_mode,
            "ref_text": prompt_item.ref_text,
        }
        torch.save(save_data, VOICES_DIR / f"{voice_id}.pt")

        # Save metadata
        meta = {"voice_id": voice_id, "name": name, "created_at": now.isoformat(), "icl_mode": prompt_item.icl_mode}
        (VOICES_DIR / f"{voice_id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        finished_at = datetime.now(timezone.utc)
        elapsed = (finished_at - started_at).total_seconds()
        logger.info("Voice registered: id=%s name=%s elapsed=%.3fs", voice_id, name, elapsed)

        return VoiceRegisterResponse(
            voice_id=voice_id,
            name=name,
            created_at=now,
            icl_mode=prompt_item.icl_mode,
            started_at=started_at,
            finished_at=finished_at,
            elapsed_seconds=elapsed,
            download_url=f"/tts/voices/{voice_id}/download",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Voice registration failed")
        raise HTTPException(status_code=500, detail=f"Voice registration failed: {e}") from e
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════
#  Voice List
# ═══════════════════════════════════════════════════════════════════

@router.get(
    "/voices",
    summary="List Voices",
    description="List all saved voice profiles.",
    response_model=VoiceListResponse,
)
async def list_voices():
    """List all registered voice profiles."""
    _ensure_voices_dir()

    voices: list[VoiceInfo] = []
    for meta_file in sorted(VOICES_DIR.glob("*.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            voices.append(VoiceInfo(
                voice_id=meta["voice_id"],
                name=meta["name"],
                created_at=datetime.fromisoformat(meta["created_at"]),
                icl_mode=meta.get("icl_mode", False),
            ))
        except Exception:
            logger.warning("Skipping corrupted metadata: %s", meta_file)

    return VoiceListResponse(voices=voices, total=len(voices))


# ═══════════════════════════════════════════════════════════════════
#  Voice Delete
# ═══════════════════════════════════════════════════════════════════

@router.delete(
    "/voices/{voice_id}",
    summary="Delete Voice",
    description="Delete a saved voice profile.",
)
async def delete_voice(voice_id: str):
    """Delete a registered voice profile."""
    pt_path = VOICES_DIR / f"{voice_id}.pt"
    json_path = VOICES_DIR / f"{voice_id}.json"

    if not pt_path.exists():
        raise HTTPException(status_code=404, detail=f"Voice '{voice_id}' not found.")

    pt_path.unlink(missing_ok=True)
    json_path.unlink(missing_ok=True)

    logger.info("Voice deleted: %s", voice_id)
    return {"detail": f"Voice '{voice_id}' deleted."}


# ═══════════════════════════════════════════════════════════════════
#  Voice Download
# ═══════════════════════════════════════════════════════════════════

@router.get(
    "/voices/{voice_id}/download",
    summary="Download Voice Checkpoint",
    description="Download the registered voice profile (.pt file).",
)
async def download_voice(voice_id: str):
    """Download a registered voice profile."""
    pt_path = VOICES_DIR / f"{voice_id}.pt"
    if not pt_path.exists():
        raise HTTPException(status_code=404, detail=f"Voice '{voice_id}' not found.")
    
    return FileResponse(
        path=pt_path,
        media_type="application/octet-stream",
        filename=f"{voice_id}.pt"
    )


# ═══════════════════════════════════════════════════════════════════
#  Synthesize with Saved Voice
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/voices/{voice_id}/synthesize",
    summary="Synthesize with Saved Voice",
    description="Generate TTS audio using a previously registered voice profile.",
    responses={
        200: {"content": {"audio/wav": {}}, "description": "Generated WAV audio"},
        404: {"description": "Voice not found"},
        500: {"description": "Generation failed"},
    },
)
async def synthesize_with_voice(
    request: Request,
    voice_id: str,
    text: str = Form(..., description="Text to synthesize"),
    language: str = Form(default="Auto", description="Language: Auto, Chinese, English, Japanese, Korean, etc."),
):
    """Generate TTS audio using a saved voice profile."""
    from qwen_tts import VoiceClonePromptItem

    model = _get_model(request)
    queue = _get_queue(request)

    pt_path = VOICES_DIR / f"{voice_id}.pt"
    if not pt_path.exists():
        raise HTTPException(status_code=404, detail=f"Voice '{voice_id}' not found.")

    started_at = datetime.now(timezone.utc)
    try:
        # Load saved prompt
        data = torch.load(pt_path, map_location=model.device, weights_only=False)
        prompt_item = VoiceClonePromptItem(
            ref_code=data["ref_code"],
            ref_spk_embedding=data["ref_spk_embedding"],
            x_vector_only_mode=data["x_vector_only_mode"],
            icl_mode=data["icl_mode"],
            ref_text=data.get("ref_text"),
        )

        logger.info("synthesize | voice=%s | text=%r | lang=%s", voice_id, text[:80], language)

        wavs, sr = await queue.submit(
            model.generate_voice_clone,
            text=text,
            language=language,
            voice_clone_prompt=[prompt_item],
        )

        buf = io.BytesIO()
        sf.write(buf, wavs[0], sr, format="WAV")
        audio_bytes = buf.getvalue()

        finished_at = datetime.now(timezone.utc)
        elapsed = (finished_at - started_at).total_seconds()
        logger.info("synthesize done | voice=%s | elapsed=%.3fs", voice_id, elapsed)

        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "Content-Disposition": f'attachment; filename="{voice_id}_output.wav"',
                "X-Started-At": started_at.isoformat(),
                "X-Finished-At": finished_at.isoformat(),
                "X-Elapsed-Seconds": f"{elapsed:.3f}",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Synthesis with saved voice failed")
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {e}") from e


# ═══════════════════════════════════════════════════════════════════
#  Synthesize with Uploaded Checkpoint
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/synthesize",
    summary="Synthesize with Uploaded Checkpoint",
    description="Generate TTS audio using an uploaded voice profile checkpoint (.pt file).",
    responses={
        200: {"content": {"audio/wav": {}}, "description": "Generated WAV audio"},
        400: {"description": "Invalid input"},
        500: {"description": "Generation failed"},
    },
)
async def synthesize_with_checkpoint(
    request: Request,
    checkpoint: UploadFile = File(..., description="Voice profile checkpoint (.pt)"),
    text: str = Form(..., description="Text to synthesize"),
    language: str = Form(default="Auto", description="Language: Auto, Chinese, English, Japanese, Korean, etc."),
):
    """Generate TTS audio using an uploaded voice profile checkpoint."""
    from qwen_tts import VoiceClonePromptItem

    if not checkpoint.filename.endswith(".pt"):
        raise HTTPException(status_code=400, detail="Checkpoint file must have a .pt extension.")

    model = _get_model(request)
    queue = _get_queue(request)

    tmp_path = None
    started_at = datetime.now(timezone.utc)
    try:
        content = await checkpoint.read()
        tmp_path = _save_upload_to_temp(content, ".pt")

        # Load saved prompt
        data = torch.load(tmp_path, map_location=model.device, weights_only=False)
        prompt_item = VoiceClonePromptItem(
            ref_code=data["ref_code"],
            ref_spk_embedding=data["ref_spk_embedding"],
            x_vector_only_mode=data["x_vector_only_mode"],
            icl_mode=data["icl_mode"],
            ref_text=data.get("ref_text"),
        )

        logger.info("synthesize_with_checkpoint | text=%r | lang=%s", text[:80], language)

        wavs, sr = await queue.submit(
            model.generate_voice_clone,
            text=text,
            language=language,
            voice_clone_prompt=[prompt_item],
        )

        buf = io.BytesIO()
        sf.write(buf, wavs[0], sr, format="WAV")
        audio_bytes = buf.getvalue()

        finished_at = datetime.now(timezone.utc)
        elapsed = (finished_at - started_at).total_seconds()
        logger.info("synthesize_with_checkpoint done | elapsed=%.3fs", elapsed)

        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "Content-Disposition": 'attachment; filename="synthesis_output.wav"',
                "X-Started-At": started_at.isoformat(),
                "X-Finished-At": finished_at.isoformat(),
                "X-Elapsed-Seconds": f"{elapsed:.3f}",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Synthesis with uploaded checkpoint failed")
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {e}") from e
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

