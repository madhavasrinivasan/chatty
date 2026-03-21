import asyncio
from fastapi import APIRouter, Depends, Request, BackgroundTasks, Form, File, UploadFile, HTTPException
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
from app.core.models.models import ChatSession, ChatMessage
from typing import List, Optional
import uuid as _uuid
from uuid import UUID


CHAT_HISTORY_MAX = 10
CUSTOMER_EMAIL_HEADER = "chatty-customer-email"
SHOP_DOMAIN_HEADER = "x-shop-domain"
CUSTOMER_EMAIL_HEADER_ALT = "x-customer-email"
CART_TOKEN_HEADER = "x-cart-token"


def _normalize_shop_domain_header(value: str) -> str:
    host = (value or "").strip().replace("https://", "").replace("http://", "").split("/")[0]
    if not host:
        return ""
    if host.endswith(".myshopify.com"):
        return host
    return f"{host.split('.')[0]}.myshopify.com"


async def get_or_create_session(
    request: Request,
    *,
    provided_session_id: Optional[str] = None,
) -> str:
    """
    Resolve an active ChatSession based on headers:
    - x-shop-domain
    - x-customer-email (fallback: chatty-customer-email)
    - x-cart-token
    If `provided_session_id` is present, prefer that session (create it if missing).
    Returns session_id as a string.
    """
    shop_domain = _normalize_shop_domain_header(
        (request.headers.get(SHOP_DOMAIN_HEADER) or request.headers.get("chatty-shop-url") or "").strip()
    )
    customer_email = (request.headers.get(CUSTOMER_EMAIL_HEADER_ALT) or request.headers.get(CUSTOMER_EMAIL_HEADER) or "").strip() or None
    cart_token = (request.headers.get(CART_TOKEN_HEADER) or request.headers.get("chatty-cart-token") or "").strip() or None

    # If frontend provides a session_id, prefer it.
    if provided_session_id:
        try:
            sid = UUID(provided_session_id)
            existing = await ChatSession.filter(id=sid).first()
            if existing:
                # Touch updated_at by updating status (Tortoise updates auto_now on update)
                await ChatSession.filter(id=sid).update(status=existing.status)
                return str(existing.id)
            if not shop_domain:
                # Can't create a session without knowing the shop domain.
                raise ValueError("Missing shop_domain for ChatSession creation")
            created = await ChatSession.create(
                id=sid,
                shop_domain=shop_domain,
                customer_email=customer_email,
                cart_token=cart_token,
                status="active",
            )
            return str(created.id)
        except Exception:
            # Fall through to header-based creation.
            pass

    if not shop_domain:
        raise HTTPException(status_code=400, detail="Missing x-shop-domain header (or chatty-shop-url fallback)")

    # Find active session matching the user/cart identifiers available.
    q = ChatSession.filter(shop_domain=shop_domain, status="active")
    if customer_email and cart_token:
        q = q.filter(customer_email=customer_email, cart_token=cart_token)
    elif customer_email:
        q = q.filter(customer_email=customer_email)
    elif cart_token:
        q = q.filter(cart_token=cart_token)
    else:
        # Without any identity headers, always create a fresh session.
        created = await ChatSession.create(
            shop_domain=shop_domain,
            customer_email=None,
            cart_token=None,
            status="active",
        )
        return str(created.id)

    existing = await q.order_by("-updated_at").first()
    if existing:
        await ChatSession.filter(id=existing.id).update(status=existing.status)
        return str(existing.id)

    created = await ChatSession.create(
        shop_domain=shop_domain,
        customer_email=customer_email,
        cart_token=cart_token,
        status="active",
    )
    return str(created.id)

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
    # Resolve/create a persisted chat session from headers/front-end session_id.
    resolved_session_id = await get_or_create_session(
        http_request, provided_session_id=(body.session_id or None)
    )
    session_uuid = UUID(resolved_session_id)

    # Persist the user's incoming message first.
    await ChatMessage.create(
        session_id=session_uuid,
        role="user",
        content=body.message,
    )

    # Load last N messages from DB for LLM context (chronological order).
    recent_msgs = await ChatMessage.filter(session_id=session_uuid).order_by("-created_at").limit(CHAT_HISTORY_MAX)
    recent_msgs_list = list(recent_msgs)[::-1]
    chat_history = [{"role": m.role, "content": m.content} for m in recent_msgs_list]

    customer_email = (
        http_request.headers.get(CUSTOMER_EMAIL_HEADER_ALT)
        or http_request.headers.get(CUSTOMER_EMAIL_HEADER)
        or ""
    ).strip()

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
        session_id=resolved_session_id,
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

    api_resp = await AppController.process_orchestrator_query(user, orchestrator_request)

    # Persist assistant message after response generation.
    try:
        result = api_resp.data or {}
        final_response = result.get("final_response") or {}
        assistant_text = (final_response.get("general_answer") or "").strip()
        if assistant_text:
            await ChatMessage.create(
                session_id=session_uuid,
                role="assistant",
                content=assistant_text,
            )
    except Exception:
        # Never fail the chat request due to persistence errors.
        pass

    return api_resp


@adminapprouter.post("/chat", response_model=APIResponse)
async def process_chat_alias(
    http_request: Request,
    body: OrchestratorRequest,
    user: dict = Depends(AppController.validate_chatbot_api_key),
):
    """Alias for /orchestrate/chatbot."""
    return await process_orchestrate_chatbot(http_request=http_request, body=body, user=user)

@adminapprouter.get("/shopify-callback", response_model=APIResponse)
async def shopify_callback(request:Request):
    return await AppController.shopify_callback(request) 

@adminapprouter.post("/addshoppify",response_model=APIResponse)
async def addshoppify(request:AddshopifyRequest,user:dict = Depends(AppController.validate_user)):
    return await AppController.addshopify(request,user)

# @adminapprouter.post("/getproducts",response_model=APIResponse)
# async def get_products(request:Request,user:dict)



