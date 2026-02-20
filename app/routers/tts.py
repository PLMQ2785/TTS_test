"""TTS router — Voice Clone, Voice Design, Custom Voice endpoints."""

import asyncio
import io
import json
import tempfile
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
import soundfile as sf
from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Request
from fastapi.responses import Response

from app.model_manager import ModelManager, ModelType
from app.schemas import VoiceInfo, VoiceListResponse, SpeakerInfo, SpeakerListResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tts", tags=["TTS"])

# Allowed audio extensions
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".wma"}

# Voices storage directory
VOICES_DIR = Path("voices")

# Speaker descriptions for CustomVoice model
SPEAKER_DESCRIPTIONS = [
    SpeakerInfo(name="Vivian", description="Bright, slightly edgy young female voice.", native_language="Chinese"),
    SpeakerInfo(name="Serena", description="Warm, gentle young female voice.", native_language="Chinese"),
    SpeakerInfo(name="Uncle_Fu", description="Seasoned male voice with a low, mellow timbre.", native_language="Chinese"),
    SpeakerInfo(name="Dylan", description="Youthful Beijing male voice with a clear, natural timbre.", native_language="Chinese (Beijing Dialect)"),
    SpeakerInfo(name="Eric", description="Lively Chengdu male voice with a slightly husky brightness.", native_language="Chinese (Sichuan Dialect)"),
    SpeakerInfo(name="Ryan", description="Dynamic male voice with strong rhythmic drive.", native_language="English"),
    SpeakerInfo(name="Aiden", description="Sunny American male voice with a clear midrange.", native_language="English"),
    SpeakerInfo(name="Ono_Anna", description="Playful Japanese female voice with a light, nimble timbre.", native_language="Japanese"),
    SpeakerInfo(name="Sohee", description="Warm Korean female voice with rich emotion.", native_language="Korean"),
]

VALID_SPEAKER_NAMES = {s.name for s in SPEAKER_DESCRIPTIONS}


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


def _get_model_manager(request: Request) -> ModelManager:
    """Get ModelManager from app state."""
    mm = getattr(request.app.state, "model_manager", None)
    if mm is None:
        raise HTTPException(status_code=503, detail="Model manager is not initialized.")
    return mm


def _get_queue(request: Request):
    """Get TTS queue from app state."""
    return request.app.state.tts_queue


def _wav_response(wav: np.ndarray, sr: int, filename: str = "output.wav") -> Response:
    """Convert numpy waveform to WAV Response."""
    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV")
    return Response(
        content=buf.getvalue(),
        media_type="audio/wav",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    mm = _get_model_manager(request)
    queue = _get_queue(request)

    tmp_path = None
    try:
        content = await ref_audio.read()
        tmp_path = _save_upload_to_temp(content, ext)

        use_ref_text = ref_text.strip() if ref_text else None
        x_vector_only = use_ref_text is None

        logger.info("voice-clone | text=%r | lang=%s | x_vector_only=%s", text[:80], language, x_vector_only)

        async def _generate():
            model = await mm.get_model(ModelType.BASE)
            return model.generate_voice_clone(
                text=text,
                language=language,
                ref_audio=tmp_path,
                ref_text=use_ref_text,
                x_vector_only_mode=x_vector_only,
            )

        wavs, sr = await queue.submit(_generate)
        return _wav_response(wavs[0], sr, "voice_clone_output.wav")

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
    response_model=VoiceInfo,
)
async def register_voice(
    request: Request,
    ref_audio: UploadFile = File(..., description="Reference audio file (wav/mp3/flac)"),
    name: str = Form(..., description="Name for this voice profile"),
    ref_text: str = Form(default="", description="Transcript of reference audio (optional, enables ICL mode)"),
):
    """Register a new voice profile from reference audio."""
    ext = _validate_audio_extension(ref_audio.filename)
    mm = _get_model_manager(request)

    _ensure_voices_dir()

    tmp_path = None
    try:
        content = await ref_audio.read()
        tmp_path = _save_upload_to_temp(content, ext)

        use_ref_text = ref_text.strip() if ref_text else None
        x_vector_only = use_ref_text is None

        logger.info("Registering voice '%s' | ref_text=%r | x_vector_only=%s", name, (use_ref_text or "")[:40], x_vector_only)

        # Build voice clone prompt (need Base model)
        model = await mm.get_model(ModelType.BASE)
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

        logger.info("Voice registered: id=%s name=%s", voice_id, name)

        return VoiceInfo(voice_id=voice_id, name=name, created_at=now, icl_mode=prompt_item.icl_mode)

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

    mm = _get_model_manager(request)
    queue = _get_queue(request)

    pt_path = VOICES_DIR / f"{voice_id}.pt"
    if not pt_path.exists():
        raise HTTPException(status_code=404, detail=f"Voice '{voice_id}' not found.")

    try:
        async def _generate():
            model = await mm.get_model(ModelType.BASE)
            # Load saved prompt
            data = torch.load(pt_path, map_location=model.device, weights_only=False)
            prompt_item = VoiceClonePromptItem(
                ref_code=data["ref_code"],
                ref_spk_embedding=data["ref_spk_embedding"],
                x_vector_only_mode=data["x_vector_only_mode"],
                icl_mode=data["icl_mode"],
                ref_text=data.get("ref_text"),
            )
            return model.generate_voice_clone(
                text=text,
                language=language,
                voice_clone_prompt=[prompt_item],
            )

        logger.info("synthesize | voice=%s | text=%r | lang=%s", voice_id, text[:80], language)
        wavs, sr = await queue.submit(_generate)
        return _wav_response(wavs[0], sr, f"{voice_id}_output.wav")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Synthesis with saved voice failed")
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {e}") from e


