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
    async def upload_knowledge_base(user: dict, file_path: List[dict], request):
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

             BackgroundTasks.add_task(AppController.create_vectors_background_task(chatbot.id)) 


             return APIResponse(
                success=True,
                message="Knowledge base uploaded successfull",
                data=chatbot
             )
            

        except Exception as e:
            print(f"error uploading knowledge base: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message) 


    @staticmethod
    async def create_vectors_background_task(chatbot_id: int):
        try:
            background_task = await AdminDbContoller().create_background_task(chatbot_id)
            if not background_task or background_task is None:
                raise ApplicationError.SomethingWentWrong("Cannot create background task")
            return background_task
        except Exception as e:
            print(f"error creating vectors background task: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message) 