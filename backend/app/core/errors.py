from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_response(*, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "message": message})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, error: AppError) -> JSONResponse:
        return _error_response(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            status_code=422,
            code="REQUEST_VALIDATION_FAILED",
            message="Request validation failed",
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request: Request, error: StarletteHTTPException) -> JSONResponse:
        if error.status_code == 404:
            return _error_response(
                status_code=404,
                code="ROUTE_NOT_FOUND",
                message="Route not found",
            )
        return _error_response(
            status_code=error.status_code,
            code="HTTP_ERROR",
            message="Request failed",
        )
