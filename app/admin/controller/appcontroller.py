from pickle import ADDITEMS
from llama_index_instrumentation.span_handlers import null
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import RegisterRequest, LoginRequest,llmrequest
from app.core.schema.schemarespone import APIResponse
from app.core.schema.applicationerror import ApplicationError
from fastapi import Request, BackgroundTasks
from app.core.services.jwt import JWTService
from Crypto.Cipher import AES
from fastapi import Request
from Crypto.Random import get_random_bytes
from typing import List, Optional
from app.core.config import db as db_config
from app.core.config.db import initialize_light_rag
from lightrag import QueryParam
from app.core.services.webcrawler import Services
from app.core.config.config import settings
from app.core.models.models import ecom_store
import bcrypt
import base64
import time
import os
from datetime import datetime, timedelta, timezone
import jwt
import httpx
import json


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
    async def shopify_callback(request: Request):
        try:
            shop = request.query_params.get("shop")
            id_token = request.query_params.get("id_token")

            if not id_token or not shop:
                return APIResponse(
                    success=False,
                    message="Missing shop or id_token parameters.",
                    data=None,
                )

            # Decode id_token (signature not verified here; Shopify validates during token exchange)
            try:
                decoded_token = jwt.decode(
                    id_token,
                    options={"verify_signature": False},
                )
            except Exception as e:
                print(f"shopify_callback jwt error: {e}")
                raise ApplicationError.SomethingWentWrong("Invalid id_token.")
            store_id = decoded_token.get("sub")  # permanent store ID

            api_key = settings.shopify_api_key
            api_secret = settings.shopify_api_secret
            if not api_key or not api_secret:
                return APIResponse(
                    success=False,
                    message="Shopify API key or secret not configured.",
                    data=None,
                )

            # Token exchange: get permanent access token
            token_url = f"https://{shop}/admin/oauth/access_token"
            payload = {
                "client_id": api_key,
                "client_secret": api_secret,
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": id_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(token_url, data=payload)
                response.raise_for_status()
                token_data = response.json()

            access_token = token_data.get("access_token")
            if not access_token:
                return APIResponse(
                    success=False,
                    message="Token exchange did not return access_token.",
                    data=None,
                )

            expires_in = token_data.get("expires_in")
            if expires_in is not None:
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            else:
                expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            refresh_token = token_data.get("refresh_token") or ""

          
            existing = await AdminDbContoller().find_one_ecom_store(store_id=store_id)
            store_name = shop  
            if existing:
                existing.access_token = access_token
                existing.refresh_token = refresh_token
                existing.expires_at = expires_at
                existing.store_name = store_name
                await existing.save()
            else:
                await AdminDbContoller().create_ecom_store(
                    user_id=1,
                    store_id=store_id,
                    store_name=store_name,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=expires_at,
                    store_type="shopify"
                )

            return APIResponse(
                status=200,
                message="Shopify callback successful; access token saved.",
                data=json.dumps({"store_id": store_id, "shop": shop}),
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
            
            
        