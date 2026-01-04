from fastapi import APIRouter 

adminauthrouter = APIRouter(
    prefix="/auth",
    tags=["auth"],
)

@adminauthrouter.post("/login")
async def login(request: LoginRequest):
    return {"message": "Login successful"}