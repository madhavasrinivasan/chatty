from pickle import ADDITEMS
from llama_index_instrumentation.span_handlers import null
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import RegisterRequest, LoginRequest
from app.core.schema.schemarespone import APIResponse
from app.core.schema.applicationerror import ApplicationError
from fastapi import Request, BackgroundTasks
from app.core.services.jwt import JWTService
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from typing import List
from app.core.config import db as db_config
from app.core.services.webcrawler import Services
import bcrypt
import base64 
import time 




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
    async def upload_knowledge_base(user: dict, file_path: List[dict], request, background_tasks: BackgroundTasks = None):
        try:
             chatbot = await AdminDbContoller().create_chatbot(user)

             print(f"Chatbot: {chatbot}")

             if not chatbot or chatbot is None:
                raise ApplicationError.SomethingWentWrong("Cannot create chatbot")

             files:List[str] = []

             for file in file_path:
                file_dict:dict = {
                    "asset_type": "pdf",
                    "user_id": user["id"],
                    "chatbot_id": chatbot.id,
                    "name": file["file_name"],
                }
                files.append(file_dict) 

             for url in request.urls:
                file_dict:dict = {
                    "asset_type": "url",
                    "user_id": user["id"],
                    "chatbot_id": chatbot.id,
                    "name": request.name,
                }
                files.append(file_dict) 

           

             add_assest = await AdminDbContoller().add_assest(chatbot.id, files)

             # Add background task using FastAPI BackgroundTasks (handles async natively)
             if background_tasks:
                 background_tasks.add_task(
                     AppController.create_vectors_background_task,
                     chatbot.id,
                     request.urls if request.urls else [],
                     files,
                     user["id"]
                 )
             else:
                 # Fallback: Try RQ if available (requires separate worker process)
                 if db_config.redis_queue is not None:
                     # Note: RQ doesn't handle async functions well, would need a wrapper
                     print("Warning: Using RQ requires a separate worker process and async wrapper")
                 else:
                     print("Warning: No background task processor available")


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
            documents = []
            # Process URLs
            if urls and len(urls) > 0:
                crawl_results = await Services.crawlweb(urls)
                crawled_docs = await Services.crawl_results_to_documents(
                    crawl_results, {"chatbot_id": chatbot_id, "user_id": user_id}
                )
                documents.extend(crawled_docs)

            # Process PDF files
            if files and len(files) > 0:
               
                for file_dict in files:
                    pdf_path = file_dict.get("name") if isinstance(file_dict, dict) else file_dict
                    pdf_docs = await Services.extract_pdf_pages_readable(pdf_path)
                    for doc in pdf_docs:
                        doc["metadata"].update({
                            "source_type": "pdf",
                            "file_name": pdf_path,
                            "chatbot_id": chatbot_id,
                            "user_id": user_id,
                        })
                    documents.extend(pdf_docs)


            nodes = Services.documents_to_nodes(documents)

            nodes = await Services.embed_nodes_in_batches(nodes)

            vector_store = await AdminDbContoller().create_vector_store(nodes)

        except Exception as e:
            print(f"error creating vectors background task: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message) 
            


            
            
        