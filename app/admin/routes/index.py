from app.admin.controller.appcontroller import AppController
from app.admin.routes.authroutes import adminauthrouter
from app.admin.routes.approutes import adminapprouter
from app.admin.routes.sessionroutes import sessionrouter
from app.core.schema.schemarespone import APIResponse
from fastapi import APIRouter, BackgroundTasks, Depends


approuter = APIRouter(
    prefix="/admin",
    tags=["admin"],
) 

approuter.include_router(adminauthrouter);
approuter.include_router(adminapprouter);
approuter.include_router(sessionrouter);


@approuter.get("/sessions", response_model=APIResponse)
async def list_sessions(user: dict = Depends(AppController.validate_user)):
    return await AppController.list_chat_sessions(user)


@approuter.get("/sessions/{session_id}/messages", response_model=APIResponse)
async def get_session_messages(session_id: str, user: dict = Depends(AppController.validate_user)):
    return await AppController.list_chat_session_messages(user, session_id)


@approuter.get("/sync-status", response_model=APIResponse)
async def get_sync_status(user: dict = Depends(AppController.validate_user)):
    return await AppController.get_sync_status(user)


@approuter.post("/sync-trigger", response_model=APIResponse)
async def sync_trigger(background_tasks: BackgroundTasks, user: dict = Depends(AppController.validate_user)):
    return await AppController.trigger_sync(user, background_tasks)

