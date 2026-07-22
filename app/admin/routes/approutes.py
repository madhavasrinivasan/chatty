import asyncio
from fastapi import APIRouter, Depends, Request, BackgroundTasks, Form, File, UploadFile, HTTPException
from app.admin.controller.appcontroller import AppController
from app.core.schema.schemarespone import APIResponse
from app.core.services.filehandler import FileHandler
from app.core.schema.schema import (
    UploadKnowledgeBaseRequest, AddshopifyRequest, OrchestratorRequest,
    LoadFirstConvoRequest, LoadFirstConvoResponse,
    ChatbotCustomizationResponse, ChatbotCustomizationUpdate, KnowledgeSummary,
    AddToCartTrackRequest,
)
from app.core.schema.schema import llmrequest, llmresponse
from app.core.services.first_conversation import generate_first_conversation_greeting
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


def _message_text_for_llm(m: ChatMessage) -> str:
    """Build a single string for LLM chat_history from stored user text or assistant payload."""
    if (m.role or "").lower() == "user":
        return (m.content or "").strip()
    payload = getattr(m, "payload", None)
    if isinstance(payload, dict):
        return str(payload.get("general_answer") or "").strip()
    return (m.content or "").strip()


def _final_response_to_dict(final_response) -> dict:
    if final_response is None:
        return {}
    if hasattr(final_response, "model_dump"):
        return final_response.model_dump(mode="json")
    if isinstance(final_response, dict):
        return dict(final_response)
    return {}


adminapprouter = APIRouter(
    prefix="/app",
    tags=["app"],
)


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


async def get_uploaded_files(files: Optional[List[UploadFile]] = File(None)):
    if files is None:
        return None
    # FastAPI may pass a single UploadFile when only one file is sent
    if not isinstance(files, list):
        files = [files]
    if len(files) == 0:
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


@adminapprouter.get("/store-token-usage", response_model=APIResponse)
async def get_store_token_usage(user: dict = Depends(AppController.validate_chatbot_api_key)):
    """
    Estimated total tokens over all `store_knowledge` rows for this merchant's store (tiktoken).
    Auth: same as chatbot — `x-api-key` (or `chatty-api-key`) via `validate_chatbot_api_key`.
    """
    return await AppController.get_store_token_usage(user)


@adminapprouter.get("/usage", response_model=APIResponse)
async def get_llm_usage(user: dict = Depends(AppController.validate_user)):
    """Cumulative estimated LLM usage (input/output tokens + cost) for the merchant's store."""
    return await AppController.get_merchant_llm_usage(user)


@adminapprouter.get("/dashboard-stats", response_model=APIResponse)
async def get_dashboard_stats(user: dict = Depends(AppController.validate_user)):
    """
    Overview dashboard metrics:
    add-to-cart clicks, attributed ATC revenue, queries/questions answered, tokens + cost.
    """
    return await AppController.get_dashboard_stats(user)


@adminapprouter.post("/analytics/add-to-cart", response_model=APIResponse)
async def track_add_to_cart(
    body: AddToCartTrackRequest,
    user: dict = Depends(AppController.validate_chatbot_api_key),
):
    """
    Widget: log a successful Add to cart from a product card.
    Auth: x-api-key / chatty-api-key (same as chatbot).
    """
    return await AppController.track_add_to_cart(user, body)


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

