from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


# ---------------------------------------------------------------------------
# Custom exception hierarchy
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Base application error."""

    status_code: int = 500
    detail: str = "Internal server error"

    def __init__(self, detail: str | None = None) -> None:
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class NotFoundError(AppError):
    """Resource not found → HTTP 404."""

    status_code = 404
    detail = "Resource not found"


class ConflictError(AppError):
    """Unique constraint violation → HTTP 409."""

    status_code = 409
    detail = "Resource conflict"


class ValidationError(AppError):
    """Business rule violation → HTTP 422."""

    status_code = 422
    detail = "Validation failed"


# ---------------------------------------------------------------------------
# Exception handler registration
# ---------------------------------------------------------------------------


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
