from pickle import ADDITEMS
from llama_index_instrumentation.span_handlers import null
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import RegisterRequest, LoginRequest,llmrequest
from app.core.schema.schemarespone import APIResponse
from app.core.schema.applicationerror import ApplicationError
from fastapi import Request, BackgroundTasks
from app.core.services.jwt import JWTService
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from typing import List
from app.core.config import db as db_config
from app.core.services.webcrawler import Services
from app.core.config.config import settings
import bcrypt
import base64 
import time
import os


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
    async def upload_knowledge_base(user: dict, file_path: List[dict], request, background_tasks: BackgroundTasks = None):
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
            documents = []
            # Process URLs
            if urls and len(urls) > 0:
                crawl_results = await Services.crawlweb(urls)
                print(f"Crawl results: {crawl_results}")
                crawled_docs = await Services.crawl_results_to_documents(
                    crawl_results, {"chatbot_id": chatbot_id, "user_id": user_id}
                )
                documents.extend(crawled_docs)


            # print(f"Documents: {documents}")
            # Process PDF files
            if files and len(files) > 0:
               
                for file_dict in files:
                    pdf_path = file_dict.get("name") if isinstance(file_dict, dict) else file_dict
                    pdf_docs = await Services.extract_pdf_pages_readable(os.path.join(directory, pdf_path))
                    print(f"PDF Docs: {pdf_docs}")
                    for pdf_doc in pdf_docs:
                        # Convert PDF doc format to match web doc format
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


            nodes = await Services.documents_to_nodes(documents)

            print(f"Nodes: {nodes}")

            embedded_nodes = await Services.embed_nodes_in_batches(nodes)

            print(f"Embedded Nodes: {embedded_nodes}")

            vector_store = await AdminDbContoller().create_vector_store(embedded_nodes);

        except Exception as e:
            print(f"error creating vectors background task: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message)  



    @staticmethod
    async def get_response(user: dict, request: llmrequest):
        try:
            generated_embedding = await Services.generate_embedding(request.question)
            print(f"Generated embedding: {generated_embedding}")
            vector_store = await AdminDbContoller().get_response(user["id"], generated_embedding)
            print(f"Vector store: {vector_store}")
            response = await Services.generate_response(vector_store)
            return APIResponse(
                success=True,
                message="Response fetched successfully",
                data=response.parsed
            )
        except Exception as e:
            print(f"error getting response: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message)

            
            
        