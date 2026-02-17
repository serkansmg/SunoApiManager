"""
Pydantic models for Suno API requests and responses.
Used by suno_router.py for Swagger/OpenAPI documentation.
"""

from pydantic import BaseModel, Field
from typing import Optional


# ─── Request Models ──────────────────────────────────────────

class GenerateRequest(BaseModel):
    """Simple generation: describe the music you want, Suno writes lyrics automatically."""
    prompt: str = Field(..., description="Natural language description of desired music")
    make_instrumental: bool = Field(False, description="Generate instrumental only (no vocals)")
    model: str = Field("chirp-v3-5", description="Suno model name")

    model_config = {"json_schema_extra": {"examples": [{"prompt": "A happy pop song about sunshine", "make_instrumental": False, "model": "chirp-v3-5"}]}}


class CustomGenerateRequest(BaseModel):
    """Custom generation: provide full lyrics, style tags, and title."""
    prompt: str = Field(..., description="Full lyrics text (with markers like [Verse], [Chorus])")
    tags: str = Field(..., description="Music genre/style tags (e.g. 'pop metal male melancholic')")
    title: str = Field(..., description="Song title")
    negative_tags: str = Field("", description="Styles to avoid")
    make_instrumental: bool = Field(False, description="Generate instrumental only (no vocals)")
    model: str = Field("chirp-v3-5", description="Suno model name")

    model_config = {"json_schema_extra": {"examples": [{"prompt": "[Verse]\nHello world\n[Chorus]\nLa la la", "tags": "pop, upbeat", "title": "Hello World", "negative_tags": "", "make_instrumental": False, "model": "chirp-v3-5"}]}}


class ExtendAudioRequest(BaseModel):
    """Extend an existing audio clip from a specific timestamp."""
    audio_id: str = Field(..., description="Suno clip ID to extend")
    prompt: str = Field("", description="Continuation lyrics/prompt")
    continue_at: float = Field(0, description="Timestamp in seconds to start extension from")
    tags: str = Field("", description="Style tags for the extension")
    negative_tags: str = Field("", description="Styles to avoid")
    title: str = Field("", description="Title for the extended clip")
    model: str = Field("chirp-v3-5", description="Suno model name")


class ConcatRequest(BaseModel):
    """Concatenate extension clips into one complete song."""
    clip_id: str = Field(..., description="Clip ID to concatenate")


class LyricsRequest(BaseModel):
    """Generate lyrics from a natural language prompt."""
    prompt: str = Field(..., description="Description of desired lyrics")

    model_config = {"json_schema_extra": {"examples": [{"prompt": "A love song about the ocean"}]}}


# ─── Response Models ─────────────────────────────────────────

class AudioInfo(BaseModel):
    """Suno audio clip information."""
    id: str
    title: Optional[str] = None
    image_url: Optional[str] = None
    audio_url: Optional[str] = None
    video_url: Optional[str] = None
    status: str = Field(..., description="Clip status: submitted, queued, streaming, complete, error")
    duration: Optional[float] = None
    model_name: Optional[str] = None
    tags: Optional[str] = None
    prompt: Optional[str] = None
    gpt_description_prompt: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    lyric: Optional[str] = None


class CreditsInfo(BaseModel):
    """Suno account billing/credits information."""
    credits_left: int
    period: Optional[str] = None
    monthly_limit: Optional[int] = None
    monthly_usage: Optional[int] = None


class LyricsResponse(BaseModel):
    """Generated lyrics response."""
    id: Optional[str] = None
    text: str
    title: str
    status: str


class WavUrlResponse(BaseModel):
    """WAV file CDN URL. May be null if conversion is still in progress."""
    wav_file_url: Optional[str] = None


class MessageResponse(BaseModel):
    """Simple status message response."""
    status: int = 200
    message: str


class SunoModel(BaseModel):
    """A Suno generation model."""
    external_key: str = Field(..., description="Model key used in API calls (e.g. 'chirp-crow')")
    name: str = Field(..., description="Display name (e.g. 'v5')")
    description: str = Field("", description="Model description")
    major_version: int = Field(0, description="Major version number")
    is_default: bool = Field(False, description="Whether this is the current default model")
    is_default_free: bool = Field(False, description="Whether this is the default free model")
    badges: list[str] = Field(default_factory=list, description="Model badges (e.g. 'pro', 'beta')")
    can_use: bool = Field(True, description="Whether the current account can use this model")
    max_prompt_length: int = Field(3000, description="Max prompt/lyrics character length")
    max_tags_length: int = Field(200, description="Max tags character length")
    capabilities: list[str] = Field(default_factory=list, description="Model capabilities (generate, extend, cover, etc.)")
    features: list[str] = Field(default_factory=list, description="Model features")


# ─── Fallback Models (used when API is unreachable) ─────────

FALLBACK_MODELS: list[dict] = [
    {"external_key": "chirp-crow", "name": "v5", "description": "Authentic vocals, superior audio quality", "major_version": 5, "is_default": True, "badges": ["pro", "beta"]},
    {"external_key": "chirp-bluejay", "name": "v4.5+", "description": "Advanced creation methods", "major_version": 5, "is_default": False, "badges": ["pro"]},
    {"external_key": "chirp-auk", "name": "v4.5", "description": "Intelligent prompts", "major_version": 5, "is_default": False, "badges": ["pro"]},
    {"external_key": "chirp-auk-turbo", "name": "v4.5-all", "description": "Best free model", "major_version": 5, "is_default": False, "is_default_free": True},
    {"external_key": "chirp-v4", "name": "v4", "description": "Improved sound quality", "major_version": 4, "is_default": False, "badges": ["pro"]},
    {"external_key": "chirp-v3-5", "name": "v3.5", "description": "Basic song structure", "major_version": 3, "is_default": False},
]
