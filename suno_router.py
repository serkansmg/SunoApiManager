"""
FastAPI Router for Suno API — Swagger-documented endpoints.

All endpoints under /suno/ prefix, communicating directly with Suno's API
via the SunoClient (no Node.js proxy needed).

Include in app.py:
    from suno_router import router as suno_router
    app.include_router(suno_router)
"""

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from suno_api import get_client
from suno_models import (
    AudioInfo,
    ConcatRequest,
    CreditsInfo,
    CustomGenerateRequest,
    ExtendAudioRequest,
    FALLBACK_MODELS,
    GenerateRequest,
    LyricsRequest,
    LyricsResponse,
    MessageResponse,
    SunoModel,
    WavUrlResponse,
)

logger = logging.getLogger("suno-manager")

router = APIRouter(prefix="/suno", tags=["Suno API"])


# ─── Helper ──────────────────────────────────────────────────

async def _get_client_or_error():
    """Get SunoClient or raise HTTP 503."""
    try:
        return await get_client()
    except Exception as e:
        logger.error(f"SunoClient init failed: {e}")
        raise HTTPException(status_code=503, detail=f"Suno API not available: {e}")


# ─── Generation Endpoints ────────────────────────────────────

@router.post(
    "/generate",
    response_model=list[AudioInfo],
    summary="Generate music from description",
    description="Simple mode: describe the music, Suno writes lyrics and generates audio. Returns 2 clips.",
)
async def generate(req: GenerateRequest):
    client = await _get_client_or_error()
    try:
        result = await client.generate(
            prompt=req.prompt,
            make_instrumental=req.make_instrumental,
            model=req.model,
        )
        return result
    except Exception as e:
        logger.error(f"/suno/generate error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/custom-generate",
    response_model=list[AudioInfo],
    summary="Generate music with custom lyrics",
    description="Custom mode: provide full lyrics, style tags, and title. Returns 2 clips.",
)
async def custom_generate(req: CustomGenerateRequest):
    client = await _get_client_or_error()
    try:
        result = await client.custom_generate(
            prompt=req.prompt,
            tags=req.tags,
            title=req.title,
            negative_tags=req.negative_tags,
            make_instrumental=req.make_instrumental,
            model=req.model,
        )
        return result
    except Exception as e:
        logger.error(f"/suno/custom-generate error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/extend",
    response_model=list[AudioInfo],
    summary="Extend an existing audio clip",
    description="Continue a song from a specific timestamp. Returns 2 clips.",
)
async def extend_audio(req: ExtendAudioRequest):
    client = await _get_client_or_error()
    try:
        result = await client.extend_audio(
            audio_id=req.audio_id,
            prompt=req.prompt,
            continue_at=req.continue_at,
            tags=req.tags,
            negative_tags=req.negative_tags,
            title=req.title,
            model=req.model,
        )
        return result
    except Exception as e:
        logger.error(f"/suno/extend error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/concat",
    summary="Concatenate extension clips",
    description="Stitch multiple extension clips into one complete song.",
)
async def concatenate(req: ConcatRequest):
    client = await _get_client_or_error()
    try:
        result = await client.concatenate(clip_id=req.clip_id)
        return result
    except Exception as e:
        logger.error(f"/suno/concat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/lyrics",
    response_model=LyricsResponse,
    summary="Generate lyrics",
    description="Generate lyrics from a text prompt. Polls until complete (up to 60s).",
)
async def generate_lyrics(req: LyricsRequest):
    client = await _get_client_or_error()
    try:
        result = await client.generate_lyrics(prompt=req.prompt)
        return result
    except Exception as e:
        logger.error(f"/suno/lyrics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Query Endpoints ─────────────────────────────────────────

@router.get(
    "/feed",
    response_model=list[AudioInfo],
    summary="Get audio clip info",
    description="Fetch clip metadata by IDs (comma-separated), or paginated library if no IDs given.",
)
async def get_feed(
    ids: str = Query(None, description="Comma-separated clip IDs"),
    page: int = Query(None, description="Page number for library browsing"),
):
    client = await _get_client_or_error()
    try:
        id_list = [i.strip() for i in ids.split(",") if i.strip()] if ids else None
        result = await client.get_audio_info(ids=id_list, page=page)
        return result
    except Exception as e:
        logger.error(f"/suno/feed error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/clip/{clip_id}",
    summary="Get single clip details",
    description="Get raw clip data by ID (unmapped, full Suno response).",
)
async def get_clip(clip_id: str):
    client = await _get_client_or_error()
    try:
        result = await client.get_clip(clip_id=clip_id)
        return result
    except Exception as e:
        logger.error(f"/suno/clip error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Billing ─────────────────────────────────────────────────

@router.get(
    "/credits",
    response_model=CreditsInfo,
    summary="Get account credits",
    description="Returns Suno account billing info: credits remaining, period, limits.",
)
async def get_credits():
    client = await _get_client_or_error()
    try:
        result = await client.get_credits()
        return result
    except Exception as e:
        logger.error(f"/suno/credits error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Models ──────────────────────────────────────────────────

@router.get(
    "/models",
    response_model=list[SunoModel],
    summary="List available generation models",
    description="Returns Suno generation models from the billing API. Falls back to a cached list if API is unreachable.",
)
async def list_models():
    try:
        client = await _get_client_or_error()
        models = await client.get_models()
        if models:
            return models
    except Exception as e:
        logger.warning(f"Could not fetch models from API, using fallback: {e}")
    return FALLBACK_MODELS


@router.get(
    "/billing-info",
    summary="Get full billing/subscription info",
    description="Returns complete billing data including subscription, models, features, plans, and limits.",
)
async def get_billing_info():
    client = await _get_client_or_error()
    try:
        result = await client.get_billing_info()
        return result
    except Exception as e:
        logger.error(f"/suno/billing-info error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── WAV Conversion ─────────────────────────────────────────

@router.post(
    "/convert-wav",
    response_model=MessageResponse,
    summary="Trigger WAV conversion",
    description="Start server-side WAV conversion for a clip. Must be called before /wav-url.",
)
async def convert_wav(
    id: str = Query(..., description="Suno clip ID"),
):
    client = await _get_client_or_error()
    try:
        result = await client.convert_wav(clip_id=id)
        return MessageResponse(status=204, message="WAV conversion triggered")
    except Exception as e:
        logger.error(f"/suno/convert-wav error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/wav-url",
    response_model=WavUrlResponse,
    summary="Get WAV file URL",
    description="Get the CDN URL for the WAV file. Returns null if conversion is still in progress. Poll until non-null.",
)
async def get_wav_url(
    id: str = Query(..., description="Suno clip ID"),
):
    client = await _get_client_or_error()
    try:
        url = await client.get_wav_url(clip_id=id)
        return WavUrlResponse(wav_file_url=url)
    except Exception as e:
        logger.error(f"/suno/wav-url error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
