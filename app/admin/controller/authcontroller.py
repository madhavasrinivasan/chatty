from llama_index_instrumentation.span_handlers import null
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import RegisterRequest, LoginRequest
from app.core.schema.schemarespone import APIResponse
from app.core.schema.applicationerror import ApplicationError
from app.core.services.jwt import JWTService
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from app.core.services.webcrawler import Services
from fastapi import Request
import bcrypt
import base64 
import time
from app.core.config.db import initialize_light_rag


class AuthController:
    @staticmethod
    async def register_user(body: RegisterRequest, request: Request):
        try: 
            check_user = await AdminDbContoller().find_one_user(body.model_dump())
            if check_user:
                raise ApplicationError.SomethingWentWrong("User already exists")
            if body.password != body.confirm_password:
                raise ApplicationError.SomethingWentWrong("Password and confirm password do not match")
            
            hashed_password = bcrypt.hashpw(body.password.encode('utf-8'), bcrypt.gensalt())
            user_data = body.model_dump(exclude={"confirm_password"})
           
            user_data["password"] = base64.b64encode(hashed_password).decode('utf-8')
            data = await AdminDbContoller().create_user(user_data) 

            new_rag = await Services.get_light_rag_for_store(request=request, store_id=str(data.id))
            if not new_rag:
                raise ApplicationError.SomethingWentWrong("Failed to create light rag")
            
            user_dict = {
                "id": data.id,
                "name": data.name,
                "username": data.username,
                "email": data.email,
                "address": data.address,
                "subscription_id": data.subscription_id,
                "created_at": data.created_at.isoformat() if data.created_at else None,
            }
            return APIResponse(
                success=True,
                message="User created successfully",
                data=user_dict
            )
        except Exception as e:
            print(f"error registering user: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message)
    

    @staticmethod
    async def login_user(body: LoginRequest, ip: str):
        try:
            check_user = await AdminDbContoller().find_one_user(body.model_dump());
            if not check_user:
                raise ApplicationError.SomethingWentWrong("User not found")
            
            stored_password_bytes = base64.b64decode(check_user.password)
            passowrd_chek = bcrypt.checkpw(body.password.encode('utf-8'), stored_password_bytes);
            if not passowrd_chek:
                raise ApplicationError.SomethingWentWrong("Invalid password") 

            check_session = await AdminDbContoller().find_one_user_session(check_user.id) 


            token = JWTService().generate_token({"user_id": check_user.id,"username": check_user.username,"email": check_user.email,"timestamp": time.time()}) 



            if not token or token == "":
                raise ApplicationError.SomethingWentWrong("Failed to generate token")

            if check_session and check_session.status == "active" and check_session != None and check_session.id != 0 and check_session.id != "":
                destroy_session = await AdminDbContoller().destroy_user_session(check_session.token)
                print(f"Destroyed user session: {destroy_session}")
                if not destroy_session or destroy_session == None :
                    raise ApplicationError.SomethingWentWrong("Failed to destroy user session")
                user_session = await AdminDbContoller().create_user_session(check_user.id, token, ip)
                if not user_session or user_session == None or user_session.id == 0 or user_session.id == "":
                    raise ApplicationError.SomethingWentWrong("Failed to create user session") 


            else:
                user_session = await AdminDbContoller().create_user_session(check_user.id, token, ip)
                if not user_session or user_session == None or user_session.id == 0 or user_session.id == "":
                    raise ApplicationError.SomethingWentWrong("Failed to create user session")
            
            respose_data: dict = {
                "token": token,
                "user": check_user.username
            }
            
            return APIResponse(
                success=True,
                message="Login successful",
                data=respose_data
            )
        except Exception as e:
            print(f"error logging in user: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message)


    @staticmethod
    async def logout_user(ip: str):
        try:
            return await AdminDbContoller().destroy_user_session(ip)
        except Exception as e:
            print(f"error logging out user: {e}")
            error_message = getattr(e, 'message', str(e))
            raise ApplicationError.SomethingWentWrong(error_message)




