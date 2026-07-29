from pickle import ADDITEMS
from llama_index_instrumentation.span_handlers import null
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import RegisterRequest, LoginRequest, llmrequest, AddshopifyRequest, OrchestratorRequest, FinalFrontendResponse
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
from app.core.services.ai_orchestrator import (
    MODEL as ORCHESTRATOR_MODEL,
    process_user_query as ai_process_user_query,
)
from app.core.services.database_executor import execute_search
from app.core.services.response_synthesis import generate_final_response
from app.core.services.shopify_service import (
    generate_shopify_install_url,
    encrypt_token,
    decrypt_token,
    get_product_collections,
    transform_shopify_product,
    build_store_token_usage_response,
    format_order_support_message,
)
from app.core.services.token_tracker import merge_token_usage_payload
from tortoise.expressions import F
from app.core.services.shopify_return_service import ShopifyReturnService
from jose import jwt as jose_jwt
from jose.exceptions import JWTError
from app.core.models.models import ecom_store, store_knowledge, chatbot_settings, ChatTranscript, ChatSession, ChatMessage
import bcrypt
import re
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
    def decode_chatbot_api_key(api_key: str) -> dict:
        """
        Decrypt the API key (AES-encrypted JWT) and decode JWT to get user_id and chatbot_id.
        Returns a dict suitable for process_orchestrator_query: {"id": user_id, "chatbot_id": chatbot_id}.
        Raises ApplicationError.Unauthorized on invalid or expired key.
        """
        if not api_key or not api_key.strip():
            raise ApplicationError.Unauthorized("Invalid or expired API key")
        try:
            jwt_string = decrypt_token(api_key.strip())
            payload = jose_jwt.decode(
                jwt_string,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            user_id = payload.get("user_id")
            chatbot_id = payload.get("chatbot_id")
            if user_id is None or chatbot_id is None:
                raise ApplicationError.Unauthorized("Invalid or expired API key")
            return {"id": user_id, "chatbot_id": chatbot_id}
        except (JWTError, ValueError, Exception) as e:
            print(f"decode_chatbot_api_key error: {e}")
            raise ApplicationError.Unauthorized("Invalid or expired API key")

    @staticmethod
    async def validate_chatbot_api_key(request: Request) -> dict:
        """
        FastAPI dependency: read x-api-key header, decode it, return user-like dict for process_orchestrator_query.
        """
        api_key = request.headers.get(settings.api_key_header) or request.headers.get("chatty-api-key")
        if not api_key or not api_key.strip():
            raise ApplicationError.Unauthorized("API key not found")
        return AppController.decode_chatbot_api_key(api_key)

    
    MAX_CUSTOM_PDF_PAGES = 50

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

             files: List[dict] = []

             if file_path:
                 from pypdf import PdfReader
                 for file in file_path:
                    pdf_name = file["file_name"]
                    full_pdf_path = os.path.join(directory, pdf_name)
                    try:
                        reader = PdfReader(full_pdf_path)
                        page_count = len(reader.pages)
                    except Exception as e:
                        print(f"PDF page count failed for {pdf_name}: {e}")
                        raise ApplicationError.BadRequest(f"Could not read PDF: {pdf_name}")
                    if page_count > AppController.MAX_CUSTOM_PDF_PAGES:
                        raise ApplicationError.BadRequest(
                            f"'{pdf_name}' has {page_count} pages. Maximum allowed is {AppController.MAX_CUSTOM_PDF_PAGES}."
                        )
                    file_dict: dict = {
                        "asset_type": "pdf",
                        "user_id": user["id"],
                        "chatbot_id": chatbot.id,
                        "name": pdf_name,
                        "page_count": page_count,
                    }
                    files.append(file_dict)

             print(f"Files: {files}")

             add_assest = await AdminDbContoller().add_assest(chatbot.id, files)

             # Create background task in database (will be picked up by polling worker)
             # Signature: create_background_task(user_id, chatbot_id, task_data)
             task_data = {
                 "urls": request.urls if request.urls else [],
                 "files": files,
                 "source_name": (getattr(request, "name", None) or "").strip() or None,
             }
             await AdminDbContoller().create_background_task(user["id"], chatbot.id, task_data)

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
                message="Knowledge base uploaded; custom PDFs will be embedded in the background",
                data=chatbot_dict
             )
            

        except ApplicationError:
            raise
        except Exception as e:
            print(f"error uploading knowledge base: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message) 


    @staticmethod
    async def create_vectors_background_task(
        chatbot_id: int,
        urls: list,
        files: list,
        user_id: int,
        source_name: str | None = None,
    ):
        """
        Embed custom PDFs (and optional URL crawls) into store_knowledge as data_type='custom'
        so hybrid chat search can retrieve them (same path as Shopify pages).
        LightRAG is skipped here until GRAPH_SEARCH is wired into the hybrid flow.
        """
        try:
            print(f"Creating custom knowledge vectors for chatbot={chatbot_id} user={user_id}")
            controller = AdminDbContoller()
            splitter = AppController._CONTENT_SPLITTER

            store = None
            if chatbot_id:
                store = await controller.find_one_ecom_store(chatbot_id)
            if store is None and user_id:
                store = await controller.find_first_ecom_store_by_user_id(user_id)
            if not store:
                raise ApplicationError.NotFound(
                    "No ecom store found for this merchant; connect Shopify before uploading custom PDFs"
                )
            store_id = store.id
            display_name = (source_name or "").strip()

            all_chunks: list[dict] = []

            # --- PDFs → custom chunks (max 50 pages, enforced in extract) ---
            if files and len(files) > 0:
                for file_dict in files:
                    pdf_path = file_dict.get("name") if isinstance(file_dict, dict) else file_dict
                    if not pdf_path:
                        continue
                    full_pdf_path = os.path.join(directory, pdf_path)
                    if not os.path.isfile(full_pdf_path):
                        print(f"PDF not found on disk: {full_pdf_path}", flush=True)
                        continue
                    try:
                        pdf_docs = await Services.extract_pdf_pages_readable(
                            full_pdf_path, max_pages=AppController.MAX_CUSTOM_PDF_PAGES
                        )
                    except ValueError as ve:
                        raise ApplicationError.BadRequest(str(ve))

                    doc_label = display_name or os.path.splitext(str(pdf_path))[0]
                    file_hash = hashlib.md5(f"{store_id}:{pdf_path}".encode()).hexdigest()[:10]
                    handle = re.sub(r"[^a-z0-9\-]+", "-", doc_label.lower()).strip("-")[:200] or "custom-doc"

                    for pdf_doc in pdf_docs:
                        page_no = int(pdf_doc.get("page_number") or 0)
                        total_pages = pdf_doc.get("total_pages") or len(pdf_docs)
                        page_text = (pdf_doc.get("text") or "").strip()
                        if not page_text:
                            continue
                        title = f"{doc_label} (p.{page_no})"
                        content = (
                            f"Custom document: {doc_label}. "
                            f"Page {page_no}/{total_pages}.\n\n{page_text}"
                        )
                        doc = LangchainDocument(
                            page_content=content,
                            metadata={
                                "title": title,
                                "handle": handle,
                                "page_number": page_no,
                                "file_name": pdf_path,
                            },
                        )
                        chunks = splitter.split_documents([doc])
                        for j, ch in enumerate(chunks):
                            # Keep shopify_product_id ≤ 50 chars
                            source_id = f"c{store_id}_{file_hash}_p{page_no}_c{j}"[:50]
                            all_chunks.append({
                                "source_id": source_id,
                                "handle": handle,
                                "title": title,
                                "content": ch.page_content,
                                "url": f"file://{pdf_path}#page={page_no}",
                                "content_hash": hashlib.md5(ch.page_content.encode()).hexdigest(),
                            })

            # --- Optional URLs → custom chunks (same store_knowledge path) ---
            if urls and len(urls) > 0:
                crawl_results = await Services.crawlweb(urls)
                crawled_docs = await Services.crawl_results_to_documents(
                    crawl_results, {"chatbot_id": chatbot_id, "user_id": user_id}
                )
                for idx, node in enumerate(crawled_docs):
                    page_content = (node.get("page_content") or "").strip()
                    if not page_content:
                        continue
                    meta = node.get("metadata") or {}
                    url = meta.get("url") or f"url_{idx}"
                    title = display_name or meta.get("title") or url
                    handle = re.sub(r"[^a-z0-9\-]+", "-", str(title).lower()).strip("-")[:200] or "custom-url"
                    url_hash = hashlib.md5(f"{store_id}:{url}".encode()).hexdigest()[:10]
                    content = f"Custom document: {title}. Source: {url}.\n\n{page_content}"
                    doc = LangchainDocument(page_content=content, metadata={"title": title, "url": url})
                    chunks = splitter.split_documents([doc])
                    for j, ch in enumerate(chunks):
                        source_id = f"c{store_id}_{url_hash}_u{idx}_c{j}"[:50]
                        all_chunks.append({
                            "source_id": source_id,
                            "handle": handle,
                            "title": str(title)[:500],
                            "content": ch.page_content,
                            "url": url,
                            "content_hash": hashlib.md5(ch.page_content.encode()).hexdigest(),
                        })

            if not all_chunks:
                print("No custom chunks to embed", flush=True)
                return

            print(f"Embedding {len(all_chunks)} custom chunks into store_knowledge (store_id={store_id})", flush=True)
            batch_size = 32
            for start in range(0, len(all_chunks), batch_size):
                batch = all_chunks[start : start + batch_size]
                texts = [r["content"] for r in batch]
                embeddings = await Services.generate_batch_embeddings(texts)
                for i, row in enumerate(batch):
                    emb = embeddings[i] if i < len(embeddings) else None
                    await controller.insert_store_knowledge_raw(
                        store_id=store_id,
                        shopify_product_id=row["source_id"],
                        handle=row["handle"],
                        title=row["title"],
                        content=row["content"],
                        data_type="custom",
                        url=row["url"],
                        embedding=emb,
                        content_hash=row.get("content_hash"),
                    )

            print(
                f"✅ Custom knowledge ingest complete: {len(all_chunks)} chunks for store {store_id}",
                flush=True,
            )

        except ApplicationError:
            raise
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
    async def ingest_comez_store_content(store_id: int, store_name: str):
        """Best-effort page and policy sync for Comez from storefront endpoints."""
        controller = AdminDbContoller()
        endpoints = {
            "terms": "/user/home/getterms",
            "disclaimer": "/user/home/getdisclaimer",
            "refund": "/user/home/getrefundpolicy",
            "return": "/user/home/getreturnpolicy",
            "shipping": "/user/home/getshipingpolicy",
            "about": "/user/home/getactiveabout",
        }
        
        headers = {
            "storename": store_name,
            "x-custom-domain": "false",
        }

        print("📖 Ingesting Comez Pages and Policies...")
        for name, path in endpoints.items():
            url = f"{settings.comez_base_url}{path}"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, headers=headers, json={})
                
                if resp.status_code != 200:
                    continue
                
                res_data = resp.json().get("data") or {}
                content_text = ""
                title = f"{name.capitalize()} Policy"
                
                if isinstance(res_data, list) and res_data:
                    chunks = []
                    for row in res_data:
                        if isinstance(row, dict):
                            chunks.append(row.get("content") or row.get("description") or row.get("name") or "")
                    content_text = "\n\n".join([c for c in chunks if c])
                elif isinstance(res_data, dict):
                    content_text = res_data.get("content") or res_data.get("description") or res_data.get("name") or ""
                    if res_data.get("title"):
                        title = res_data["title"]
                
                if not content_text or len(content_text.strip()) < 10:
                    continue
                
                soup = BeautifulSoup(content_text, "html.parser")
                clean_text = soup.get_text(separator=" ").strip()
                full_content = f"{title}: {clean_text}"
                embedding = await Services.generate_embedding(full_content)
                
                handle = name.lower().replace(" ", "-")
                shopify_product_id = f"comez_policy_{store_id}_{name}"
                url_path = f"/policies/{handle}" if name != "about" else "/about"
                
                await controller.insert_store_knowledge_raw(
                    store_id=store_id,
                    shopify_product_id=shopify_product_id,
                    handle=handle,
                    title=title,
                    content=full_content,
                    data_type="page",
                    url=url_path,
                    embedding=embedding,
                )
                print(f"✅ Ingested Comez page: {title}")
            except Exception as e:
                print(f"⚠️ Error ingesting Comez page {name}: {e}")

    @staticmethod
    async def get_products_background_task(chatbot_id: int, store_id: int , task_id: int | None = None):
        try:
            print(f"Getting products background task for chatbot: {chatbot_id}")

            shop_details = await AdminDbContoller().find_one_ecom_store(chatbot_id=chatbot_id)
            if not shop_details or shop_details is None:
                raise ApplicationError.SomethingWentWrong("Cannot find ecom store")

            store_name = shop_details.store_name or ""
            access_token = shop_details.access_token or ""
            if not store_name.strip():
                raise ApplicationError.SomethingWentWrong(
                    "Store has no store name; please configure it first."
                )

            # Mark indexing as in-progress for the admin sync view.
            try:
                await ecom_store.filter(id=shop_details.id).update(
                    sync_status="syncing",
                    last_synced_at=datetime.now(timezone.utc),
                )
            except Exception:
                # Non-fatal: indexing still proceeds even if sync flags fail.
                pass

            if shop_details.store_type == "comez":
                from app.core.services.comez_service import ComezService
                try:
                    print(f"📖 Fetching products from Comez for: {store_name}")
                    raw_products = await ComezService.fetch_all_products(
                        store_name.strip(),
                        custom_domain=bool(getattr(shop_details, "custom_domain", False)),
                        x_store=getattr(shop_details, "x_store", None) or store_name.strip(),
                        access_token=getattr(shop_details, "access_token", None),
                    )
                    print(f"✅ Fetched {len(raw_products)} raw products from Comez.")
                    
                    products_list = []
                    for item in raw_products:
                        if not isinstance(item, dict):
                            continue
                        if item.get("status") == "inactive":
                            continue
                            
                        clean_product = ComezService.transform_comez_product(item)
                        products_list.append(clean_product)
                        
                        if len(products_list) >= 50:
                            await Services.insert_products_to_database(products_list, chatbot_id=chatbot_id)
                            products_list = []
                            
                    if products_list:
                        await Services.insert_products_to_database(products_list, chatbot_id=chatbot_id)
                    print("✅ Comez products inserted to database.")

                    await AppController.ingest_comez_store_content(shop_details.id, store_name.strip())
                    # Store DNA is generated by the query_expander_context background task
                    # (enqueued at onboard/sync). Do not block product sync on DNA here.

                    if task_id is not None:
                        await AdminDbContoller().update_background_task_status(task_id, "completed", None)

                    try:
                        await ecom_store.filter(id=shop_details.id).update(sync_status="idle")
                    except Exception:
                        pass
                    return
                except Exception as e:
                    print(f"error getting products background task for Comez: {e}")
                    if task_id is not None:
                        await AdminDbContoller().update_background_task_status(task_id, "failed", str(e))
                    try:
                        await ecom_store.filter(id=shop_details.id).update(sync_status="failed")
                    except Exception:
                        pass
                    raise ApplicationError.SomethingWentWrong(str(e) or "Something went wrong")

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

                if task_id is not None:
                    await AdminDbContoller().update_background_task_status(task_id, "completed", None)

                # Indexing complete.
                try:
                    await ecom_store.filter(id=shop_details.id).update(sync_status="idle")
                except Exception:
                    pass
            except Exception as e:
                print(f"error getting products background task: {e}")
                if task_id is not None:
                    await AdminDbContoller().update_background_task_status(task_id, "failed" ,str(e))
                # Indexing failed.
                try:
                    await ecom_store.filter(id=shop_details.id).update(sync_status="failed")
                except Exception:
                    pass
                raise ApplicationError.SomethingWentWrong(str(e) or "Something went wrong")
            finally:
                shopify.ShopifyResource.clear_session()

        except Exception as e:
            print(f"error in get_products_background_task: {e}")
            if task_id is not None:
                await AdminDbContoller().update_background_task_status(task_id, "failed" ,str(e))
            # Ensure sync_status flips to failed even if the error happens early.
            try:
                # shop_details may be undefined here; best-effort only.
                if "shop_details" in locals() and shop_details:
                    await ecom_store.filter(id=shop_details.id).update(sync_status="failed")
            except Exception:
                pass
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
        import time as _time
        _t0 = _time.perf_counter()

        print(f"order history: {request.order_history} {request.previous_session_history} {request.user_facts}")
        store_dna = ""
        store_id = None
        store = None
        chatbot_id = request.chatbot_id or user.get("chatbot_id")
        if chatbot_id:
            store = await AdminDbContoller().find_one_ecom_store(chatbot_id)
        if store is None and user.get("id"):
            # Fallback: use first ecom_store for this user so execute_search can run
            store = await AdminDbContoller().find_first_ecom_store_by_user_id(user["id"])
        if store:
            if getattr(store, "store_dna", None):
                store_dna = store.store_dna or ""
            store_id = store.id


        if request.action_payload and request.action_payload.get("action_type") == "SUBMIT_RETURN":
            order_number = (request.action_payload.get("order_number") or "").strip()
            items = request.action_payload.get("items") or []
            if not order_number or not store or not getattr(store, "access_token", None):
                return APIResponse(
                    success=False,
                    message="Missing order number or store credentials",
                    data={"final_response": {"general_answer": "Unable to submit return: missing order or store configuration."}},
                )
            try:
                return_svc = ShopifyReturnService(store.store_name or "", store.access_token or "")
                submit_result = await return_svc.submit_return_request(order_number, items)
            except Exception as e:
                return APIResponse(
                    success=False,
                    message="Return submission failed",
                    data={"final_response": {"general_answer": f"Sorry, we couldn't submit your return: {e!s}"}},
                )
            if not submit_result.get("ok"):
                return APIResponse(
                    success=False,
                    message="Return submission failed",
                    data={"final_response": {"general_answer": submit_result.get("error", "Return request failed.")}},
                )
            # Append user system message and assistant success message to active chat history in the database
            user_system_msg = "[SYSTEM: User submitted return form]"
            assistant_success_msg = "I have successfully submitted your return request!"
            session_id = (request.session_id or "").strip()
            existing_history = list(request.chat_history or [])
            new_messages = [
                {"role": "user", "content": user_system_msg},
                {"role": "assistant", "content": assistant_success_msg},
            ]
            updated_history = existing_history + new_messages
            if session_id:
                transcript = await ChatTranscript.get_or_none(session_id=session_id)
                if transcript:
                    await transcript.update(raw_history=updated_history)
                else:
                    await ChatTranscript.create(
                        session_id=session_id,
                        store_id=store.id,
                        user_email=None,
                        raw_history=updated_history,
                    )
            final = FinalFrontendResponse(
                general_answer=assistant_success_msg,
                urls=[],
                products=[],
                suggested_actions=["Track another order?", "Browse products?"],
                order_status=[],
                return_ui_items=[],
            )
            return APIResponse(
                success=True,
                message="Return submitted",
                data={"final_response": final.model_dump(), "route": "RETURN_REQUEST"},
            )

        chat_history = request.chat_history if request.chat_history is not None else []
        print(f"Chat history: {chat_history}")
        pre_fetched = request.pre_fetched_orders if request.pre_fetched_orders is not None else {}
        print(f"Pre fetched: {pre_fetched}")
        user_facts = (request.user_facts or "").strip()
        print(f"User facts: {user_facts}")
        order_history = (request.order_history or "").strip()
        previous_session_history = (request.previous_session_history or "").strip()

        # Derive subscription_plan from the database using the authenticated user id.
        try:
            subscription_plan = await AdminDbContoller().get_user_subscription_plan(user["id"])
        except Exception:
            subscription_plan = "starter"

        _t1 = _time.perf_counter()
        print(f"  ⏱️ [CTRL] Store lookup + subscription: {_t1 - _t0:.2f}s")

        result = await ai_process_user_query(
            message=request.message,
            chat_history=chat_history,
            pre_fetched_orders=pre_fetched,
            store_dna=store_dna,
            subscription_plan=subscription_plan,
            store_name=store.store_name if store else None,
            access_token=store.access_token if store else None,
            user_facts=user_facts,
            order_history=order_history,
            previous_session_history=previous_session_history,
            store_type=store.store_type if store else None,
            store=store,
        )
        _t2 = _time.perf_counter()
        print(f"  ⏱️ [CTRL] Unified Router+Expander (LLM #1): {_t2 - _t1:.2f}s  |  route={result.get('route')}")
        print(f"Result: {result}")


        if result.get("route") == "ORDER_SUPPORT":
            order_status_payload = result.get("order_status")
            order_status_list = [order_status_payload] if order_status_payload is not None else []
            if result.get("prompting"):
                general_answer = "Please share your order number so I can look it up."
            elif order_status_list:
                o = order_status_list[0] if isinstance(order_status_list[0], dict) else {}
                if o.get("found"):
                    general_answer = format_order_support_message(o)
                else:
                    general_answer = o.get("message", "We couldn’t find that order. Please check the number and try again.")
            else:
                general_answer = "Please share your order number so I can look it up."
            final = FinalFrontendResponse(
                general_answer=general_answer,
                urls=[],
                products=[],
                suggested_actions=["Track another order?", "Browse products?", "What’s my shipping status?"],
                order_status=order_status_list,
            )
            result["final_response"] = final.model_dump()


        if result.get("route") == "GENERAL_CHAT":
            conversational = result.get("conversational_response") or ""
            final = FinalFrontendResponse(
                general_answer=conversational,
                urls=[],
                products=[],
                suggested_actions=[
                    "What products do you have?",
                    "Do you have a size guide?",
                    "Can you help me find a gift?",
                ],
            )
            result["final_response"] = final.model_dump()

        # FOLLOW_UP_QUESTION: LLM asked for clarification; return in final_response format (same as ORDER_SUPPORT prompting / GENERAL_CHAT)
        if result.get("route") == "FOLLOW_UP_QUESTION":
            follow_up = result.get("follow_up_message") or "Could you tell me a bit more so I can help you better?"
            final = FinalFrontendResponse(
                general_answer=follow_up,
                urls=[],
                products=[],
                suggested_actions=[],
            )
            result["final_response"] = final.model_dump()

        # RETURN_REQUEST: Return Specialist reply; Output Interceptor for FETCH_ORDER, then CREATE_RETURN eligibility
        if result.get("route") == "RETURN_REQUEST":
            return_reply = result.get("return_specialist_response") or "I can help with returns. Please share your order number, the item, and the reason."
            order_status_list = []
            return_ui_items: List[dict] = []
            return_order_number: Optional[str] = None

            # Phase 1: Detect [ACTION:FETCH_ORDER | order: #<order_number>], fetch line items for UI, strip tag
            fetch_order_match = re.search(
                r"\[ACTION:FETCH_ORDER\s*\|\s*order:\s*#?([^\s\]|]+)\]",
                return_reply,
                re.IGNORECASE,
            )
            if fetch_order_match and store and getattr(store, "access_token", None):
                order_number = (fetch_order_match.group(1) or "").strip()
                return_order_number = f"#{order_number}" if order_number and not order_number.startswith("#") else order_number or None
                try:
                    return_svc = ShopifyReturnService(
                        store.store_name or "",
                        store.access_token or "",
                    )
                    return_ui_items = await return_svc.fetch_order_line_items_for_ui(order_number)
                except Exception:
                    return_ui_items = []
                return_reply = re.sub(
                    r"\[ACTION:FETCH_ORDER\s*\|\s*order:\s*#?[^\s\]|]*\]",
                    "",
                    return_reply,
                    flags=re.IGNORECASE,
                ).strip()

            # CREATE_RETURN: submit return eligibility check
            action_match = re.search(
                r"\[ACTION:CREATE_RETURN\s*\|\s*order:\s*#?([^\s|]+)\s*\|\s*item:\s*(.+?)\s*\|\s*reason:\s*(.+)\]",
                return_reply,
                re.IGNORECASE | re.DOTALL,
            )
            if action_match and store and getattr(store, "access_token", None):
                order_number = (action_match.group(1) or "").strip()
                item_title = (action_match.group(2) or "").strip()
                reason = (action_match.group(3) or "").strip()
                try:
                    return_svc = ShopifyReturnService(
                        store.store_name or "",
                        store.access_token or "",
                    )
                    eligibility = await return_svc.fetch_return_eligibility(order_number, item_title or None)
                    order_status_list = [eligibility]
                except Exception as e:
                    order_status_list = [{"ok": False, "error": str(e)}]
                return_reply = re.sub(
                    r"\[ACTION:CREATE_RETURN\s*\|\s*order:\s*#?[^\s|]+\s*\|\s*item:\s*[^|]+\s*\|\s*reason:\s*[^\]]+\]",
                    "",
                    return_reply,
                    flags=re.IGNORECASE | re.DOTALL,
                ).strip()

            final = FinalFrontendResponse(
                general_answer=return_reply,
                urls=[],
                products=[],
                suggested_actions=["Track another order?", "Browse products?"],
                order_status=order_status_list,
                return_ui_items=return_ui_items,
                order_number=return_order_number,
            )
            result["final_response"] = final.model_dump()

        if (
            result.get("route") == "HYBRID_SEARCH"
            and result.get("search_payload")
            and store_id is not None
        ):
            try:
                _t_db_start = _time.perf_counter()
                rows = await execute_search(store_id=store_id, payload=result["search_payload"])
                _t_db_end = _time.perf_counter()
                print(f"  ⏱️ [CTRL] DB Search (execute_search): {_t_db_end - _t_db_start:.2f}s  |  {len(rows)} rows")

                # Enrich hybrid search results with discount_info when discounts were requested.
                discounts = result.get("discounts") or []

                def _matches_product(discount: dict, product_gid: str) -> bool:
                    if not discount:
                        return False
                    entitled_products = discount.get("entitled_product_ids") or []
                    if not entitled_products:
                        # No explicit entitlements => treat as global (applies to all products)
                        return True

                    def _extract_id(gid: str) -> str:
                        s = str(gid).strip()
                        return s.split("/")[-1] if "/" in s else s

                    pid = _extract_id(product_gid)
                    return any(_extract_id(e) == pid for e in entitled_products)

                for row in rows:
                    pid = str(row.get("shopify_product_id") or row.get("id") or "").strip()
                    if not pid:
                        continue
                    row_discounts: list[dict] = []
                    for d in discounts:
                        try:
                            if _matches_product(d, pid):
                                row_discounts.append(
                                    {
                                        "code": d.get("code"),
                                        "title": d.get("title"),
                                        "type": d.get("type"),
                                        "value": d.get("value"),
                                        "currency": d.get("currency"),
                                    }
                                )
                        except Exception:
                            continue
                    if row_discounts:
                        row["discount_info"] = row_discounts

                result["search_results"] = rows
                # LLM synthesis + variant match + inventory check -> FinalFrontendResponse
                try:
                    _t_synth_start = _time.perf_counter()
                    final, synth_usage = await generate_final_response(
                        user_query=request.message or "",
                        hybrid_results=rows,
                        shop_domain=store.store_name if store else "",
                        db_session=None,
                        access_token=store.access_token if store else "",
                        store_id=store_id,
                        user_facts=user_facts,
                        order_history=order_history,
                        previous_session_history=previous_session_history,
                        active_chat_history=chat_history,
                        cart_items=request.cart_items,
                    )
                    _t_synth_end = _time.perf_counter()
                    print(f"  ⏱️ [CTRL] Response Synthesis (LLM #2 + variant + inventory): {_t_synth_end - _t_synth_start:.2f}s")
                    print(f"  ⏱️ [CTRL] HYBRID_SEARCH total: {_t_synth_end - _t2:.2f}s")
                    print(f"final: {final}")
                    result["final_response"] = final.model_dump()
                    merged = merge_token_usage_payload(
                        result.get("token_usage"),
                        synth_usage,
                        ORCHESTRATOR_MODEL,
                    )
                    if merged:
                        result["token_usage"] = merged
                except Exception as syn_err:
                    print(f"Response synthesis error: {syn_err}", flush=True)
                    result["final_response"] = None
            except Exception as e:
                print(f"Orchestrator execute_search error: {e}", flush=True)
                result["search_results"] = []

        try:
            await AppController._persist_orchestrator_token_usage(
                store_id,
                (request.session_id or "").strip() or None,
                result.get("token_usage"),
            )
        except Exception as persist_err:
            print(f"Token usage persist skipped: {persist_err}", flush=True)

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

            # 6. Trigger get_products then query_expander_context (store DNA).
            # DNA task user_id MUST be ecom_store.id (worker passes it as store_id).
            if existing.chatbot_id and existing.user_id:
                await AdminDbContoller().create_background_task(
                    user_id=existing.user_id,
                    chatbot_id=existing.chatbot_id,
                    task_type="get_products",
                    task_data={"store_id": existing.id},
                )
                await AdminDbContoller().create_background_task(
                    user_id=existing.id,
                    chatbot_id=existing.chatbot_id,
                    task_type="query_expander_context",
                    task_data={"store_id": existing.id},
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

    @staticmethod
    async def addcomez(request, user: dict):
        """
        Register / update a Comez store for the logged-in admin.
        No OAuth — JWT + storefront URL are saved on ecom_store; sync task is enqueued.
        """
        try:
            store_name = (request.store_name or "").strip()
            access_token = (request.access_token or "").strip()
            storefront_url = (request.storefront_url or "").strip().rstrip("/")
            custom_domain = bool(getattr(request, "custom_domain", False))
            x_store = (getattr(request, "x_store", None) or store_name).strip()

            if not store_name:
                raise ApplicationError.BadRequest("store_name is required")
            if not access_token:
                raise ApplicationError.BadRequest("access_token (Comez JWT) is required")
            if not storefront_url:
                raise ApplicationError.BadRequest("storefront_url is required")
            if not storefront_url.startswith("http://") and not storefront_url.startswith("https://"):
                storefront_url = f"https://{storefront_url}"

            controller = AdminDbContoller()
            chatbot = await controller.create_chatbot({"id": user["id"]})

            token = JWTService().generate_token({
                "user_id": user["id"],
                "username": user.get("username") or user.get("email") or str(user["id"]),
                "chatbot_id": chatbot.id,
            })
            encrypted_api_key = encrypt_token(token)
            await controller.update_chatbot(chatbot.id, {"api_key": encrypted_api_key})

            existing = await controller.find_first_ecom_store_by_user_id(user["id"])
            if existing and str(getattr(existing, "store_type", "") or "").lower() in ("comez",):
                await controller.update_comez_store_connection(
                    store_id=existing.id,
                    access_token=access_token,
                    storefront_url=storefront_url,
                    custom_domain=custom_domain,
                    x_store=x_store,
                    store_name=store_name,
                )
                store = await controller.find_first_ecom_store_by_user_id(user["id"])
            elif existing and not existing.access_token and (getattr(existing, "store_type", None) in (None, "comez") or str(existing.store_type) == "comez"):
                await controller.update_comez_store_connection(
                    store_id=existing.id,
                    access_token=access_token,
                    storefront_url=storefront_url,
                    custom_domain=custom_domain,
                    x_store=x_store,
                    store_name=store_name,
                )
                store = await ecom_store.filter(id=existing.id).first()
                if store:
                    store.chatbot_id = chatbot.id
                    store.store_type = "comez"
                    await store.save()
            else:
                store = await controller.create_ecom_store(
                    user_id=user["id"],
                    chatbot_id=chatbot.id,
                    store_id=f"comez_{store_name.replace('-', '_')}",
                    store_name=store_name,
                    access_token=access_token,
                    refresh_token=None,
                    expires_at=None,
                    store_type="comez",
                    storefront_url=storefront_url,
                    custom_domain=custom_domain,
                    x_store=x_store,
                )

            # Enqueue catalog sync + store DNA (DNA uses ecom_store.id as user_id)
            store_pk = store.id if store else None
            await controller.create_background_task(
                user["id"],
                chatbot.id,
                {"store_id": store_pk},
                task_type="get_products",
            )
            if store_pk is not None:
                await controller.create_background_task(
                    store_pk,
                    chatbot.id,
                    {"store_id": store_pk},
                    task_type="query_expander_context",
                )

            return APIResponse(
                status=200,
                message="Comez store connected. Catalog sync queued.",
                data={
                    "store_id": store_pk,
                    "store_name": store_name,
                    "storefront_url": storefront_url,
                    "custom_domain": custom_domain,
                    "x_store": x_store,
                    "store_type": "comez",
                    "chatbot_id": chatbot.id,
                    "api_key": encrypted_api_key,
                    "sync_status": "queued",
                },
            )
        except ApplicationError:
            raise
        except Exception as e:
            print(f"error adding comez: {e}")
            error_message = getattr(e, "message", str(e))
            raise ApplicationError.SomethingWentWrong(error_message)

    # ============================
    # Orchestrator LLM usage persistence (per store + per chat session)
    # ============================

    @staticmethod
    async def _persist_orchestrator_token_usage(
        store_id: int | None,
        session_id: str | None,
        token_usage: dict | None,
    ) -> None:
        if not token_usage or not token_usage.get("totals"):
            return
        totals = token_usage["totals"]
        try:
            tin = int(totals.get("input_tokens", 0))
            tout = int(totals.get("output_tokens", 0))
            cost = float(totals.get("total_cost_usd", 0.0))
        except (TypeError, ValueError):
            return
        if tin == 0 and tout == 0:
            return
        if store_id:
            await ecom_store.filter(id=store_id).update(
                total_input_tokens=F("total_input_tokens") + tin,
                total_output_tokens=F("total_output_tokens") + tout,
                total_cost_usd=F("total_cost_usd") + cost,
            )
        if session_id:
            from uuid import UUID as _UUID

            try:
                su = _UUID(session_id.strip())
            except Exception:
                return
            await ChatSession.filter(id=su).update(
                input_tokens_total=F("input_tokens_total") + tin,
                output_tokens_total=F("output_tokens_total") + tout,
                estimated_cost_usd_total=F("estimated_cost_usd_total") + cost,
                tokens_used=F("tokens_used") + tin + tout,
            )

    @staticmethod
    async def get_merchant_llm_usage(user: dict) -> APIResponse:
        """Dashboard: cumulative estimated LLM usage for the merchant's primary store."""
        store = await AdminDbContoller().find_first_ecom_store_by_user_id(user["id"])
        if not store:
            raise ApplicationError.NotFound("No store found")
        tin = int(getattr(store, "total_input_tokens", 0) or 0)
        tout = int(getattr(store, "total_output_tokens", 0) or 0)
        tcost = float(getattr(store, "total_cost_usd", 0.0) or 0.0)
        from app.core.services.token_tracker import usd_to_inr, USD_TO_INR
        return APIResponse(
            success=True,
            message="LLM usage (tiktoken estimates; see token_usage.components per request for breakdown)",
            data={
                "store_id": store.id,
                "total_input_tokens": tin,
                "total_output_tokens": tout,
                "total_cost_usd": round(tcost, 8),
                "total_cost_inr": usd_to_inr(tcost),
                "currency": "INR",
                "usd_to_inr_rate": USD_TO_INR,
            },
        )

    # ============================
    # Store token usage (tiktoken estimate over store_knowledge)
    # ============================

    @staticmethod
    async def get_store_token_usage(user: dict) -> APIResponse:
        """
        Tiktoken estimate over all `store_knowledge` rows for the store tied to the chatbot API key.
        """
        store = None
        if user.get("chatbot_id"):
            store = await AdminDbContoller().find_one_ecom_store(user["chatbot_id"])
        if store is None and user.get("id"):
            store = await AdminDbContoller().find_first_ecom_store_by_user_id(user["id"])
        if not store:
            raise ApplicationError.NotFound("No store found")

        rows = await AdminDbContoller().fetch_store_knowledge_rows_for_token_count(
            ecom_store_id=store.id,
            chatbot_id=store.chatbot_id,
        )
        data = build_store_token_usage_response(rows, store_id=store.id)
        return APIResponse(
            success=True,
            message="Token usage estimate (tiktoken over store_knowledge)",
            data=data,
        )

    # ============================
    # Admin Live Chat - business orchestration
    # ============================

    @staticmethod
    async def list_chat_sessions(user: dict):
        sessions = await AdminDbContoller().list_active_chat_sessions_for_user(user["id"])
        return APIResponse(success=True, message="Sessions fetched", data=sessions)

    @staticmethod
    async def list_chat_session_messages(user: dict, session_id: str):
        from uuid import UUID as _UUID

        try:
            sid = _UUID(session_id)
        except Exception:
            raise ApplicationError.BadRequest("Invalid session_id")

        messages = await AdminDbContoller().list_chat_messages_for_session_for_user(user_id=user["id"], session_id=sid)
        if messages is None:
            # Keep behavior consistent with existing controllers: raise application error.
            raise ApplicationError.NotFound("Session not found")
        return APIResponse(success=True, message="Messages fetched", data={"session_id": session_id, "messages": messages})

    @staticmethod
    async def get_sync_status(user: dict):
        status = await AdminDbContoller().get_sync_status_for_user(user["id"])
        return APIResponse(success=True, message="Sync status fetched", data=status)

    @staticmethod
    async def trigger_sync(user: dict, background_tasks: BackgroundTasks | None = None):
        store = await AdminDbContoller().find_first_ecom_store_by_user_id(user["id"])
        if not store:
            raise ApplicationError.NotFound("No store found")
        if not store.chatbot_id:
            raise ApplicationError.BadRequest("Store has no chatbot_id")

        now = datetime.now(timezone.utc)
        await AdminDbContoller().set_store_sync_status(
            store.id, last_synced_at=now, sync_status="syncing"
        )

        await AdminDbContoller().create_background_task(
            user_id=store.user_id,
            chatbot_id=store.chatbot_id,
            task_data={"store_id": store.id},
            task_type="get_products",
        )
        await AdminDbContoller().create_background_task(
            user_id=store.id,
            chatbot_id=store.chatbot_id,
            task_data={"store_id": store.id},
            task_type="query_expander_context",
        )

        return APIResponse(
            success=True,
            message="Sync enqueued",
            data={"sync_status": "syncing", "last_synced_at": now.isoformat()},
        )

    @staticmethod
    async def get_chatbot_customization(user: dict):
        store = await AdminDbContoller().find_first_ecom_store_by_user_id(user["id"])
        if not store:
            raise ApplicationError.NotFound("No store found")
        custom = await AdminDbContoller().get_chatbot_customization(store.id)
        return APIResponse(
            success=True,
            message="Customization settings fetched",
            data={
                "bot_name": custom.bot_name,
                "greeting_message": custom.greeting_message,
                "logo_url": custom.logo_url,
                "avatar_url": custom.avatar_url,
                "primary_color": custom.primary_color,
                "secondary_color": custom.secondary_color,
                "background_color": custom.background_color,
                "text_color": custom.text_color,
                "user_bubble_color": custom.user_bubble_color,
                "bot_bubble_color": custom.bot_bubble_color,
                "font_family": custom.font_family,
                "font_size_base": custom.font_size_base,
                "widget_position": custom.widget_position,
                "border_radius": custom.border_radius,
                "button_icon_style": custom.button_icon_style,
                "send_button_color": custom.send_button_color,
                "other_color": custom.other_color,
                "sample_questions": custom.sample_questions or [],
                "system_prompt_override": custom.system_prompt_override,
                "updated_at": custom.updated_at.isoformat() if custom.updated_at else None,
            }
        )

    @staticmethod
    async def update_chatbot_customization(user: dict, body: dict):
        store = await AdminDbContoller().find_first_ecom_store_by_user_id(user["id"])
        if not store:
            raise ApplicationError.NotFound("No store found")
        
        update_data = {k: v for k, v in body.items() if v is not None}
        custom = await AdminDbContoller().update_chatbot_customization(store.id, update_data)

        # Sync customization to template_json in chatbot_settings table
        custom_data = {
            "bot_name": custom.bot_name,
            "greeting_message": custom.greeting_message,
            "logo_url": custom.logo_url,
            "avatar_url": custom.avatar_url,
            "primary_color": custom.primary_color,
            "secondary_color": custom.secondary_color,
            "background_color": custom.background_color,
            "text_color": custom.text_color,
            "user_bubble_color": custom.user_bubble_color,
            "bot_bubble_color": custom.bot_bubble_color,
            "font_family": custom.font_family,
            "font_size_base": custom.font_size_base,
            "widget_position": custom.widget_position,
            "border_radius": custom.border_radius,
            "button_icon_style": custom.button_icon_style,
            "send_button_color": custom.send_button_color,
            "other_color": custom.other_color,
            "sample_questions": custom.sample_questions or [],
            "system_prompt_override": custom.system_prompt_override,
        }

        if store.chatbot_id:
            await chatbot_settings.filter(id=store.chatbot_id).update(template_json=custom_data)
        else:
            await chatbot_settings.filter(user_id=user["id"]).update(template_json=custom_data)

        return APIResponse(
            success=True,
            message="Customization settings updated",
            data={
                "bot_name": custom.bot_name,
                "greeting_message": custom.greeting_message,
                "logo_url": custom.logo_url,
                "avatar_url": custom.avatar_url,
                "primary_color": custom.primary_color,
                "secondary_color": custom.secondary_color,
                "background_color": custom.background_color,
                "text_color": custom.text_color,
                "user_bubble_color": custom.user_bubble_color,
                "bot_bubble_color": custom.bot_bubble_color,
                "font_family": custom.font_family,
                "font_size_base": custom.font_size_base,
                "widget_position": custom.widget_position,
                "border_radius": custom.border_radius,
                "button_icon_style": custom.button_icon_style,
                "send_button_color": custom.send_button_color,
                "other_color": custom.other_color,
                "sample_questions": custom.sample_questions or [],
                "system_prompt_override": custom.system_prompt_override,
                "updated_at": custom.updated_at.isoformat() if custom.updated_at else None,
            }
        )


    @staticmethod
    async def get_public_chatbot_customization(storefront_info: dict):
        chatbot_id = storefront_info.get("chatbot_id")
        store = await AdminDbContoller().find_one_ecom_store(chatbot_id)
        if not store:
            raise ApplicationError.NotFound("No store found for this chatbot_id")
        custom = await AdminDbContoller().get_chatbot_customization(store.id)
        store_type = getattr(store, "store_type", None)
        store_type_val = getattr(store_type, "value", store_type) or "shopify"
        return APIResponse(
            success=True,
            message="Public customization settings fetched",
            data={
                "bot_name": custom.bot_name,
                "greeting_message": custom.greeting_message,
                "logo_url": custom.logo_url,
                "avatar_url": custom.avatar_url,
                "primary_color": custom.primary_color,
                "secondary_color": custom.secondary_color,
                "background_color": custom.background_color,
                "text_color": custom.text_color,
                "user_bubble_color": custom.user_bubble_color,
                "bot_bubble_color": custom.bot_bubble_color,
                "font_family": custom.font_family,
                "font_size_base": custom.font_size_base,
                "widget_position": custom.widget_position,
                "border_radius": custom.border_radius,
                "button_icon_style": custom.button_icon_style,
                "send_button_color": custom.send_button_color,
                "other_color": custom.other_color,
                "sample_questions": custom.sample_questions or [],
                "system_prompt_override": custom.system_prompt_override,
                "updated_at": custom.updated_at.isoformat() if custom.updated_at else None,
                "store_type": str(store_type_val),
                "storefront_url": getattr(store, "storefront_url", None),
                "store_name": store.store_name,
                "custom_domain": bool(getattr(store, "custom_domain", False)),
                "x_store": getattr(store, "x_store", None) or store.store_name,
            }
        )


    @staticmethod
    async def get_sync_summary(user: dict):
        summary = await AdminDbContoller().get_sync_summary_for_user(user["id"])
        return APIResponse(
            success=True,
            message="Knowledge base sync summary fetched",
            data=summary
        )

    @staticmethod
    async def set_session_needs_human(user: dict, session_id: str, needs_human: bool):
        from uuid import UUID as _UUID
        try:
            sid = _UUID(session_id)
        except Exception:
            raise ApplicationError.BadRequest("Invalid session_id")
            
        success = await AdminDbContoller().set_session_needs_human(sid, needs_human)
        if not success:
            raise ApplicationError.NotFound("Session not found")
        return APIResponse(
            success=True,
            message=f"Session needs_human set to {needs_human}",
            data={"session_id": session_id, "needs_human": needs_human}
        )

    @staticmethod
    async def track_add_to_cart(user: dict, body) -> APIResponse:
        """Widget: log a successful Add to cart click for dashboard revenue attribution."""
        store = None
        if user.get("chatbot_id"):
            store = await AdminDbContoller().find_one_ecom_store(user["chatbot_id"])
        if store is None and user.get("id"):
            store = await AdminDbContoller().find_first_ecom_store_by_user_id(user["id"])
        if not store:
            raise ApplicationError.NotFound("No store found")

        qty = max(1, int(getattr(body, "quantity", 1) or 1))
        unit_price = float(getattr(body, "unit_price", 0.0) or 0.0)
        if unit_price < 0:
            unit_price = 0.0
        line_revenue = round(unit_price * qty, 2)
        currency = (getattr(body, "currency", None) or "INR").strip()[:8] or "INR"
        shop_domain = (getattr(body, "shop_domain", None) or "").strip() or None
        if shop_domain:
            shop_domain = AdminDbContoller()._normalize_shop_domain(shop_domain) or shop_domain

        event = await AdminDbContoller().create_add_to_cart_event(
            store_id=store.id,
            chatbot_id=user.get("chatbot_id") or store.chatbot_id,
            session_id=(getattr(body, "session_id", None) or None),
            shop_domain=shop_domain,
            product_id=getattr(body, "product_id", None),
            variant_id=getattr(body, "variant_id", None),
            title=getattr(body, "title", None),
            quantity=qty,
            unit_price=unit_price,
            currency=currency,
            line_revenue=line_revenue,
        )
        return APIResponse(
            success=True,
            message="Add to cart event logged",
            data={
                "id": event.id,
                "line_revenue": line_revenue,
                "quantity": qty,
            },
        )

    @staticmethod
    async def get_dashboard_stats(user: dict) -> APIResponse:
        """Admin Overview: ATC clicks, attributed revenue, queries, tokens + cost."""
        stats = await AdminDbContoller().get_dashboard_stats_for_user(user["id"])
        return APIResponse(
            success=True,
            message="Dashboard stats fetched",
            data=stats,
        )
        