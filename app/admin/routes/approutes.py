from fastapi import APIRouter , Depends , Request
from app.admin.controller.appcontroller import AppController
from app.core.schema.schemarespone import APIResponse
adminapprouter = APIRouter(
    prefix="/app",
    tags=["app"],
)

@adminapprouter.get("/user", response_model=APIResponse)
async def get_user(user: dict = Depends(AppController.validate_user)):
    return APIResponse(
        success=True,
        message="App successful",
        data=user
    ) 