@adminapprouter.post("/load-first-convo", response_model=APIResponse)
async def load_first_convo(
    http_request: Request,
    body: LoadFirstConvoRequest,
    user: dict = Depends(AppController.validate_chatbot_api_key),
):
    """
    Widget open: return a personalized sales greeting when customer email is known.
    Headers: x-api-key, x-shop-domain, x-customer-email (optional), x-cart-token (optional).
    """
    from app.core.config.gemini_client import configure_gemini_env

    configure_gemini_env()

    resolved_session_id = await get_or_create_session(
        http_request, provided_session_id=(body.session_id or None)
    )
    session_uuid = UUID(resolved_session_id)

    cart_token = (
        (http_request.headers.get(CART_TOKEN_HEADER) or http_request.headers.get("chatty-cart-token") or "")
        .strip()
        or (body.cart_token or "").strip()
        or None
    )
    customer_email = (
        http_request.headers.get(CUSTOMER_EMAIL_HEADER_ALT)
        or http_request.headers.get(CUSTOMER_EMAIL_HEADER)
        or ""
    ).strip() or None

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
            get_previous_session_summary_for_context(customer_email, store.id, exclude_session_id=resolved_session_id),
            get_order_history_for_context(
                store.store_name or "",
                store.access_token or "",
                customer_email,
                limit=10,
            ),
        )

    # Save to ChatSession cache
    update_fields = {}
    if customer_email:
        update_fields["customer_email"] = customer_email
    if cart_token:
        update_fields["cart_token"] = cart_token
    if customer_email and store:
        update_fields["prefetched_user_facts"] = user_facts
        update_fields["prefetched_previous_session_history"] = previous_session_history
        update_fields["prefetched_order_history"] = order_history

    if update_fields:
        await ChatSession.filter(id=session_uuid).update(**update_fields)

    store_dna = (getattr(store, "store_dna", None) or "") if store else ""

    greeting, suggested_actions, personalized, context_used = await generate_first_conversation_greeting(
        store_dna=store_dna,
        shop_domain=(store.store_name or "") if store else "",
        access_token=(store.access_token or "") if store else "",
        customer_email=customer_email,
        cart_token=cart_token,
        chat_history=body.chat_history or [],
        store_id=store.id if store else None,
        exclude_session_id=resolved_session_id,
        prefetched_user_facts=user_facts or None,
        prefetched_previous_session_history=previous_session_history or None,
        prefetched_order_history=order_history or None,
        cart_items=body.cart_items or [],
    )


    payload = LoadFirstConvoResponse(
        session_id=resolved_session_id,
        personalized=personalized,
        general_answer=greeting,
        suggested_actions=suggested_actions,
        context_used=context_used,
    )

    try:
        await ChatMessage.create(
            session_id=session_uuid,
            role="assistant",
            content=greeting,
            payload=payload.model_dump(mode="json"),
        )
    except Exception as e:
        print(f"load_first_convo persist greeting error: {e}")

    return APIResponse(
        status=200,
        message="First conversation loaded.",
        data=payload.model_dump(mode="json"),
    )