# ═══════════════════════════════════════════════════════════════════
#  Voice Design — generate speech with natural language instruct
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/voice-design",
    summary="Voice Design TTS",
    description=(
        "Generate speech with a voice designed from a natural language description. "
        "Describe the desired voice characteristics (gender, age, tone, emotion, etc.) in the instruct field."
    ),
    responses={
        200: {"content": {"audio/wav": {}}, "description": "Generated WAV audio"},
        400: {"description": "Invalid input"},
        500: {"description": "Generation failed"},
    },
)
async def voice_design(
    request: Request,
    text: str = Form(..., description="Text to synthesize"),
    instruct: str = Form(..., description="Natural language voice/style description (e.g. 'Warm female voice, speaking slowly and gently')"),
    language: str = Form(default="Auto", description="Language: Auto, Chinese, English, Japanese, Korean, etc."),
):
    """Generate TTS audio using the VoiceDesign model with natural language instruct."""
    mm = _get_model_manager(request)
    queue = _get_queue(request)

    if not instruct.strip():
        raise HTTPException(status_code=400, detail="instruct is required for voice design.")

    try:
        async def _generate():
            model = await mm.get_model(ModelType.VOICE_DESIGN)
            return model.generate_voice_design(
                text=text,
                language=language,
                instruct=instruct.strip(),
            )

        logger.info("voice-design | text=%r | instruct=%r | lang=%s", text[:80], instruct[:80], language)
        wavs, sr = await queue.submit(_generate)
        return _wav_response(wavs[0], sr, "voice_design_output.wav")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Voice design generation failed")
        raise HTTPException(status_code=500, detail=f"Voice design failed: {e}") from e


