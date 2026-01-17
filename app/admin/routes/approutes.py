from fastapi import APIRouter , Depends , Request,BackgroundTasks,Form
from app.admin.controller.appcontroller import AppController
from app.core.schema.schemarespone import APIResponse
from app.core.services.filehandler import FileHandler
from app.core.schema.schema import UploadKnowledgeBaseRequest
from typing import List
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

@adminapprouter.post("/uploadknowlegdebase", response_model=APIResponse,)
async def upload_files(user: dict = Depends(AppController.validate_user),file_path:List[dict] = Depends(FileHandler.upload_file),request: UploadKnowledgeBaseRequest = Depends(UploadKnowledgeBaseRequest.as_form)):
    return await AppController.upload_knowledge_base(user,file_path,request)