@adminapprouter.post("/orchestrate/chatbot", response_model=APIResponse)
async def process_orchestrate_chatbot(
    http_request: Request,
    body: OrchestratorRequest,
    user: dict = Depends(AppController.validate_chatbot_api_key),
):
    import time as _time
    _t_start = _time.perf_counter()

    resolved_session_id = await get_or_create_session(
        http_request, provided_session_id=(body.session_id or None)
    )
    session_uuid = UUID(resolved_session_id)
   
    cart_token = (
        (http_request.headers.get(CART_TOKEN_HEADER) or http_request.headers.get("chatty-cart-token") or "").strip()
        or (body.cart_token or "").strip()
        or None
    )
    print(f"cart_token: {cart_token}")

    customer_email = (
        http_request.headers.get(CUSTOMER_EMAIL_HEADER_ALT)
        or http_request.headers.get(CUSTOMER_EMAIL_HEADER)
        or ""
    ).strip()
    print(f"customer_email: {customer_email}")

    # Link session to customer and/or update cart token
    update_fields = {}
    if customer_email:
        update_fields["customer_email"] = customer_email
    if cart_token:
        update_fields["cart_token"] = cart_token
    if update_fields:
        await ChatSession.filter(id=session_uuid).update(**update_fields)

    # Persist the user's incoming message first.
    await ChatMessage.create(
        session_id=session_uuid,
        role="user",
        content=body.message,
        payload=None,
    )

    _t_session = _time.perf_counter()
    print(f"⏱️ [TIMING] Session + headers + persist user msg: {_t_session - _t_start:.2f}s")

    # Load last N messages from DB for LLM context (chronological order).
    recent_msgs = await ChatMessage.filter(session_id=session_uuid).order_by("-created_at").limit(CHAT_HISTORY_MAX)
    recent_msgs_list = list(recent_msgs)[::-1]
    chat_history = [{"role": m.role, "content": _message_text_for_llm(m)} for m in recent_msgs_list]

    store = None
    chatbot_id = user.get("chatbot_id")
    if chatbot_id:
        store = await AdminDbContoller().find_one_ecom_store(chatbot_id)
    if store is None and user.get("id"):
        store = await AdminDbContoller().find_first_ecom_store_by_user_id(user["id"])

    # Fetch the session to check for prefetched cache
    session = await ChatSession.filter(id=session_uuid).first()
    
    user_facts = getattr(session, "prefetched_user_facts", None) if session else None
    previous_session_history = getattr(session, "prefetched_previous_session_history", None) if session else None
    order_history = getattr(session, "prefetched_order_history", None) if session else None

    # Fallback to fetching dynamically if not cached/prefetched in the session
    if customer_email and store:
        tasks = []
        need_update = False
        
        if not user_facts:
            tasks.append(get_user_facts_for_context(customer_email, store.id))
            need_update = True
        else:
            async def _dummy_facts(): return user_facts
            tasks.append(_dummy_facts())
            
        if not previous_session_history:
            tasks.append(get_previous_session_summary_for_context(customer_email, store.id, exclude_session_id=resolved_session_id))
            need_update = True
        else:
            async def _dummy_prev(): return previous_session_history
            tasks.append(_dummy_prev())
            
        if not order_history:
            tasks.append(get_order_history_for_context(store.store_name or "", store.access_token or "", customer_email))
            need_update = True
        else:
            async def _dummy_orders(): return order_history
            tasks.append(_dummy_orders())
            
        fetched_user_facts, fetched_prev_sess, fetched_orders = await asyncio.gather(*tasks)
        
        if not user_facts:
            user_facts = fetched_user_facts
        if not previous_session_history:
            previous_session_history = fetched_prev_sess
        if not order_history:
            order_history = fetched_orders
            
        if need_update and session:
            await ChatSession.filter(id=session_uuid).update(
                prefetched_user_facts=user_facts,
                prefetched_previous_session_history=previous_session_history,
                prefetched_order_history=order_history
            )
    
    _t_context = _time.perf_counter()
    print(f"⏱️ [TIMING] Context loading (chat history + store + user facts + orders): {_t_context - _t_session:.2f}s")

    cart_total = float(body.cart_total) / 100.0 if body.cart_total is not None else 0.0
    cart_items = []
    
    raw_body_items = body.cart_items or []
    for item in raw_body_items:
        if isinstance(item, dict):
            raw_price = item.get("price")
            price = float(raw_price) / 100.0 if raw_price is not None else 0.0
            cart_items.append({
                "id": item.get("id"),
                "product_id": item.get("product_id"),
                "title": item.get("title"),
                "quantity": item.get("quantity"),
                "price": price,
            })
        else:
            cart_items.append(item)

    print(f"order history in routes: {order_history}")
    print(f"cart items in routes: {cart_items}")
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
        cart_token=cart_token,
        cart_total=cart_total,
        cart_items=cart_items,
    )


    _t_pre_orchestrate = _time.perf_counter()
    print(f"⏱️ [TIMING] Cart parsing + request build: {_t_pre_orchestrate - _t_context:.2f}s")

    api_resp = await AppController.process_orchestrator_query(user, orchestrator_request)

    _t_post_orchestrate = _time.perf_counter()
    print(f"⏱️ [TIMING] Orchestrator total (router+search+synthesis): {_t_post_orchestrate - _t_pre_orchestrate:.2f}s")
    print(f"⏱️ [TIMING] TOTAL end-to-end: {_t_post_orchestrate - _t_start:.2f}s")

    try:
        result = api_resp.data or {}
        final_response = result.get("final_response")
        fr_dict = _final_response_to_dict(final_response)
        if fr_dict:
            ga = (fr_dict.get("general_answer") or "").strip() or None
            await ChatMessage.create(
                session_id=session_uuid,
                role="assistant",
                content=ga,
                payload=fr_dict,
            )
        # tokens_used / per-session input+output + cost are updated in process_orchestrator_query
        # via token_usage (intent router + expander + final response).
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

@adminapprouter.get("/chatbot/customization", response_model=APIResponse)
async def get_chatbot_customization(user: dict = Depends(AppController.validate_user)):
    return await AppController.get_chatbot_customization(user)

