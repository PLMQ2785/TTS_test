"""Pydantic schemas for request/response models."""

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    queue_pending: int = 0


class ErrorResponse(BaseModel):
    detail: str


class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    created_at: datetime
    icl_mode: bool


class VoiceListResponse(BaseModel):
    voices: list[VoiceInfo]
    total: int
