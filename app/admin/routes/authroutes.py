from fastapi import APIRouter, Request
from app.core.schema.schemarespone import APIResponse
from app.core.schema.schema import RegisterRequest , LoginRequest
from app.admin.controller.authcontroller import AuthController
adminauthrouter = APIRouter(
    prefix="/auth",
    tags=["auth"],
)

@adminauthrouter.post("/register", response_model=APIResponse)
async def register(body: RegisterRequest, http_request: Request):
    return await AuthController.register_user(body, http_request) 

@adminauthrouter.post("/login", response_model=APIResponse)
async def login(body: LoginRequest, http_request: Request):
    ip = http_request.headers.get("X-Forwarded-For") or (http_request.client.host if http_request.client else None)
    return await AuthController.login_user(body, ip)


@adminauthrouter.post("/logout", response_model=APIResponse)
async def logout(http_request: Request):
    ip = http_request.headers.get("X-Forwarded-For") or (http_request.client.host if http_request.client else None)
    return await AuthController.logout_user(ip) 