@adminapprouter.put("/chatbot/customization", response_model=APIResponse)
async def update_chatbot_customization(
    bot_name: Optional[str] = Form(None),
    greeting_message: Optional[str] = Form(None),
    primary_color: Optional[str] = Form(None),
    secondary_color: Optional[str] = Form(None),
    background_color: Optional[str] = Form(None),
    text_color: Optional[str] = Form(None),
    user_bubble_color: Optional[str] = Form(None),
    bot_bubble_color: Optional[str] = Form(None),
    font_family: Optional[str] = Form(None),
    font_size_base: Optional[int] = Form(None),
    widget_position: Optional[str] = Form(None),
    border_radius: Optional[int] = Form(None),
    button_icon_style: Optional[str] = Form(None),
    send_button_color: Optional[str] = Form(None),
    other_color: Optional[str] = Form(None),
    sample_questions: Optional[str] = Form(None),
    system_prompt_override: Optional[str] = Form(None),
    logo_url: Optional[str] = Form(None),
    avatar_url: Optional[str] = Form(None),
    logo_file: Optional[UploadFile] = File(None),
    avatar_file: Optional[UploadFile] = File(None),
    user: dict = Depends(AppController.validate_user)
):
    file_handler = FileHandler()
    logo_path = None
    if logo_file and logo_file.filename:
        logo_path = await file_handler.upload_and_compress_image(logo_file)
        
    avatar_path = None
    if avatar_file and avatar_file.filename:
        avatar_path = await file_handler.upload_and_compress_image(avatar_file)
        
    body_data = {}
    if bot_name is not None: body_data["bot_name"] = bot_name
    if greeting_message is not None: body_data["greeting_message"] = greeting_message
    if primary_color is not None: body_data["primary_color"] = primary_color
    if secondary_color is not None: body_data["secondary_color"] = secondary_color
    if background_color is not None: body_data["background_color"] = background_color
    if text_color is not None: body_data["text_color"] = text_color
    if user_bubble_color is not None: body_data["user_bubble_color"] = user_bubble_color
    if bot_bubble_color is not None: body_data["bot_bubble_color"] = bot_bubble_color
    if font_family is not None: body_data["font_family"] = font_family
    if font_size_base is not None: body_data["font_size_base"] = font_size_base
    if widget_position is not None: body_data["widget_position"] = widget_position
    if border_radius is not None: body_data["border_radius"] = border_radius
    if button_icon_style is not None: body_data["button_icon_style"] = button_icon_style
    if send_button_color is not None: body_data["send_button_color"] = send_button_color
    if other_color is not None: body_data["other_color"] = other_color
    if system_prompt_override is not None: body_data["system_prompt_override"] = system_prompt_override
    
    if logo_path:
        body_data["logo_url"] = logo_path
    elif logo_url is not None:
        body_data["logo_url"] = logo_url

    if avatar_path:
        body_data["avatar_url"] = avatar_path
    elif avatar_url is not None:
        body_data["avatar_url"] = avatar_url

    if sample_questions is not None:
        try:
            import json
            parsed_qs = json.loads(sample_questions)
            if isinstance(parsed_qs, list):
                body_data["sample_questions"] = parsed_qs
            else:
                body_data["sample_questions"] = [q.strip() for q in sample_questions.split(",") if q.strip()]
        except Exception:
            body_data["sample_questions"] = [q.strip() for q in sample_questions.split(",") if q.strip()]

    return await AppController.update_chatbot_customization(user, body_data)



@adminapprouter.post("/chatbot/customization/image", response_model=APIResponse)
async def upload_customization_image(
    file: UploadFile = File(...),
    user: dict = Depends(AppController.validate_user)
):
    file_handler = FileHandler()
    url = await file_handler.upload_image(file)
    return APIResponse(
        success=True,
        message="Image uploaded successfully",
        data={"url": url}
    )

@adminapprouter.get("/chatbot/customization/public", response_model=APIResponse)
async def get_public_chatbot_customization(
    user: dict = Depends(AppController.validate_chatbot_api_key)
):
    return await AppController.get_public_chatbot_customization(user)

@adminapprouter.get("/sync-summary", response_model=APIResponse)
async def get_sync_summary(user: dict = Depends(AppController.validate_user)):
    return await AppController.get_sync_summary(user)

# @adminapprouter.post("/getproducts",response_model=APIResponse)
# async def get_products(request:Request,user:dict)



