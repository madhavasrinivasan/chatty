from pickle import ADDITEMS
from llama_index_instrumentation.span_handlers import null
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import RegisterRequest, LoginRequest, llmrequest, AddshopifyRequest, OrchestratorRequest
from app.core.schema.schemarespone import APIResponse
from app.core.schema.applicationerror import ApplicationError
from fastapi import Request, BackgroundTasks
from app.core.services.jwt import JWTService
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from fastapi import Request
from Crypto.Random import get_random_bytes
from typing import List, Optional
import asyncio
from app.core.config import db as db_config
from app.core.config.db import initialize_light_rag
from lightrag import QueryParam
from app.core.services.webcrawler import Services
from app.core.config.config import settings
from langchain_core.documents import Document as LangchainDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.core.services.ai_orchestrator import process_user_query as ai_process_user_query
from app.core.services.database_executor import execute_search
from app.core.services.shopify_service import (
    generate_shopify_install_url,
    encrypt_token,
    decrypt_token,
    get_product_collections,
    transform_shopify_product,
)
from app.core.models.models import ecom_store, store_knowledge, chatbot_settings
import bcrypt
import base64
import time
import os
from datetime import datetime, timedelta, timezone
import shopify
from shopify.collection import PaginatedIterator
import jwt
import httpx
import json
from bs4 import BeautifulSoup
import hashlib
import binascii


directory = settings.file_upload_directory_pdf


