from llama_index_instrumentation.span_handlers import null
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import RegisterRequest, LoginRequest
from app.core.schema.schemarespone import APIResponse
from app.core.schema.applicationerror import ApplicationError
from fastapi import Request
from app.core.services.jwt import JWTService
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
import bcrypt
import base64 
import time 




class AppController:
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