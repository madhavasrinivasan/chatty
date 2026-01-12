from fastapi import APIRouter 
from app.core.schema.schemarespone import APIResponse
from app.core.schema.schema import RegisterRequest , LoginRequest
from app.admin.controller.authcontroller import AuthController
adminauthrouter = APIRouter(
    prefix="/auth",
    tags=["auth"],
)

@adminauthrouter.post("/register", response_model=APIResponse)
async def register(request: RegisterRequest):
    return await AuthController.register_user(request) 

@adminauthrouter.post("/login", response_model=APIResponse)
async def login(request: LoginRequest):
    ip = request.client.host
   
    
    return await AuthController.login_user(request, ip)

