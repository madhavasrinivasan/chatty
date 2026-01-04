from fastapi import APIRouter 

adminapprouter = APIRouter(
    prefix="/app",
    tags=["app"],
)

@adminapprouter.get("/user")
async def get_user():
    return {"message": "App successful"}