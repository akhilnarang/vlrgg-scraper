import logging

import httpx2
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exception_handlers import http_exception_handler

logger = logging.getLogger(__name__)


class NotFoundError(HTTPException):
    def __init__(self, detail: str = "Not found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class BadRequestError(HTTPException):
    def __init__(self, detail: str = "Bad request"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class UnauthorizedError(HTTPException):
    def __init__(self, detail: str = "Unauthorized"):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


class ConflictError(HTTPException):
    def __init__(self, detail: str = "Conflict"):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class InternalServerError(HTTPException):
    def __init__(self, detail: str = "Internal server error"):
        super().__init__(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


class ServiceUnavailableError(HTTPException):
    def __init__(self, detail: str = "Service unavailable"):
        super().__init__(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


class ScrapingError(HTTPException):
    def __init__(
        self,
        detail: str = "VLR.gg server returned an error",
        *,
        url: str = "",
        upstream_status: int = 0,
    ):
        super().__init__(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
        self.url = url
        self.upstream_status = upstream_status


class RateLimitError(HTTPException):
    def __init__(self, detail: str = "Rate limit exceeded", *, retry_after: int | None = None):
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        super().__init__(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail, headers=headers)


async def vlr_unreachable_handler(request: Request, exc: Exception) -> Response:
    """Answer 503 when VLR.gg can't be reached (DNS failure, refused connection, timeout)."""
    logger.error("VLR.gg is unreachable: %r", exc)
    return await http_exception_handler(request, ServiceUnavailableError("VLR.gg is unreachable"))


def register_exception_handlers(app: FastAPI) -> None:
    """Register application-wide exception handlers.

    :param app: FastAPI instance.
    :return: None.
    """
    app.add_exception_handler(httpx2.TransportError, vlr_unreachable_handler)
