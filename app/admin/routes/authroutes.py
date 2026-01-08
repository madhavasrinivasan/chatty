from fastapi import APIRouter 
from app.core.schema.schemarespone import APIResponse
from app.core.schema.schema import RegisterRequest , LoginRequest
from app.admin.controller.authcontroller import AuthCntroller
adminauthrouter = APIRouter(
    prefix="/auth",
    tags=["auth"],
)

@adminauthrouter.post("/register", response_model=APIResponse)
async def register(request: RegisterRequest):
    return await AuthCntroller.register_user(request) 

