from tortoise import models, fields
from enum import Enum
import uuid


class asset_type(str, Enum):
    url = "url"
    pdf = "pdf"
    docx = "docx"
    csv = "csv"


class subscription_pack(str, Enum):
    trial = "trial"
    starter = "starter"
    enterprise = "enterprise"

class ecom_store_type(str, Enum):
    shopify = "shopify"
    comez = "comez"
    custom = "custom"

class subscription_type(str, Enum):
    monthly = "monthly"
    yearly = "yearly"


class user_session_status(str, Enum):
    active = "active"
    inactive = "inactive"


class background_task_status(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed" 

class background_task_type(str, Enum):
    create_vectors = "create_vectors"
    get_products = "get_products"
    get_orders = "get_orders"
    query_expander_context = "query_expander_context"


class store_knowledge_data_type(str, Enum):
    product = "product"
    faq = "faq"
    manual = "manual"
    policy = "policy"
    page = "page"
    # Use a shorter DB value due to existing VARCHAR(7) column; "collect" is 7 chars.
    collection = "collect"


class product_type(str, Enum):
    shopify = "shopify"
    comez = "comez"
    custom = "custom"


class users(models.Model):
    id = fields.IntField(pk=True)

    name = fields.CharField(max_length=100, null=True)
    username = fields.CharField(max_length=100, unique=True)
    email = fields.CharField(max_length=255, unique=True)
    password = fields.CharField(max_length=255)

    address = fields.TextField(null=True)
    subscription_id = fields.IntField(null=True)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "users"
        indexes = [
            ("username",),
            ("email",),
        ]




class chatbot_settings(models.Model):
    id = fields.IntField(pk=True)

    user_id = fields.IntField()
    template_json = fields.JSONField(null=True)
    allowed_url = fields.JSONField(null=True)

    is_test = fields.BooleanField(default=False)
    api_key = fields.CharField(max_length=512, unique=True)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "chatbot_settings"
        indexes = [
            ("user_id",),
            ("api_key",),
        ]




class user_assets(models.Model):
    id = fields.IntField(pk=True)
    asset_type = fields.CharEnumField(asset_type)
    user_id = fields.IntField()
    chatbot_id = fields.IntField()
    name = fields.CharField(max_length=255)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "user_assets"
        indexes = [
            ("user_id",),
            ("chatbot_id",),
            ("asset_type",),
        ]


# ============================
# vector_store
# ============================

# class vector_store(models.Model):
#     id = fields.BigIntField(pk=True)

#     user_id = fields.IntField()
#     chatbot_id = fields.IntField()

#     metadata = fields.JSONField(null=True)
#     content = fields.TextField()

#     # NOTE:
#     # This will be created as JSON initially.
#     # Convert to VECTOR(768) using raw SQL after schema creation.
#     vector = fields.JSONField()

#     created_at = fields.DatetimeField(auto_now_add=True)

#     class Meta:
#         table = "vector_store"
#         indexes = [
#             ("user_id",),
#             ("chatbot_id",),
#         ]



class store_knowledge(models.Model):
    id = fields.IntField(pk=True)
    data_type = fields.CharEnumField(store_knowledge_data_type) 
    store_id = fields.BigIntField()
    shopify_product_id = fields.CharField(max_length=50, null=True, unique=True) 
    handle = fields.CharField(max_length=255)
    title = fields.TextField()
    content = fields.TextField()
    price = fields.DecimalField(max_digits=10, decimal_places=2, null=True)  
    stock = fields.IntField(default=0)
    image_url = fields.TextField(null=True)
    variant_data = fields.JSONField(null=True)
    content_hash = fields.CharField(max_length=32, null=True)
    url = fields.TextField(null=True)
    product_type = fields.CharEnumField(product_type ,null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "store_knowledge"
        indexes = [
            ("data_type",),
            ("shopify_product_id",),
            ("handle",),
            ("store_id",),
        ]


# ============================
# subscriptions
# ============================

class subscriptions(models.Model):
    id = fields.IntField(pk=True)

    user_id = fields.IntField()

    pack = fields.CharEnumField(subscription_pack)
    type = fields.CharEnumField(subscription_type)

    start_date = fields.DateField(null=True)
    end_date = fields.DateField(null=True)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "subscriptions"
        indexes = [
            ("user_id",),
            ("pack",),
        ]


# ============================
# query_logs
# ============================

class query_logs(models.Model):
    id = fields.BigIntField(pk=True)

    ipv4 = fields.CharField(max_length=45)
    user_id = fields.IntField()
    chatbot_id = fields.IntField()

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "query_logs"
        indexes = [
            ("user_id",),
            ("chatbot_id",),
            ("created_at",),
        ] 



class user_sessions(models.Model):
    id = fields.IntField(pk=True)

    user_id = fields.IntField()
    token = fields.CharField(max_length=255)
    ip_address = fields.CharField(max_length=255)
    status = fields.CharEnumField(user_session_status)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "user_sessions"
        indexes = [
            ("user_id"),
            ("status"),
            ("token"),
        ]


class background_tasks(models.Model):
    id = fields.IntField(pk=True)
    chatbot_id = fields.BigIntField()
    user_id = fields.IntField()
    task_type = fields.CharEnumField(background_task_type)
    task_data = fields.JSONField(null=True)  # Store urls, files, etc.
    status = fields.CharEnumField(background_task_status, default=background_task_status.pending)
    error_message = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "background_tasks"
        indexes = [
            ("chatbot_id",),
            ("user_id",),
            ("task_type",),
            ("status",),
        ] 


class ecom_store(models.Model):
    id = fields.IntField(pk=True)
    user_id = fields.IntField()
    chatbot_id = fields.IntField(null=True)
    store_id = fields.CharField(max_length=255, null=True)
    store_name = fields.CharField(max_length=255)
    access_token = fields.CharField(max_length=2048 , null=True)
    refresh_token = fields.CharField(max_length=2048 , null=True)
    expires_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True, null=True)
    updated_at = fields.DatetimeField(auto_now=True, null=True)
    store_type = fields.CharEnumField(ecom_store_type, null=True)
    store_dna = fields.TextField(null=True)
    last_synced_at = fields.DatetimeField(null=True)
    sync_status = fields.CharField(max_length=20, default="idle")
    # Cumulative LLM usage estimates (tiktoken + pricing from token_tracker), updated per orchestrator turn.
    total_input_tokens = fields.BigIntField(default=0)
    total_output_tokens = fields.BigIntField(default=0)
    total_cost_usd = fields.FloatField(default=0.0)
    class Meta:
        table = "ecom_store"
        indexes = [
            ("store_id",),
            ("user_id",),
            ("chatbot_id",),
        ]


class chatbot_customization(models.Model):
    id = fields.IntField(pk=True)
    store = fields.OneToOneField("models.ecom_store", related_name="customization", on_delete=fields.CASCADE)
    bot_name = fields.CharField(max_length=255, default="Assistant")
    greeting_message = fields.TextField(default="Hi! How can I help you today?")
    logo_url = fields.TextField(null=True)
    avatar_url = fields.TextField(null=True)
    primary_color = fields.CharField(max_length=50, default="#4F46E5")
    secondary_color = fields.CharField(max_length=50, default="#E0E7FF")
    background_color = fields.CharField(max_length=50, default="#FFFFFF")
    text_color = fields.CharField(max_length=50, default="#1F2937")
    user_bubble_color = fields.CharField(max_length=50, default="#4F46E5")
    bot_bubble_color = fields.CharField(max_length=50, default="#F3F4F6")
    font_family = fields.CharField(max_length=100, default="Inter")
    font_size_base = fields.IntField(default=14)
    widget_position = fields.CharField(max_length=50, default="bottom-right")
    border_radius = fields.IntField(default=8)
    button_icon_style = fields.CharField(max_length=50, default="default")
    send_button_color = fields.CharField(max_length=50, default="#4F46E5")
    other_color = fields.CharField(max_length=50, default="#6B7280")
    sample_questions = fields.JSONField(default=list)
    system_prompt_override = fields.TextField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)


    class Meta:
        table = "chatbot_customization"
        indexes = [
            ("store_id",),
        ]


