import asyncio
from fastapi import APIRouter, Depends, Request, BackgroundTasks, Form, File, UploadFile
from app.admin.controller.appcontroller import AppController
from app.core.schema.schemarespone import APIResponse
from app.core.services.filehandler import FileHandler
from app.core.schema.schema import UploadKnowledgeBaseRequest, AddshopifyRequest, OrchestratorRequest
from app.core.schema.schema import llmrequest, llmresponse
from app.core.services.session_memory import (
    get_user_facts_for_context,
    get_previous_session_summary_for_context,
    get_order_history_for_context,
)
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from typing import List, Optional

CHAT_HISTORY_MAX = 10
CUSTOMER_EMAIL_HEADER = "chatty-customer-email"

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
async def get_response(request: llmrequest, user: dict = Depends(AppController.validate_user)):
    return await AppController.get_response(user, request)

@adminapprouter.post("/orchestrate", response_model=APIResponse)
async def process_orchestrate(request: OrchestratorRequest, user: dict = Depends(AppController.validate_user)):
    """Run the AI E-Commerce Orchestrator (IntentRouter + QueryExpander). Returns route and, for HYBRID, the expanded search payload."""
    return await AppController.process_orchestrator_query(user, request)

@adminapprouter.post("/orchestrate/chatbot", response_model=APIResponse)
async def process_orchestrate_chatbot(
    http_request: Request,
    body: OrchestratorRequest,
    user: dict = Depends(AppController.validate_chatbot_api_key),
):
    """
    Main chat route for chatbot/frontend: auth via x-api-key. Slices chat to last 10,
    reads chatty-customer-email, fetches user_facts / previous_session_history / order_history
    concurrently when email present, then runs orchestrator with memory.
    """
    chat_history = list(body.chat_history or [])[-CHAT_HISTORY_MAX:]
    customer_email = (http_request.headers.get(CUSTOMER_EMAIL_HEADER) or "").strip()

    store = None
    chatbot_id = user.get("chatbot_id")
    if chatbot_id:
        store = await AdminDbContoller().find_one_ecom_store(chatbot_id)
    if store is None and user.get("id"):
        store = await AdminDbContoller().find_first_ecom_store_by_user_id(user["id"])

    user_facts = ""
    previous_session_history = ""
    order_history = ""
    if customer_email and store:
        user_facts, previous_session_history, order_history = await asyncio.gather(
            get_user_facts_for_context(customer_email, store.id),
            get_previous_session_summary_for_context(customer_email, store.id),
            get_order_history_for_context(
                store.store_name or "",
                store.access_token or "",
                customer_email,
            ),
        )

    orchestrator_request = OrchestratorRequest(
        session_id=body.session_id,
        message=body.message,
        chat_history=chat_history,
        action_payload=body.action_payload,
        pre_fetched_orders=body.pre_fetched_orders or {},
        chatbot_id=body.chatbot_id or chatbot_id,
        subscription_plan=body.subscription_plan,
        user_facts=user_facts or None,
        order_history=order_history or None,
        previous_session_history=previous_session_history or None,
    )
    return await AppController.process_orchestrator_query(user, orchestrator_request)

@adminapprouter.get("/shopify-callback", response_model=APIResponse)
async def shopify_callback(request:Request):
    return await AppController.shopify_callback(request) 

@adminapprouter.post("/addshoppify",response_model=APIResponse)
async def addshoppify(request:AddshopifyRequest,user:dict = Depends(AppController.validate_user)):
    return await AppController.addshopify(request,user)

# @adminapprouter.post("/getproducts",response_model=APIResponse)
# async def get_products(request:Request,user:dict)



