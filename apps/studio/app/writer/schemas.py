"""Pydantic models for the Writer Factory pipeline.

These models double as JSON Schema definitions sent to Gemini's
structured-output mode — anything the model emits must validate or
the pipeline retries / falls back. `model_config = ConfigDict(extra='forbid')`
is deliberate: Gemini occasionally adds fields like "explanation" or
"confidence_score"; we treat that as a schema violation rather than
silently dropping them, because it usually means the prompt drift is
already underway.
"""
from pydantic import BaseModel, ConfigDict, Field


class ClimaxMoment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp_s: float = Field(
        ge=0.0,
        description="Seconds from video start to the most memorable moment.",
    )
    what: str = Field(
        min_length=1,
        description="Concrete description of what happens at that timestamp.",
    )


class VideoFacts(BaseModel):
    """Layer 1 output. Strict schema — every field is required.

    Empty lists are valid (some videos have no spoken words / no props),
    but the field must be present. Gemini learns to populate everything
    when the schema enforces it.
    """
    model_config = ConfigDict(extra="forbid")

    opening_2s: str = Field(
        min_length=1,
        description="What literally happens in the first 2 seconds.",
    )
    climax_moment: ClimaxMoment = Field(
        description="The single most memorable / attention-grabbing "
                    "moment in the video. Pick exactly one.",
    )
    on_screen_text: list[str] = Field(
        description="Verbatim text overlaid on the video (subtitles, "
                    "captions, on-screen graphics). Empty list if none.",
    )
    spoken_verbatim: list[str] = Field(
        description="Short verbatim quotes from the audio. Empty list "
                    "if the video is silent or non-verbal.",
    )
    subject_expression_at_climax: str = Field(
        min_length=1,
        description="Facial expression / body language at the climax "
                    "moment. 'neutral' is a valid value.",
    )
    visible_props: list[str] = Field(
        description="Concrete objects, brands, locations visible "
                    "on screen. Empty list if none stand out.",
    )
    unique_moments: list[str] = Field(
        description="Any moment that is specifically memorable beyond "
                    "the climax. Empty list if nothing else stands out.",
    )
    vibe: str = Field(
        min_length=1,
        description="One-line aesthetic / mood description.",
    )
