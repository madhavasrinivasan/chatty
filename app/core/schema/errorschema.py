from fastapi import Request
from fastapi.responses import JSONResponse
from app.core.schema.applicationerror import ApplicationError


def register_exception_handlers(app):
    """Register all exception handlers for the FastAPI app"""
    
    @app.exception_handler(ApplicationError)
    async def application_error_handler(request: Request, exc: ApplicationError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status": 400,
                "error": {
                    "type": exc.type,
                    "message": exc.message,
                    "description": exc.discription,
                    "errors": exc.errors,
                },
            }
        )