# ============================
# Dual-Memory chat (sessions + user facts)
# ============================


class ChatTranscript(models.Model):
    """Stores full chat history per session for dual-memory architecture."""
    session_id = fields.CharField(pk=True, max_length=255)
    store_id = fields.IntField()
    user_email = fields.CharField(max_length=255, null=True)
    raw_history = fields.JSONField(default=list)  # list of {"role": "user"|"assistant", "content": "..."}
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "chat_transcript"
        indexes = [
            ("store_id",),
            ("user_email",),
        ]


class ChatSession(models.Model):
    """Live chat session (persisted per shop + customer or cart token)."""

    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    shop_domain = fields.CharField(max_length=255, index=True)
    customer_email = fields.CharField(max_length=255, null=True)
    cart_token = fields.CharField(max_length=255, null=True)
    # Cumulative tiktoken estimate (legacy: input+output per turn).
    tokens_used = fields.BigIntField(default=0)
    # Per-session LLM estimates (orchestrator: intent + expander + final response).
    input_tokens_total = fields.BigIntField(default=0)
    output_tokens_total = fields.BigIntField(default=0)
    estimated_cost_usd_total = fields.FloatField(default=0.0)
    status = fields.CharField(max_length=20, default="active")
    needs_human = fields.BooleanField(default=False)
    prefetched_order_history = fields.TextField(null=True)
    prefetched_user_facts = fields.TextField(null=True)
    prefetched_previous_session_history = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "chat_sessions"
        indexes = [
            ("shop_domain",),
            ("customer_email",),
            ("cart_token",),
            ("status",),
            ("updated_at",),
        ]