# ═══════════════════════════════════════════════════════════════════
#  Voice Design + Clone — design a voice and register it for reuse
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/voice-design-clone",
    summary="Voice Design + Clone",
    description=(
        "Design a voice via natural language, then register it as a reusable voice profile. "
        "The designed voice can later be used with /tts/voices/{voice_id}/synthesize."
    ),
    response_model=VoiceInfo,
)
async def voice_design_clone(
    request: Request,
    name: str = Form(..., description="Name for this voice profile"),
    ref_text: str = Form(..., description="Reference text for the voice design (will be spoken in the designed voice)"),
    instruct: str = Form(..., description="Natural language voice/style description"),
    language: str = Form(default="Auto", description="Language for the reference text"),
):
    """Design a voice and register it as a clone profile for reuse."""
    mm = _get_model_manager(request)

    if not instruct.strip():
        raise HTTPException(status_code=400, detail="instruct is required for voice design.")
    if not ref_text.strip():
        raise HTTPException(status_code=400, detail="ref_text is required (text to speak in the designed voice).")

    _ensure_voices_dir()

    try:
        logger.info("voice-design-clone | name=%s | instruct=%r | lang=%s", name, instruct[:80], language)

        # Step 1: Generate reference audio with VoiceDesign model
        vd_model = await mm.get_model(ModelType.VOICE_DESIGN)
        ref_wavs, sr = vd_model.generate_voice_design(
            text=ref_text.strip(),
            language=language,
            instruct=instruct.strip(),
        )

        # Step 2: Create clone prompt with Base model
        base_model = await mm.get_model(ModelType.BASE)
        prompt_items = base_model.create_voice_clone_prompt(
            ref_audio=(ref_wavs[0], sr),
            ref_text=ref_text.strip(),
        )

        # Step 3: Save voice profile
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

        meta = {
            "voice_id": voice_id,
            "name": name,
            "created_at": now.isoformat(),
            "icl_mode": prompt_item.icl_mode,
            "source": "voice_design",
            "instruct": instruct.strip(),
        }
        (VOICES_DIR / f"{voice_id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        logger.info("Voice design+clone registered: id=%s name=%s", voice_id, name)

        return VoiceInfo(voice_id=voice_id, name=name, created_at=now, icl_mode=prompt_item.icl_mode)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Voice design+clone failed")
        raise HTTPException(status_code=500, detail=f"Voice design+clone failed: {e}") from e


# ═══════════════════════════════════════════════════════════════════
#  Custom Voice — preset speakers with instruct-based style control
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/custom-voice",
    summary="Custom Voice TTS",
    description=(
        "Generate speech using a preset speaker with optional instruct-based style control. "
        "Use GET /tts/speakers to see available speakers. "
        "The instruct field accepts natural language like '빠르게 말하기', 'Very excited', '낮은 톤으로 차분하게'."
    ),
    responses={
        200: {"content": {"audio/wav": {}}, "description": "Generated WAV audio"},
        400: {"description": "Invalid input"},
        500: {"description": "Generation failed"},
    },
)
async def custom_voice(
    request: Request,
    text: str = Form(..., description="Text to synthesize"),
    speaker: str = Form(..., description="Speaker name (e.g. Vivian, Ryan, Sohee)"),
    language: str = Form(default="Auto", description="Language: Auto, Chinese, English, Japanese, Korean, etc."),
    instruct: str = Form(default="", description="Optional style instruction (e.g. '빠르게 말하기', 'Speak slowly and softly')"),
):
    """Generate TTS audio using a CustomVoice preset speaker with optional style instruct."""
    mm = _get_model_manager(request)
    queue = _get_queue(request)

    if speaker not in VALID_SPEAKER_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown speaker '{speaker}'. Valid speakers: {', '.join(sorted(VALID_SPEAKER_NAMES))}",
        )

    try:
        async def _generate():
            model = await mm.get_model(ModelType.CUSTOM_VOICE)
            kwargs = dict(
                text=text,
                language=language,
                speaker=speaker,
            )
            if instruct.strip():
                kwargs["instruct"] = instruct.strip()
            return model.generate_custom_voice(**kwargs)

        logger.info("custom-voice | speaker=%s | text=%r | instruct=%r | lang=%s", speaker, text[:80], instruct[:80], language)
        wavs, sr = await queue.submit(_generate)
        return _wav_response(wavs[0], sr, "custom_voice_output.wav")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Custom voice generation failed")
        raise HTTPException(status_code=500, detail=f"Custom voice failed: {e}") from e


# ═══════════════════════════════════════════════════════════════════
#  Speakers list
# ═══════════════════════════════════════════════════════════════════

@router.get(
    "/speakers",
    summary="List Speakers",
    description="List all available preset speakers for Custom Voice TTS.",
    response_model=SpeakerListResponse,
)
async def list_speakers():
    """List available preset speakers for CustomVoice model."""
    return SpeakerListResponse(speakers=SPEAKER_DESCRIPTIONS, total=len(SPEAKER_DESCRIPTIONS))
