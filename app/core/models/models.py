from tortoise import models, fields
from enum import Enum


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
    api_key = fields.CharField(max_length=128, unique=True)

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

class vector_store(models.Model):
    id = fields.BigIntField(pk=True)

    user_id = fields.IntField()
    chatbot_id = fields.IntField()

    metadata = fields.JSONField(null=True)
    content = fields.TextField()

    # NOTE:
    # This will be created as JSON initially.
    # Convert to VECTOR(768) using raw SQL after schema creation.
    vector = fields.JSONField()

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "vector_store"
        indexes = [
            ("user_id",),
            ("chatbot_id",),
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
    chatbot_id = fields.IntField()
    user_id = fields.IntField()
    task_type = fields.CharField(max_length=50, default="create_vectors")
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
            ("status",),
        ] 


class ecom_store(models.Model):
    id = fields.IntField(pk=True)
    user_id = fields.IntField()
    store_id = fields.CharField(max_length=255)
    store_name = fields.CharField(max_length=255)
    access_token = fields.CharField(max_length=255)
    refresh_token = fields.CharField(max_length=255)
    expires_at = fields.DatetimeField()
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    store_type = fields.CharEnumField(ecom_store_type)
    class Meta:
        table = "ecom_store"
        indexes = [
            ("store_id",),
            ("user_id",),
            ("store_name",),
            ("store_type",),
        ]