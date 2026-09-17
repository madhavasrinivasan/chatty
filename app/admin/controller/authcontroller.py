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
import asyncio
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
            confirm = body.confirm_password if body.confirm_password is not None else body.password
            if body.password != confirm:
                raise ApplicationError.SomethingWentWrong("Password and confirm password do not match")
            
            hashed_password = bcrypt.hashpw(body.password.encode('utf-8'), bcrypt.gensalt())
            user_data = body.model_dump(exclude={"confirm_password"})
           
            user_data["password"] = base64.b64encode(hashed_password).decode('utf-8')
            data = await AdminDbContoller().create_user(user_data)

            from app.core.services.subscription_service import provision_trial_and_wallet

            trial = await provision_trial_and_wallet(data.id)

            # LightRAG init can be slow / hang on Vertex — don't block registration.
            try:
                new_rag = await asyncio.wait_for(
                    Services.get_light_rag_for_store(request=request, store_id=str(data.id)),
                    timeout=20.0,
                )
                if not new_rag:
                    print(f"warn: LightRAG init returned empty for user {data.id}")
            except Exception as rag_err:
                print(f"warn: LightRAG init skipped on register for user {data.id}: {rag_err}")
            
            user_dict = {
                "id": data.id,
                "name": data.name,
                "username": data.username,
                "email": data.email,
                "address": data.address,
                "subscription_id": trial.get("subscription_id") or data.subscription_id,
                "created_at": data.created_at.isoformat() if data.created_at else None,
                "trial": trial,
            }
            return APIResponse(
                success=True,
                message=f"User created successfully. 14-day free trial until {trial.get('end_date')}.",
                data=user_dict
            )
        except Exception as e:
            print(f"error registering user: {e}")
            if isinstance(e, ApplicationError):
                raise e
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
                await AdminDbContoller().destroy_user_session(check_session.token)
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
    async def logout_user(request: Request):
        try:
            token = (request.headers.get("adminauthtoken") or "").strip()
            if token:
                await AdminDbContoller().destroy_user_session(token)
            return APIResponse(
                success=True,
                message="Logged out successfully",
                data=None,
            )
        except Exception as e:
            print(f"error logging out user: {e}")
            # Still succeed for the client — local token clear is what matters for UX
            return APIResponse(
                success=True,
                message="Logged out successfully",
                data=None,
            )




