"""Public API models (request / response / errors)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictStr, model_validator
from pydantic_core import PydanticCustomError

from app.services.validation import MessageValidationError, validate_message

EXAMPLE_MESSAGE = "Check out our latest business platform and grow your online visibility. https://example.com"


class PostRequest(BaseModel):
    """Body of ``POST /posting``. Only ``message`` is accepted; platform selection is internal."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"message": EXAMPLE_MESSAGE}]})

    message: StrictStr = Field(
        description="1-200 characters after trimming, containing exactly one http(s) CTA link.",
    )
    _link: str = PrivateAttr(default="")

    @model_validator(mode="after")
    def _validate_message(self) -> PostRequest:
        try:
            validated = validate_message(self.message)
        except MessageValidationError as exc:
            # "{reason}" template + context keeps braces inside URLs from being treated as format fields.
            raise PydanticCustomError(exc.code, "{reason}", {"reason": exc.message}) from None
        self.message = validated.text
        self._link = validated.link
        return self

    @property
    def link(self) -> str:
        return self._link


class PlatformResult(BaseModel):
    platform: str = Field(examples=["Mastodon"])
    status: Literal["success", "failed"]
    posted_url: str | None = Field(description="Public URL of the post; null on failure.")
    public_access: bool = Field(
        description="true only if an unauthenticated request confirmed the post is public. "
        "false means 'not verified' (failed, verification unavailable, or not yet visible).",
    )
    date: str | None = Field(description="YYYY-MM-DD at the moment of publication (APP_TIMEZONE); null on failure.")
    time: str | None = Field(description="HH:MM:SS at the moment of publication (APP_TIMEZONE); null on failure.")
    error: str | None = Field(description="'<CODE>: <message>' on failure, null on success.")


class Summary(BaseModel):
    total: int
    succeeded: int
    failed: int


class PostResponse(BaseModel):
    success: bool = Field(description="true if at least one platform succeeded.")
    summary: Summary
    results: list[PlatformResult]


class ErrorDetail(BaseModel):
    field: str
    code: str
    message: str


class ValidationErrorResponse(BaseModel):
    success: Literal[False] = False
    error: str = "Validation failed"
    details: list[ErrorDetail]


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    error: str


class PlatformInfo(BaseModel):
    key: str
    name: str
    configured: bool
    missing_env: list[str]
    public_verification: bool


class PlatformsResponse(BaseModel):
    dry_run: bool
    platforms: list[PlatformInfo]
