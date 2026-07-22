from app.core import models as Models
from app.core.schema.applicationerror import ApplicationError
from tortoise import connections
import datetime
from typing import List, Dict
import uuid
import json

class AdminDbContoller:
    def __init__(self):
        self.models = Models
        self.connection = connections.get("default") 

    async def create_user(self, body: dict):
        try:
            return await self.models.users.create(
                name=body.get("name"),
                username=body["username"],
                email=body["email"],
                password=body["password"],
                address=body.get("address"),
                subscription_id=body.get("subscription_id"),
            )
        except Exception as e:
            print(f"Error creating user: {e}")
            raise ApplicationError.InternalServerError("Cannot create user")

    async def find_one_user(self, body: dict):
        try:
            return await self.models.users.filter(email=body["email"]).first()
        except Exception as e:
            print(f"Error finding user: {e}")
            raise ApplicationError.InternalServerError("Cannot find user") 


    async def find_one_chatbot(self, store_id: int):
        try:
            return await self.models.chatbot_settings.filter(store_id=store_id).first()
        except Exception as e:
            print(f"Error finding chatbot: {e}")
            raise ApplicationError.InternalServerError("Cannot find chatbot") 

    async def create_user_session(self, user_id: int, token: str, ip: str):
        try:
            return await self.models.user_sessions.create(user_id=user_id, token=token, ip_address=ip, status="active")
        except Exception as e:
            print(f"Error creating user session: {e}")
            raise ApplicationError.InternalServerError("Cannot create user session")
    
    async def find_one_user_session(self, user_id: int):
        try:
            return await self.models.user_sessions.filter(user_id=user_id).first()
        except Exception as e:
            print(f"Error finding user session: {e}")
            raise ApplicationError.InternalServerError("Cannot find user session")
    
    async def destroy_user_session(self, token: str):
        print(f"Destroying user session: {token}")
        try:
            return await self.models.user_sessions.filter(token=token).update(status="inactive")
        except Exception as e:
            print(f"Error destroying user session: {e}")
            raise ApplicationError.InternalServerError("Cannot destroy user session") 
    
    async def find_one_user_session_by_token(self, token: str):
        try:
            return await self.models.user_sessions.filter(token=token, status="active").first();
        except Exception as e:
            print(f"Error finding user session by token: {e}")
            raise ApplicationError.InternalServerError("Cannot find user session by token") 

    async def find_one_user_by_id(self, user_id: int):
        print(f"Finding user by id: {user_id}")
        try:
            user = await self.models.users.filter(id=user_id).first()
            if user:
                # Convert to dict and exclude password
                user_dict = {
                    "id": user.id,
                    "name": user.name,
                    "username": user.username,
                    "email": user.email,
                    "address": user.address,
                    "subscription_id": user.subscription_id,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                }
                return user_dict
            return None
        except Exception as e:
            print(f"Error finding user by id: {e}")
            raise ApplicationError.InternalServerError("Cannot find user by id")


    async def get_user_subscription_plan(self, user_id: int) -> str:
        """
        Returns a normalized subscription plan string for a user.
        - "enterprise" if the latest subscription pack is enterprise.
        - "starter" for trial/starter/unknown or if no subscription record exists.
        """
        try:
            sub = await self.models.subscriptions.filter(user_id=user_id).order_by("-created_at").first()
            if not sub:
                return "starter"

            pack = getattr(sub, "pack", None)
            if pack == self.models.subscription_pack.enterprise:
                return "enterprise"

            # Treat trial/starter/anything else as starter/basic tier
            return "starter"
        except Exception as e:
            print(f"Error getting subscription plan for user {user_id}: {e}")
            # Fallback safely to starter
            return "starter"

    
    async def create_chatbot(self, body: dict):
        try:
            return await self.models.chatbot_settings.create(
                user_id=body["id"],
                api_key=str(uuid.uuid4()),
            )
        except Exception as e:
            print(f"Error creating chatbot: {e}")
            raise ApplicationError.InternalServerError("Cannot create chatbot")

    async def update_chatbot(self, chatbot_id: int, body: dict):
        try:
            return await self.models.chatbot_settings.filter(id=chatbot_id).update(**body)
        except Exception as e:
            print(f"Error updating chatbot: {e}")
            raise ApplicationError.InternalServerError("Cannot update chatbot")


    async def add_assest(self, chatbot_id: int, files: List[dict]):
        try:
            # Convert dictionaries to model instances
            asset_instances = []
            for file_dict in files:
                # Convert string asset_type to enum
                asset_type_str = file_dict["asset_type"]
                asset_type_enum = self.models.asset_type(asset_type_str)
                
                asset_instances.append(
                    self.models.user_assets(
                        asset_type=asset_type_enum,
                        user_id=file_dict["user_id"],
                        chatbot_id=file_dict["chatbot_id"],
                        name=file_dict["name"]
                    )
                )
            return await self.models.user_assets.bulk_create(asset_instances)
        
        except Exception as e:
            print(f"Error adding assest: {e}")
            raise ApplicationError.InternalServerError("Cannot add assest")

    async def create_background_task(self, user_id: int, chatbot_id: int,task_data: Dict, task_type: str = "create_vectors"):
        try:
            return await self.models.background_tasks.create(
                chatbot_id=chatbot_id,
                user_id=user_id,
                task_type=task_type,
                task_data=task_data,
                status=self.models.background_task_status.pending
            )
        except Exception as e:
            print(f"Error creating background task: {e}")
            raise ApplicationError.InternalServerError("Cannot create background task")

    async def get_pending_background_tasks(self):
        try:
            return await self.models.background_tasks.filter(
                status=self.models.background_task_status.pending
            ).limit(1).all()
        except Exception as e:
            print(f"Error getting pending tasks: {e}")
            raise ApplicationError.InternalServerError("Cannot get pending tasks")

    async def update_background_task_status(self, task_id: int, status: str, error_message: str = None):
        try:
            update_data = {"status": status}
            if error_message:
                update_data["error_message"] = error_message
            return await self.models.background_tasks.filter(id=task_id).update(**update_data)
        except Exception as e:
            print(f"Error updating task status: {e}")
            raise ApplicationError.InternalServerError("Cannot update task status")

    async def create_vector_store(self, nodes):
        values = []
        placeholders = []

        for i, node in enumerate(nodes):
            placeholders.append(
                f"(${i*5+1}, ${i*5+2}, ${i*5+3}, ${i*5+4}, ${i*5+5})"
            )

            # Get embedding from metadata (stored there since Document objects don't allow arbitrary attributes)
            embedding = node.metadata.pop("embedding", [])
            
            values.extend([
                node.metadata["user_id"],
                node.metadata["chatbot_id"],
                node.page_content,  # Langchain Document uses .page_content, not .text
                json.dumps(node.metadata),
                json.dumps(embedding)
            ])

        sql = f"""
        INSERT INTO vector_store
        (user_id, chatbot_id, content, metadata, vector)
        VALUES {", ".join(placeholders)}
        """

        conn = connections.get("default")
        await conn.execute_query(sql, values)

    async def bulk_insert_vectors(nodes):
        values = []
        placeholders = []

        for i, node in enumerate(nodes):
            placeholders.append(
                f"(${i*6+1}, ${i*6+2}, ${i*6+3}, ${i*6+4}, ${i*6+5}, ${i*6+6})"
            )

            # Get embedding from metadata (stored there since Document objects don't allow arbitrary attributes)
            embedding = node.metadata.pop("embedding", [])
            
            values.extend([
                str(uuid.uuid4()),
                node.metadata["chatbot_id"],
                node.metadata["user_id"],
                node.page_content,  # Langchain Document uses .page_content, not .text
                json.dumps(node.metadata),
                embedding
            ])

        sql = f"""
        INSERT INTO vector_store
        (id, user_id, chatbot_id, content, metadata, vector)
        VALUES {", ".join(placeholders)}
        """

        conn = connections.get("default")
        await conn.execute_query(sql, values)


     
    async def get_response(self, user_id: int, embedding: list[float]):
        try:
            # Convert to JSON string - Tortoise handles this
            vector_json = json.dumps(embedding)
            
            sql = """
                SELECT id, content, metadata,
                       vector <=> $1::vector AS distance
                FROM vector_store
                WHERE user_id = $2 
                  AND vector <=> $1::vector < 0.5
                ORDER BY vector <=> $1::vector ASC
                LIMIT 20;
            """
            
            conn = connections.get("default")
            rows = await conn.execute_query_dict(sql, [vector_json, user_id])
            return rows
        except Exception as e:
            print(f"Error getting response: {e}")
            raise ApplicationError.InternalServerError("Cannot get response")


    async def find_one_ecom_store(self, chatbot_id: int):
        try:
            return await self.models.ecom_store.filter(chatbot_id=chatbot_id).first()
        except Exception as e:
            print(f"Error finding ecom store: {e}")
            raise ApplicationError.InternalServerError("Cannot find ecom store")

    async def find_first_ecom_store_by_user_id(self, user_id: int):
        """Return the first ecom_store for the given user (for default store when chatbot_id is not sent)."""
        try:
            return await self.models.ecom_store.filter(user_id=user_id).first()
        except Exception as e:
            print(f"Error finding ecom store by user: {e}")
            return None

    async def find_one_ecom_store_by_shop(self, shop: str):
        """Find ecom_store by store domain (e.g. chatty-store-3.myshopify.com or chatty-store-3)."""
        try:
            clean = (shop or "").replace("https://", "").replace(".myshopify.com", "").strip()
            # Try exact shop first, then without .myshopify.com
            row = await self.models.ecom_store.filter(store_name=shop).first()
            if row:
                return row
            if clean and clean != shop:
                return await self.models.ecom_store.filter(store_name=clean).first()
            return None
        except Exception as e:
            print(f"Error finding ecom store by shop: {e}")
            raise ApplicationError.InternalServerError("Cannot find ecom store")

    async def update_ecom_store_tokens(self, ecom_store_id: int, access_token: str, refresh_token: str, expires_at, store_name: str = None):
        """Update access_token, refresh_token, expires_at (and optionally store_name) for an ecom_store."""
        try:
            q = self.models.ecom_store.filter(id=ecom_store_id)
            upd = {"access_token": access_token, "refresh_token": refresh_token, "expires_at": expires_at}
            if store_name is not None:
                upd["store_name"] = store_name
            await q.update(**upd)
        except Exception as e:
            print(f"Error updating ecom store tokens: {e}")
            raise ApplicationError.InternalServerError("Cannot update ecom store tokens")


    async def create_ecom_store(self, user_id: int, chatbot_id: int, store_id: str, store_name: str, access_token: str, refresh_token: str, expires_at: datetime, store_type: str):
        try:
            return await self.models.ecom_store.create(user_id=user_id, chatbot_id=chatbot_id, store_id=store_id, store_name=store_name, access_token=access_token, refresh_token=refresh_token, expires_at=expires_at, store_type=store_type)
        except Exception as e:
            print(f"Error creating ecom store: {e}")
            raise ApplicationError.InternalServerError("Cannot create ecom store")

    async def update_store_dna(self, store_id: int, dna_summary: str):
        """Update the store_dna column for a given ecom_store id."""
        try:
            await self.models.ecom_store.filter(id=store_id).update(store_dna=dna_summary)
        except Exception as e:
            print(f"Error updating store DNA: {e}")
            raise ApplicationError.InternalServerError("Cannot update store DNA")

    async def insert_products_to_database(self, products_list: list, chatbot_id: int):
        """
        Insert a batch of products into store_knowledge.
        Uses one parameterized INSERT per row (asyncpg-style $1 placeholders),
        which avoids SQL injection and type issues while still batching at the
        application level.
        """
        print(f"Products list: {products_list}")
        try:
            if not products_list:
                return

            sql = """
            INSERT INTO store_knowledge (
                shopify_product_id,
                store_id,
                handle,
                title,
                content,
                price,
                stock,
                image_url,
                variant_data,
                embedding,
                content_hash,
                product_type,
                data_type
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11, $12, $13
            )
            """

            for product in products_list:
                params = [
                    product.get("shopify_product_id", ""),
                    int(chatbot_id),
                    product.get("handle", ""),
                    product.get("title", ""),
                    product.get("content", ""),
                    product.get("price", 0),
                    product.get("stock", 0),
                    product.get("image_url", ""),
                    json.dumps(product.get("variant_data", {})),
                    json.dumps(product.get("embedding", [])),
                    product.get("content_hash", ""),
                    product.get("product_type", "shopify"),
                    product.get("data_type", "product"),
                ]
                await self.connection.execute_query(sql, params)
        except Exception as e:
            print(f"Error inserting products to database: {e}")
            raise ApplicationError.InternalServerError("Cannot insert products to database")

    async def insert_store_knowledge_raw(
        self,
        store_id: int,
        shopify_product_id: str,
        handle: str,
        title: str,
        content: str,
        data_type: str,
        url: str = None,
        embedding: list = None,
        content_hash: str = None,
    ):
        """Insert or update one store_knowledge row (e.g. page/policy) using raw SQL only. Matches store_knowledge table schema."""
        try:
            emb_json = json.dumps(embedding) if embedding and isinstance(embedding, list) else None
            sql = """
            INSERT INTO store_knowledge (
                store_id, shopify_product_id, handle, title, content,
                price, stock, image_url, variant_data, content_hash, url,
                data_type, product_type, embedding
            ) VALUES ($1, $2, $3, $4, $5, NULL, 0, NULL, NULL, $6, $7, $8, NULL, $9::vector)
            ON CONFLICT (shopify_product_id) DO UPDATE SET
                handle = EXCLUDED.handle,
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                url = EXCLUDED.url,
                data_type = EXCLUDED.data_type,
                content_hash = EXCLUDED.content_hash,
                embedding = EXCLUDED.embedding
            """
            await self.connection.execute_query(
                sql,
                [
                    store_id,
                    shopify_product_id[:50],
                    handle[:255] if handle else "unknown",
                    title,
                    content,
                    content_hash,
                    url,
                    data_type,
                    emb_json if emb_json else None,
                ],
            )
        except Exception as e:
            print(f"Error insert_store_knowledge_raw: {e}")
            raise ApplicationError.InternalServerError("Cannot insert store_knowledge")

    # ============================
    # store_knowledge: raw rows for metrics (no HTTP / billing shape here)
    # ============================

    async def fetch_store_knowledge_rows_for_token_count(
        self, *, ecom_store_id: int, chatbot_id: int | None
    ) -> list[dict]:
        """
        Plain `store_knowledge` rows for token estimation.
        `store_id` may be ecom_store.id (pages/collections) or chatbot_id (product ingest).
        """
        ids: set[int] = {int(ecom_store_id)}
        if chatbot_id is not None:
            ids.add(int(chatbot_id))
        rows = await self.models.store_knowledge.filter(store_id__in=list(ids)).values(
            "id", "title", "content", "data_type"
        )
        return list(rows)

    # ============================
    # Live chat: sessions/messages
    # ============================

    def _normalize_shop_domain(self, value: str) -> str:
        host = (value or "").strip().replace("https://", "").replace("http://", "").split("/")[0]
        if not host:
            return ""
        if host.endswith(".myshopify.com"):
            return host
        return f"{host.split('.')[0]}.myshopify.com"

    async def list_active_chat_sessions_for_user(self, user_id: int) -> list[dict]:
        """
        Live Chat admin view: list active sessions for the authenticated merchant's shop,
        ordered by updated_at DESC. Includes a latest message snippet when possible.
        """
        store = await self.find_first_ecom_store_by_user_id(user_id)
        if not store:
            return []

        shop_domain = self._normalize_shop_domain(store.store_name or "")
        if not shop_domain:
            return []

        sessions = await self.models.ChatSession.filter(shop_domain=shop_domain, status="active").order_by("-updated_at").all()
        out: list[dict] = []
        for s in sessions:
            last_msg = await self.models.ChatMessage.filter(session_id=s.id).order_by("-created_at").first()
            snippet = ""
            if last_msg:
                if getattr(last_msg, "content", None):
                    snippet = str(last_msg.content)[:80]
                elif isinstance(getattr(last_msg, "payload", None), dict):
                    ga = (last_msg.payload or {}).get("general_answer")
                    if ga:
                        snippet = str(ga)[:80]

            out.append(
                {
                    "id": str(s.id),
                    "shop_domain": s.shop_domain,
                    "customer_email": s.customer_email,
                    "cart_token": s.cart_token,
                    "tokens_used": getattr(s, "tokens_used", None),
                    "input_tokens_total": getattr(s, "input_tokens_total", None),
                    "output_tokens_total": getattr(s, "output_tokens_total", None),
                    "estimated_cost_usd_total": getattr(s, "estimated_cost_usd_total", None),
                    "status": s.status,
                    "needs_human": getattr(s, "needs_human", False),
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                    "latest_message_snippet": snippet,
                }
            )

        return out

    async def list_chat_messages_for_session_for_user(
        self, *, user_id: int, session_id: "uuid.UUID"
    ) -> list[dict] | None:
        store = await self.find_first_ecom_store_by_user_id(user_id)
        if not store:
            return None

        shop_domain = self._normalize_shop_domain(store.store_name or "")
        if not shop_domain:
            return None

        session = await self.models.ChatSession.filter(id=session_id, shop_domain=shop_domain).first()
        if not session:
            return None

        messages = await self.models.ChatMessage.filter(session_id=session.id).order_by("created_at").all()
        return [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "payload": getattr(m, "payload", None),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]

    # ============================
    # LightRAG sync tracking
    # ============================

    async def get_sync_status_for_user(self, user_id: int) -> dict:
        store = await self.find_first_ecom_store_by_user_id(user_id)
        if not store:
            return {"last_synced_at": None, "sync_status": "idle"}

        return {
            "last_synced_at": store.last_synced_at.isoformat() if store.last_synced_at else None,
            "sync_status": store.sync_status,
        }

    async def set_store_sync_status(self, store_id: int, *, last_synced_at: datetime, sync_status: str) -> None:
        await self.models.ecom_store.filter(id=store_id).update(
            last_synced_at=last_synced_at,
            sync_status=sync_status,
        )

    # ============================
    # Chatbot Customization DB Methods
    # ============================

    async def get_chatbot_customization(self, store_id: int):
        try:
            custom = await self.models.chatbot_customization.filter(store_id=store_id).first()
            if not custom:
                # Create with default options
                custom = await self.models.chatbot_customization.create(
                    store_id=store_id,
                    bot_name="Assistant",
                    greeting_message="Hi! How can I help you today?",
                    primary_color="#4F46E5",
                    secondary_color="#E0E7FF",
                    background_color="#FFFFFF",
                    text_color="#1F2937",
                    user_bubble_color="#4F46E5",
                    bot_bubble_color="#F3F4F6",
                    font_family="Inter",
                    font_size_base=14,
                    widget_position="bottom-right",
                    border_radius=8,
                    button_icon_style="default",
                    send_button_color="#4F46E5",
                    other_color="#6B7280",
                    sample_questions=["What are your best sellers?", "Do you have any active discounts?"],
                )
            return custom
        except Exception as e:
            print(f"Error getting chatbot customization for store {store_id}: {e}")
            raise ApplicationError.InternalServerError("Cannot get chatbot customization")

    async def update_chatbot_customization(self, store_id: int, data: dict):
        try:
            # Ensure it exists
            await self.get_chatbot_customization(store_id)
            await self.models.chatbot_customization.filter(store_id=store_id).update(**data)
            return await self.models.chatbot_customization.filter(store_id=store_id).first()
        except Exception as e:
            print(f"Error updating chatbot customization for store {store_id}: {e}")
            raise ApplicationError.InternalServerError("Cannot update chatbot customization")

    # ============================
    # Sync Summary & handoff flag methods
    # ============================

    async def get_sync_summary_for_user(self, user_id: int) -> dict:
        try:
            store = await self.find_first_ecom_store_by_user_id(user_id)
            if not store:
                return {
                    "total_products": 0,
                    "total_pages": 0,
                    "total_policies": 0,
                    "total_custom": 0,
                    "last_synced_at": None,
                    "sync_status": "idle",
                }
            
            store_ids = [store.id]
            if store.chatbot_id:
                store_ids.append(store.chatbot_id)
                
            total_products = await self.models.store_knowledge.filter(
                store_id__in=store_ids, data_type="product"
            ).count()
            
            total_pages = await self.models.store_knowledge.filter(
                store_id__in=store_ids, data_type="page"
            ).count()
            
            total_policies_policy = await self.models.store_knowledge.filter(
                store_id__in=store_ids, data_type="policy"
            ).count()
            
            total_policies_page = await self.models.store_knowledge.filter(
                store_id__in=store_ids, data_type="page", url__icontains="/policies/"
            ).count()
            
            total_policies = total_policies_policy + total_policies_page

            total_custom = await self.models.store_knowledge.filter(
                store_id__in=store_ids, data_type="custom"
            ).count()

            return {
                "total_products": total_products,
                "total_pages": total_pages,
                "total_policies": total_policies,
                "total_custom": total_custom,
                "last_synced_at": store.last_synced_at.isoformat() if store.last_synced_at else None,
                "sync_status": store.sync_status,
            }
        except Exception as e:
            print(f"Error getting sync summary: {e}")
            raise ApplicationError.InternalServerError("Cannot get sync summary")

    async def set_session_needs_human(self, session_id: uuid.UUID, needs_human: bool) -> bool:
        try:
            session = await self.models.ChatSession.filter(id=session_id).first()
            if not session:
                return False
            await self.models.ChatSession.filter(id=session_id).update(needs_human=needs_human)
            return True
        except Exception as e:
            print(f"Error setting needs_human to {needs_human} on session {session_id}: {e}")
            raise ApplicationError.InternalServerError("Cannot update session handover status")

    async def create_add_to_cart_event(
        self,
        *,
        store_id: int,
        chatbot_id: int | None,
        session_id: str | None,
        shop_domain: str | None,
        product_id: str | None,
        variant_id: str | None,
        title: str | None,
        quantity: int,
        unit_price: float,
        currency: str,
        line_revenue: float,
    ):
        try:
            return await self.models.AddToCartEvent.create(
                store_id=store_id,
                chatbot_id=chatbot_id,
                session_id=session_id,
                shop_domain=shop_domain,
                product_id=product_id,
                variant_id=variant_id,
                title=title,
                quantity=quantity,
                unit_price=unit_price,
                currency=currency or "INR",
                line_revenue=line_revenue,
            )
        except Exception as e:
            print(f"Error creating add_to_cart event: {e}")
            raise ApplicationError.InternalServerError("Cannot log add to cart event")

    async def get_dashboard_stats_for_user(self, user_id: int) -> dict:
        """
        Aggregate dashboard metrics for the merchant's primary store:
        ATC clicks + attributed revenue, queries answered, LLM tokens + cost.
        """
        from datetime import datetime, timezone
        from app.core.services.token_tracker import usd_to_inr, USD_TO_INR

        store = await self.find_first_ecom_store_by_user_id(user_id)
        if not store:
            return {
                "store_id": None,
                "add_to_cart_clicks": 0,
                "attributed_revenue": 0.0,
                "currency": "INR",
                "queries_answered": 0,
                "questions_answered": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "total_cost_inr": 0.0,
                "usd_to_inr_rate": USD_TO_INR,
                "active_sessions_today": 0,
            }

        store_id = store.id
        shop_domain = self._normalize_shop_domain(store.store_name or "")

        atc_qs = self.models.AddToCartEvent.filter(store_id=store_id)
        add_to_cart_clicks = await atc_qs.count()
        revenue_rows = await self.models.AddToCartEvent.filter(store_id=store_id).values_list(
            "line_revenue", flat=True
        )
        attributed_revenue = round(sum(float(r or 0) for r in revenue_rows), 2)

        queries_answered = 0
        questions_answered = 0
        active_sessions_today = 0
        if shop_domain:
            session_ids = await self.models.ChatSession.filter(shop_domain=shop_domain).values_list(
                "id", flat=True
            )
            if session_ids:
                queries_answered = await self.models.ChatMessage.filter(
                    session_id__in=session_ids, role="user"
                ).count()
                questions_answered = await self.models.ChatMessage.filter(
                    session_id__in=session_ids, role="assistant"
                ).count()

            start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            active_sessions_today = await self.models.ChatSession.filter(
                shop_domain=shop_domain,
                status="active",
                updated_at__gte=start_of_day,
            ).count()

        tin = int(getattr(store, "total_input_tokens", 0) or 0)
        tout = int(getattr(store, "total_output_tokens", 0) or 0)
        tcost_usd = float(getattr(store, "total_cost_usd", 0.0) or 0.0)
        tcost_inr = usd_to_inr(tcost_usd)

        return {
            "store_id": store_id,
            "add_to_cart_clicks": add_to_cart_clicks,
            "attributed_revenue": attributed_revenue,
            "currency": "INR",
            "queries_answered": queries_answered,
            "questions_answered": questions_answered,
            "total_input_tokens": tin,
            "total_output_tokens": tout,
            "total_tokens": tin + tout,
            "total_cost_usd": round(tcost_usd, 8),
            "total_cost_inr": tcost_inr,
            "usd_to_inr_rate": USD_TO_INR,
            "active_sessions_today": active_sessions_today,
        }