class ChatMessage(models.Model):
    """Single chat message belonging to a ChatSession."""

    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    session = fields.ForeignKeyField("models.ChatSession", related_name="messages", on_delete=fields.CASCADE)
    role = fields.CharField(max_length=20)  # "user" or "assistant"
    # Plain text (user message, or optional short copy for assistant e.g. general_answer).
    content = fields.TextField(null=True)
    # Full structured payload: for assistant, entire FinalFrontendResponse / final_response JSON.
    payload = fields.JSONField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "chat_messages"
        indexes = [
            ("session",),
            ("created_at",),
        ]


class UserMemorySummary(models.Model):
    """Extracted permanent facts about a user (preferences, sizes, baby details, dislikes)."""
    id = fields.IntField(pk=True)
    user_email = fields.CharField(max_length=255)
    store_id = fields.IntField()
    fact = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "user_memory_summary"
        indexes = [
            ("user_email",),
            ("store_id",),
        ]

class TicketsRaised(models.Model):
    id = fields.IntField(pk=True)
    user_id = fields.IntField()
    chatbot_id = fields.IntField()
    ticket_id = fields.CharField(max_length=255)
    ticket_subject = fields.CharField(max_length=255)
    user_email = fields.CharField(max_length=255)
    user_name = fields.CharField(max_length=255)
    user_phone = fields.CharField(max_length=255)
    user_address = fields.TextField()
    ticket_description = fields.TextField()
    ticket_status = fields.CharField(max_length=255)
    ticket_created_at = fields.DatetimeField(auto_now_add=True)
    ticket_updated_at = fields.DatetimeField(auto_now=True)
    class Meta:
        table = "tickets_raised"
        indexes = [
            ("user_id",),
            ("chatbot_id",),
            ("ticket_id",),
            ("ticket_status", "ticket_created_at", "ticket_updated_at"),
        ]