class  AppController:
    @staticmethod
    async def get_user(token: str):
        try:
            return await AdminDbContoller().find_one_user_session(token)
        except Exception as e:
            print(f"error getting user: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message) 




    @staticmethod
    async def validate_user(request: Request):
        token = request.headers.get("adminauthtoken")
        if not token:
            raise ApplicationError.Unauthorized("Invalid User Token Not Found")
        try:
            session = await AdminDbContoller().find_one_user_session_by_token(token)
            if not session or session is None:
                raise ApplicationError.Unauthorized("Cannot Find User Session")
            
            user = await AdminDbContoller().find_one_user_by_id(session.user_id)
            print(f"User: {user}")
            if not user or user is None:
                raise ApplicationError.Unauthorized("Cannot Find User")
            
            return user
        except Exception as e:
            print(f"error validating user: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.Unauthorized(error_message)  

    
    @staticmethod
    async def upload_knowledge_base(user: dict, file_path: Optional[List[dict]], request, background_tasks: BackgroundTasks = None):
        try: 
             print(f"Uploading knowledge base for user: {user}")
             print(f"File path: {file_path}")
             print(f"Request: {request}")
             print(f"Background tasks: {background_tasks}")
             chatbot = await AdminDbContoller().create_chatbot(user)

             print(f"Chatbot: {chatbot}")

             if not chatbot or chatbot is None:
                raise ApplicationError.SomethingWentWrong("Cannot create chatbot")

             files:List[str] = []

             if file_path:
                 for file in file_path:
                    file_dict:dict = {
                        "asset_type": "pdf",
                        "user_id": user["id"],
                        "chatbot_id": chatbot.id,
                        "name": file["file_name"],
                    }
                    files.append(file_dict) 

            #  for url in request.urls:
            #     file_dict:dict = {
            #         "asset_type": "url",
            #         "user_id": user["id"],
            #         "chatbot_id": chatbot.id,
            #         "name": url,
            #     }
            #     files.append(file_dict) 

             print(f"Files: {files}")

             add_assest = await AdminDbContoller().add_assest(chatbot.id, files)

             # Create background task in database (will be picked up by polling worker)
             task_data = {
                 "urls": request.urls if request.urls else [],
                 "files": files
             }
             await AdminDbContoller().create_background_task(chatbot.id, user["id"], task_data)

             # Convert Tortoise model to dict for serialization
             chatbot_dict = {
                 "id": chatbot.id,
                 "user_id": chatbot.user_id,
                 "api_key": chatbot.api_key,
                 "template_json": chatbot.template_json,
                 "allowed_url": chatbot.allowed_url,
                 "is_test": chatbot.is_test,
                 "created_at": chatbot.created_at.isoformat() if chatbot.created_at else None,
             }
             
             return APIResponse(
                success=True,
                message="Knowledge base uploaded successfull",
                data=chatbot_dict
             )
            

        except Exception as e:
            print(f"error uploading knowledge base: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message) 


    @staticmethod
    async def create_vectors_background_task(chatbot_id: int, urls: list, files: list, user_id: int):
        try:
            print(f"Creating vectors background task for chatbot: {chatbot_id}")
            documents: list = []

            # Process URLs
            if urls and len(urls) > 0:
                crawl_results = await Services.crawlweb(urls)
                crawled_docs = await Services.crawl_results_to_documents(
                    crawl_results, {"chatbot_id": chatbot_id, "user_id": user_id}
                )
                documents.extend(crawled_docs) 

                # for crawl_result in crawl_results:
                # documents.append({"crawlaai":crawl_results})
            # Process PDF files
            if files and len(files) > 0:
                for file_dict in files:
                    pdf_path = file_dict.get("name") if isinstance(file_dict, dict) else file_dict
                    full_pdf_path = os.path.join(directory, pdf_path)
                    pdf_docs = await Services.extract_pdf_pages_readable(full_pdf_path)
                    for pdf_doc in pdf_docs:
                        doc = {
                            "page_content": pdf_doc["text"],
                            "metadata": {
                                "source_type": "pdf",
                                "file_name": pdf_path,
                                "page_number": pdf_doc.get("page_number"),
                                "total_pages": pdf_doc.get("total_pages"),
                                "chatbot_id": chatbot_id,
                                "user_id": user_id,
                            }
                        }
                        documents.append(doc)
                    # documents.append({"pdf":pdf_doc})

            if not documents:
                print("No documents to insert into LightRAG")
                return 

            # with open("files.txt", "w", encoding="utf-8") as f:
            #     f.write(str(documents))

            # Get LightRAG instance for this workspace (store_${chatbot_id})
            # Background task has no Request; use initialize_light_rag directly (no app.state).
            workspace_id = f"store_{chatbot_id}"
            rag = await initialize_light_rag(store_id=workspace_id)

            # 1. Chunk documents into nodes (800 chars, overlap 120) via Services
            # nodes = await Services.documents_to_nodes(documents)

            # 2. Convert nodes to strings with SOURCE header for LightRAG
            texts_to_insert = []
            for node in documents:
                source = node.get("metadata").get("url") or node.get("metadata").get("file_name") or "unknown_source"
                final_text = f"--- SOURCE: {source} ---\n{node.get("page_content")}"
                texts_to_insert.append(final_text)

            if not texts_to_insert:
                print("No content to insert into LightRAG")
                return

            # 3. Insert into LightRAG (embedding, graph storage handled by LightRAG)
            track_id = await rag.ainsert(input=texts_to_insert)
            print(f"LightRAG insert completed for chatbot {chatbot_id}, track_id: {track_id}")

        except Exception as e:
            print(f"error creating vectors background task: {e}")
            error_message = getattr(e, "message", str(e))
            raise ApplicationError.SomethingWentWrong(error_message)  
   


    _CONTENT_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=150)

    @staticmethod
    async def ingest_store_content(store_id: int):
        """Ingest pages first (parse → split 5000/150 → embed → raw insert), then policies (same). Uses raw SQL only. Policy IDs use index when Shopify id is None."""
        controller = AdminDbContoller()
        splitter = AppController._CONTENT_SPLITTER
        try:
            print("📖 Ingesting Pages...")
            pages = shopify.Page.find()
            all_page_chunks = []
            for page in pages:
                body_html = getattr(page, "body_html", None) or ""
                if not body_html.strip():
                    continue
                soup = BeautifulSoup(body_html, "html.parser")
                clean_text = soup.get_text(separator=" ").strip()
                title = getattr(page, "title", "Page") or "Page"
                handle = (getattr(page, "handle", None) or "")[:255].replace(" ", "-").lower() or "page"
                url = f"/pages/{getattr(page, 'handle', '') or ''}" or None
                page_id = f"page_{getattr(page, 'id', id(page))}"
                content = f"Page: {title}. Content: {clean_text}"
                doc = LangchainDocument(page_content=content, metadata={"source_id": page_id, "handle": handle, "title": title, "url": url})
                chunks = splitter.split_documents([doc])
                for j, ch in enumerate(chunks):
                    all_page_chunks.append({
                        "source_id": f"{page_id}_c{j}",
                        "handle": handle,
                        "title": title,
                        "content": ch.page_content,
                        "url": url,
                    })
            if all_page_chunks:
                texts = [r["content"] for r in all_page_chunks]
                embeddings = await Services.generate_batch_embeddings(texts)
                for i, row in enumerate(all_page_chunks):
                    emb = embeddings[i] if i < len(embeddings) else None
                    await controller.insert_store_knowledge_raw(
                        store_id=store_id,
                        shopify_product_id=row["source_id"],
                        handle=row["handle"],
                        title=row["title"],
                        content=row["content"],
                        data_type="page",
                        url=row["url"],
                        embedding=emb,
                    )
                print("✅ Pages ingestion complete.")
        except Exception as e:
            print(f"⚠️ Error ingesting pages: {e}")

        # 2. Policies: whole policy per row (no chunking), single embedding each. Policy.all() for Shopify API.
        try:
            print("📜 Ingesting Store Policies...")
            try:
                policies = shopify.Policy.all()
            except AttributeError:
                policies = list(shopify.Policy.find() or [])
            for policy in policies:
                body = getattr(policy, "body", "") or ""
                if not body or len(body.strip()) < 10:
                    continue
                title = getattr(policy, "title", "Policy") or "Policy"
                soup = BeautifulSoup(body, "html.parser")
                clean_text = soup.get_text(separator=" ").strip()
                full_content = f"Policy: {title}. Content: {clean_text}"
                embedding = await Services.generate_embedding(full_content)
                handle = title.lower().replace(" ", "-")
                shopify_product_id = f"policy_{title.lower().replace(' ', '_')}"
                url = f"/policies/{handle}"
                await controller.insert_store_knowledge_raw(
                    store_id=store_id,
                    shopify_product_id=shopify_product_id,
                    handle=handle,
                    title=title,
                    content=full_content,
                    data_type="page",
                    url=url,
                    embedding=embedding,
                )
            print("✅ Policies ingestion complete.")
        except Exception as e:
            print(f"⚠️ Error ingesting policies: {e}")

    @staticmethod
    async def ingest_shopify_collections(store_id: int, shopify_domain: str, access_token: str):
        """
        Ingest Shopify custom + smart collections into store_knowledge as data_type="collection".
        Uses Shopify python library (sync) via asyncio.to_thread to avoid blocking.
        Stores only title/description/url and an embedding vector; upserts only when content_hash changes.
        """
        controller = AdminDbContoller()
        print(f"Shopify domain collection: {shopify_domain}")

        def _fetch_collections_sync():
            with shopify.Session.temp(shopify_domain, "2024-01", access_token):
                custom: list = []
                smart: list = []
                try:
                    # PaginatedIterator yields pages (lists); flatten into a single list
                    custom_pages = PaginatedIterator(shopify.CustomCollection.find(limit=250))
                    for page in custom_pages:
                        custom.extend(list(page))
                except Exception:
                    custom = list(shopify.CustomCollection.find() or [])
                try:
                    smart_pages = PaginatedIterator(shopify.SmartCollection.find(limit=250))
                    for page in smart_pages:
                        smart.extend(list(page))
                except Exception:
                    smart = list(shopify.SmartCollection.find() or [])
                print(f"Custom collections: {custom}")
                print(f"Smart collections: {smart}")
                return custom + smart


        try:
            collections = await asyncio.to_thread(_fetch_collections_sync)
        except Exception as e:
            print(f"⚠️ Error fetching collections: {e}")
            return

        if not collections:
            print("No collections found to ingest.")
            return

        pending = []
        for col in collections:
            col_id = getattr(col, "id", None)
            if col_id is None:
                continue
            handle = getattr(col, "handle", "") or ""
            title = getattr(col, "title", "") or "Collection"
            body_html = getattr(col, "body_html", None)
            if not body_html or not str(body_html).strip():
                body_html = f"Explore the {title} collection"

            soup = BeautifulSoup(str(body_html), "html.parser")
            clean_text = soup.get_text(separator=" ").strip()
            if not clean_text:
                clean_text = f"Explore the {title} collection"

            text_to_embed = f"Collection Title: {title}. Description: {clean_text}"
            content_hash = hashlib.md5(text_to_embed.encode("utf-8")).hexdigest()

            existing = await store_knowledge.filter(
                store_id=store_id,
                shopify_product_id=str(col_id),
            ).first()
            if existing and getattr(existing, "content_hash", None) == content_hash:
                continue

            pending.append(
                {
                    "shopify_product_id": str(col_id),
                    "handle": handle,
                    "title": title,
                    "content": clean_text,
                    "url": f"/collections/{handle}",
                    "content_hash": content_hash,
                    "text_to_embed": text_to_embed,
                }
            )

        if not pending:
            print("✅ Collections already up-to-date.")
            return

        try:
            embeddings = await Services.generate_batch_embeddings([p["text_to_embed"] for p in pending])
        except Exception as e:
            print(f"⚠️ Error embedding collections: {e}")
            return

        for i, row in enumerate(pending):
            emb = embeddings[i] if i < len(embeddings) else None
            try:
                await controller.insert_store_knowledge_raw(
                    store_id=store_id,
                    shopify_product_id=row["shopify_product_id"],
                    handle=row["handle"],
                    title=row["title"],
                    content=row["content"],
                    # DB stores this as "collect" (7 chars) to fit existing VARCHAR(7),
                    # but the logical meaning is "collection".
                    data_type="collect",
                    url=row["url"],
                    embedding=emb,
                    content_hash=row["content_hash"],
                )
            except Exception as e:
                print(f"⚠️ Error upserting collection {row.get('shopify_product_id')}: {e}")

        print(f"✅ Collections ingestion complete. Upserted {len(pending)} collection rows.")

    @staticmethod
    async def get_products_background_task(chatbot_id: int, store_id: int , task_id: int):
        try:
            print(f"Getting products background task for chatbot: {chatbot_id}")

            shop_details = await AdminDbContoller().find_one_ecom_store(chatbot_id=chatbot_id)
            if not shop_details or shop_details is None:
                raise ApplicationError.SomethingWentWrong("Cannot find shopify store")

            store_name = shop_details.store_name or ""
            access_token = shop_details.access_token or ""
            if not store_name.strip() or not access_token:
                raise ApplicationError.SomethingWentWrong(
                    "Shopify store has no store name or access token; complete OAuth first."
                )

            session = shopify.Session(store_name.strip(), "2024-04", access_token)
            shopify.ShopifyResource.activate_session(session)
            products_list = []
            try:
                shop = shopify.Shop.current()
                print(f"✅ Success! Connected to shop: {shop.name}")
                print(f"Currency: {shop.currency}")

                # Set app-reserved metafield on shop with chatbot api_key (for storefront/API use)
                try:
                    chatbot = await chatbot_settings.filter(id=chatbot_id).first()
                    api_key_value = (chatbot.api_key or "").strip() if chatbot else ""
                    if api_key_value:
                        # Plain host for URL (e.g. store.myshopify.com)
                        shop_host = (store_name or "").strip().replace("https://", "").replace("http://", "").split("/")[0]
                        if not shop_host:
                            shop_host = (
                                f"{store_name.strip()}.myshopify.com"
                                if ".myshopify.com" not in store_name
                                else store_name.strip()
                            )

                        metafield_url = f"https://{shop_host}/admin/api/2026-01/metafields.json"

                        data = {
                            "metafield": {
                                "namespace": "chatbot_settings",  # Updated to the working namespace
                                "key": "api_key",
                                "value": api_key_value,
                                "type": "single_line_text_field",
                            }
                        }
                        async with httpx.AsyncClient() as client:
                            resp = await client.post(
                                metafield_url,
                                json=data,
                                headers={
                                    "X-Shopify-Access-Token": access_token,
                                    "Content-Type": "application/json",
                                },
                                timeout=15.0,
                            )
                        if resp.status_code >= 400:
                            print(f"⚠️ Metafield api_key set failed: {resp.status_code} {resp.text}")
                        else:
                            print("✅ Shop metafield api_key set successfully.")
                    else:
                        print("⚠️ No chatbot api_key to set on shop metafield.")
                except Exception as metafield_err:
                    print(f"⚠️ Error setting shop metafield api_key: {metafield_err}")

                # Ingest pages and policies first (uses current session)
                await AppController.ingest_store_content(store_id=shop_details.id)

                # Then ingest collections (custom + smart) into store_knowledge as data_type="collection"
                await AppController.ingest_shopify_collections(
                    store_id=shop_details.id,
                    shopify_domain=store_name.strip(),
                    access_token=access_token,
                )

                # Paginate through ALL products (Shopify returns 50 per page by default)
                for page in PaginatedIterator(shopify.Product.find(limit=250)):
                    for product in page:
                        print(f"Product: {product.title} (id={product.id})")
                        collections = get_product_collections(product.id)
                        collection_text = ", ".join(collections) if collections else ""

                        raw = product.to_dict()
                        clean_product = transform_shopify_product(raw, collection_text=collection_text)
                        products_list.append(clean_product)

                        if len(products_list) >= 50:
                            await Services.insert_products_to_database(products_list, chatbot_id=chatbot_id)
                            products_list = []

                if products_list:
                    await Services.insert_products_to_database(products_list, chatbot_id=chatbot_id)

                await AdminDbContoller().update_background_task_status(task_id, "completed", None)
            except Exception as e:
                print(f"error getting products background task: {e}")
                await AdminDbContoller().update_background_task_status(task_id, "failed" ,str(e))
                raise ApplicationError.SomethingWentWrong(str(e) or "Something went wrong")
            finally:
                shopify.ShopifyResource.clear_session()

        except Exception as e:
            print(f"error in get_products_background_task: {e}")
            await AdminDbContoller().update_background_task_status(task_id, "failed" ,str(e))
            raise ApplicationError.SomethingWentWrong(str(e) or "Something went wrong")


    @staticmethod
    async def ask_store(store_id: str, question: str, mode: str = "hybrid") -> str:
        """
        Query a specific store's knowledge base using LightRAG.
        Modes: 'naive', 'local', 'global', 'hybrid'
        """
        rag = await initialize_light_rag(store_id=f"store_1")
        response = await rag.aquery(
            question,
            param=QueryParam(
                mode="hybrid",
                top_k=20,
            ),
        )
        return response

    @staticmethod
    async def get_response(user: dict, request: llmrequest):
        try:
            store_id = request.store_id or f"store_{user['id']}"
            mode = request.mode or "hybrid"
            response = await AppController.ask_store(
                store_id=store_id,
                question=request.question,
                mode=mode,
            )
            return APIResponse(
                success=True,
                message="Response fetched successfully",
                data={"response": response}
            )
        except Exception as e:
            print(f"error getting response: {e}")
            error_message = getattr(e, "message", str(e))
            raise ApplicationError.SomethingWentWrong(error_message)

    @staticmethod
    async def process_orchestrator_query(user: dict, request: OrchestratorRequest):
        """
        Runs the AI orchestrator (IntentRouter + QueryExpander). Resolves store_dna from
        ecom_store when chatbot_id is provided. For HYBRID_SEARCH, runs execute_search
        and attaches search_results to the response.
        """
        store_dna = ""
        store_id = None
        store = None
        if request.chatbot_id:
            store = await AdminDbContoller().find_one_ecom_store(request.chatbot_id)
        if store is None and user.get("id"):
            # Fallback: use first ecom_store for this user so execute_search can run
            store = await AdminDbContoller().find_first_ecom_store_by_user_id(user["id"])
        if store:
            if getattr(store, "store_dna", None):
                store_dna = store.store_dna or ""
            store_id = store.id
        chat_history = request.chat_history if request.chat_history is not None else []
        pre_fetched = request.pre_fetched_orders if request.pre_fetched_orders is not None else {}

        # Derive subscription_plan from the database using the authenticated user id.
        try:
            subscription_plan = await AdminDbContoller().get_user_subscription_plan(user["id"])
        except Exception:
            subscription_plan = "starter"

        result = await ai_process_user_query(
            message=request.message,
            chat_history=chat_history,
            pre_fetched_orders=pre_fetched,
            store_dna=store_dna,
            subscription_plan=subscription_plan,
            store_name=store.store_name if store else None,
            access_token=store.access_token if store else None,
        )
        print(f"Result: {result}")

        # ORDER_SUPPORT: if prompting, return as-is; order_status is already set by orchestrator when order_id was present
        if result.get("route") == "ORDER_SUPPORT":
            return APIResponse(success=True, message="Orchestrator result", data=result)

        # When route is HYBRID_SEARCH, run the search against store_knowledge and attach results.
        if (
            result.get("route") == "HYBRID_SEARCH"
            and result.get("search_payload")
            and store_id is not None
        ):
            try:
                rows = await execute_search(store_id=store_id, payload=result["search_payload"])
                result["search_results"] = rows
            except Exception as e:
                print(f"Orchestrator execute_search error: {e}", flush=True)
                result["search_results"] = []

        return APIResponse(
            success=True,
            message="Orchestrator result",
            data=result,
        )

    @staticmethod
    async def shopify_callback(request: Request):
        try:
            # 1. Parse query params (OAuth redirect from Shopify)
            code = request.query_params.get("code")
            shop = request.query_params.get("shop")
            hmac_param = request.query_params.get("hmac")
            state = request.query_params.get("state")
            timestamp = request.query_params.get("timestamp")

            if not code or not shop:
                return APIResponse(
                    status=400,
                    message="Missing code or shop parameters.",
                    data=None,
                )

            api_key = settings.shopify_api_key
            api_secret = settings.shopify_api_secret
            if not api_key or not api_secret:
                return APIResponse(
                    status=500,
                    message="Shopify API key or secret not configured.",
                    data=None,
                )

            # 2. Validate HMAC (Shopify OAuth verification)
            shopify.Session.setup(api_key=api_key, secret=api_secret)
            params = dict(request.query_params)
            if not shopify.Session.validate_params(params):
                return APIResponse(
                    status=400,
                    message="Invalid HMAC or expired request.",
                    data=None,
                )

            # 3. Exchange code for access_token
            token_url = f"https://{shop}/admin/oauth/access_token"
            payload = {
                "client_id": api_key,
                "client_secret": api_secret,
                "code": code,
            }
            async with httpx.AsyncClient() as client:
                response = await client.post(token_url, data=payload)
                response.raise_for_status()
                token_data = response.json()

            access_token = token_data.get("access_token")
            if not access_token:
                return APIResponse(
                    status=400,
                    message="Token exchange did not return access_token.",
                    data=None,
                )

            expires_in = token_data.get("expires_in")
            if expires_in is not None:
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            else:
                expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            refresh_token = token_data.get("refresh_token") or ""

            # 4. Find existing ecom_store (created in addshopify); do NOT create here
            existing = await AdminDbContoller().find_one_ecom_store_by_shop(shop)
            if not existing:
                return APIResponse(
                    status=404,
                    message="Store not found. Complete Add Shopify flow first.",
                    data=None,
                )

            # 5. Update existing store tokens in DB (explicit UPDATE so access_token is persisted)
            await AdminDbContoller().update_ecom_store_tokens(
                ecom_store_id=existing.id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                store_name=shop,
            )

            # 6. Trigger get_products and query_expander_context background tasks for this store
            if existing.chatbot_id and existing.user_id:
                await AdminDbContoller().create_background_task(
                    user_id=existing.user_id,
                    chatbot_id=existing.chatbot_id,
                    task_type="get_products",
                    task_data=None,
                )
                await AdminDbContoller().create_background_task(
                    user_id=existing.id,  # treated as store_id for DNA generation
                    chatbot_id=existing.chatbot_id,
                    task_type="query_expander_context",
                    task_data=None,
                )

            return APIResponse(
                status=200,
                message="Shopify callback successful; access token saved.",
                data=json.dumps({"store_id": existing.id, "shop": shop}),
            )
        except httpx.HTTPStatusError as e:
            print(f"shopify_callback token exchange error: {e}")
            raise ApplicationError.SomethingWentWrong(
                f"Token exchange failed: {e.response.status_code}"
            )
        except Exception as e:
            print(f"error shopify callback: {e}")
            error_message = getattr(e, "message", str(e))
            raise ApplicationError.SomethingWentWrong(error_message)


       

            
    @staticmethod
    async def addshopify(request: AddshopifyRequest, user: dict):
        try:
            store_name = request.store_name
            print(f"[addshopify] Received store_name: {store_name}")

            shopify.Session.setup(
                api_key=settings.shopify_api_key,
                secret=settings.shopify_api_secret,
            )

            chatbot_id = await AdminDbContoller().create_chatbot({"id": user["id"]})
            print(f"[addshopify] Created chatbot with id: {getattr(chatbot_id, 'id', None)}")

            token = JWTService().generate_token({
                "user_id": user["id"],
                "username": user["username"],
                "chatbot_id": chatbot_id.id,
            })
            print(f"[addshopify] Generated JWT token: {token}")

            encrypted_api_key = encrypt_token(token)
            print(f"[addshopify] Encrypted API key: {encrypted_api_key}")

            await AdminDbContoller().update_chatbot(chatbot_id.id, {"api_key": encrypted_api_key})
            print(f"[addshopify] Updated chatbot {chatbot_id.id} with new api_key.")

            await AdminDbContoller().create_ecom_store(
                user_id=user["id"],
                chatbot_id=chatbot_id.id,
                store_name=store_name,
                store_type="shopify",
                access_token=None,
                refresh_token=None,
                expires_at=None,
                store_id=None
            )

            install_url, state = generate_shopify_install_url(store_name)
            print(f"[addshopify] Generated install URL for {store_name}; state={state}")

            return APIResponse(
                status=200,
                message="Ecom store created; redirect user to install URL.",
                data={
                    "install_url": install_url,
                    "state": state,
                    "store_name": store_name,
                },
            )
        except Exception as e:
            print(f"error adding shopify: {e}")
            error_message = getattr(e, "message", str(e))
            raise ApplicationError.SomethingWentWrong(error_message)
        