import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.constants import ERROR_REPORT_MAX_RESPONSE_BYTES, ERROR_REPORT_MAX_TRACE_BYTES


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """
    Truncate a string so its UTF-8 encoding fits within `max_bytes`, cutting on a codepoint boundary.

    :param text: The string to truncate
    :param max_bytes: The maximum number of UTF-8 bytes to keep
    :return: The (possibly truncated) string and whether truncation happened
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    # Decoding with errors="ignore" drops the partial codepoint left at the cut, if any.
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


# Body for `POST /api/v1/error-reports/`
class ErrorReportIn(BaseModel):
    """A client-side error report submitted by the mobile app."""

    client_event_id: uuid.UUID
    occurred_at: datetime
    platform: Literal["android", "ios"]
    app_version: str = Field(max_length=64)
    app_build: str = Field(max_length=64)
    device_brand: str | None = Field(default=None, max_length=64)
    device_model: str | None = Field(default=None, max_length=128)
    os_version: str | None = Field(default=None, max_length=64)
    screen: str | None = Field(default=None, max_length=128)
    operation: str | None = Field(default=None, max_length=128)
    had_cached_data: bool | None = None
    error_kind: Literal["http", "network", "timeout", "parsing", "database"]
    error_type: str = Field(max_length=256)
    error_message: str = Field(max_length=4096)
    stack_trace: str | None = None
    request_method: str | None = Field(default=None, max_length=16)
    request_path: str | None = Field(default=None, max_length=512)
    request_query: dict[str, str] | None = None
    response_status: int | None = Field(default=None, ge=100, le=599)
    response_body: str | None = None
    backend_request_id: str | None = Field(default=None, max_length=128)
    trace_truncated: bool = False
    response_truncated: bool = False

    @field_validator("occurred_at")
    @classmethod
    def validate_aware(cls, value: datetime) -> datetime:
        """Reject naive timestamps - without an offset we cannot place the client event on a timeline."""
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("must be timezone-aware")
        return value

    @model_validator(mode="after")
    def enforce_size_caps(self) -> Self:
        """
        Server-side truncation: oversized blobs are cut to the cap and the matching flag is forced on,
        so a client that lies about (or omits) the flag cannot claim an untruncated payload.
        """
        if self.stack_trace is not None:
            self.stack_trace, truncated = _truncate_utf8(self.stack_trace, ERROR_REPORT_MAX_TRACE_BYTES)
            if truncated:
                self.trace_truncated = True
        if self.response_body is not None:
            self.response_body, truncated = _truncate_utf8(self.response_body, ERROR_REPORT_MAX_RESPONSE_BYTES)
            if truncated:
                self.response_truncated = True
        return self


# Response for `POST /api/v1/error-reports/`
class ErrorReportResponse(BaseModel):
    """Acknowledgement that a report was captured; `id` echoes the client's event id."""

    id: uuid.UUID
    received_at: datetime
