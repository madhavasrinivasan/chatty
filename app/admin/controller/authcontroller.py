from llama_index_instrumentation.span_handlers import null
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import RegisterRequest, LoginRequest
from app.core.schema.schemarespone import APIResponse
from app.core.schema.applicationerror import ApplicationError
from app.core.services.jwt import JWTService
import bcrypt
import base64 


class AuthController:
    @staticmethod
    async def register_user(body: RegisterRequest):
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
            
            passowrd_chek = bcrypt.checkpw(body.password.encode('utf-8'), check_user.password.encode('utf-8'));
            if not passowrd_chek:
                raise ApplicationError.SomethingWentWrong("Invalid password") 

            token = JWTService().generate_token({"user_id": check_user.id,"username": check_user.username,"email": check_user.email}) 

            if not token or token == "":
                raise ApplicationError.SomethingWentWrong("Failed to generate token")
            
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