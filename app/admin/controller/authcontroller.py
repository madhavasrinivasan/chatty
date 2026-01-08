from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import RegisterRequest
from app.core.schema.schemarespone import APIResponse
from app.core.schema.applicationerror import ApplicationError
import bcrypt
import base64 


class AuthCntroller:
    @staticmethod
    async def register_user(body: RegisterRequest):
        try: 
            print(body.password)
            print(body.confirm_password)
            check_user = await AdminDbContoller().find_one_user(body.model_dump())
            if check_user:
                raise ApplicationError.SomethingWentWrong("User already exists")
            if body.password != body.confirm_password:
                raise ApplicationError.SomethingWentWrong("Password and confirm password do not match")
            
            hashed_password = bcrypt.hashpw(body.password.encode('utf-8'), bcrypt.gensalt())
            user_data = body.model_dump(exclude={"confirm_password"})
            # Store bcrypt hash as base64 encoded string
            user_data["password"] = base64.b64encode(hashed_password).decode('utf-8')
            data = await AdminDbContoller().create_user(user_data)
            # Convert Tortoise ORM model to dict (exclude password for security)
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