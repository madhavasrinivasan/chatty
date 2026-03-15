from fastapi import APIRouter, BackgroundTasks, Depends

from app.admin.controller.appcontroller import AppController
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import SyncSessionRequest
from app.core.services.session_memory import process_and_save_session

sessionrouter = APIRouter(
    prefix="/session",
    tags=["api"],
)


@sessionrouter.post("/sync")
async def sync_session(
    payload: SyncSessionRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(AppController.validate_chatbot_api_key),
):
    """
    Queue chat session for background processing. Requires x-api-key header; decodes it to get
    chatbot_id, resolves ecom_store to get store_id, and uses that for transcript and chat summary/fact extraction.
    """
    chatbot_id = user.get("chatbot_id")
    store = None
    if chatbot_id:
        store = await AdminDbContoller().find_one_ecom_store(chatbot_id)
    store_id = store.id if store else None
    payload_with_store = SyncSessionRequest(
        session_id=payload.session_id,
        store_id=store_id,
        user_email=payload.user_email,
        chat_history=payload.chat_history,
    )
    background_tasks.add_task(process_and_save_session, payload_with_store)
    return {"status": "success", "message": "Session queued for background processing"}
