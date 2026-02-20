from fastapi import APIRouter, Depends, Request, BackgroundTasks, Form, File, UploadFile
from app.admin.controller.appcontroller import AppController
from app.core.schema.schemarespone import APIResponse
from app.core.services.filehandler import FileHandler
from app.core.schema.schema import UploadKnowledgeBaseRequest
from app.core.schema.schema import llmrequest , llmresponse
from typing import List, Optional

adminapprouter = APIRouter(
    prefix="/app",
    tags=["app"],
)

async def get_uploaded_files(files: Optional[List[UploadFile]] = File(None)):
    if files is None or (isinstance(files, list) and len(files) == 0):
        return None
    file_handler = FileHandler()
    return await file_handler.upload_file(files)

@adminapprouter.get("/user", response_model=APIResponse)
async def get_user(user: dict = Depends(AppController.validate_user)):
    return APIResponse(
        success=True,
        message="App successful",
        data=user
    ) 

@adminapprouter.post("/uploadknowlegdebase", response_model=APIResponse)
async def upload_files(
    background_tasks: BackgroundTasks,
    user: dict = Depends(AppController.validate_user),
    file_path: Optional[List[dict]] = Depends(get_uploaded_files),
    name: str = Form(...),
    urls: Optional[str] = Form(None)
): 
    request = UploadKnowledgeBaseRequest.as_form(
        chatbot_id=None,
        name=name,
        urls=urls
    )
    return await AppController.upload_knowledge_base(user, file_path, request, background_tasks) 

@adminapprouter.post("/response", response_model=APIResponse)
async def get_response(request:llmrequest, user: dict = Depends(AppController.validate_user)):
    return await AppController.get_response(user, request) 

@adminapprouter.get("/shopify-callback",response_model=APIResponse)
async def shopify_callback(request:Request):
    return await AppController.shopify_callback(request) 




