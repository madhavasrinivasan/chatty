from app.core import models as Models
from app.core.schema.applicationerror import ApplicationError
from tortoise import connections


class AdminDbContoller:
    def __init__(self):
        self.models = Models
        self.connection = connections.get("default") 

    async def create_user(self, body: dict):
        try:
            return await self.models.users.create(
                name=body.get("name"),
                username=body["username"],
                email=body["email"],
                password=body["password"],
                address=body.get("address"),
                subscription_id=body.get("subscription_id"),
            )
        except Exception as e:
            print(f"Error creating user: {e}")
            raise ApplicationError.InternalServerError("Cannot create user")

    async def find_one_user(self, body: dict):
        try:
            return await self.models.users.filter(email=body["email"]).first()
        except Exception as e:
            print(f"Error finding user: {e}")
            raise ApplicationError.InternalServerError("Cannot find user") 

    async def create_user_session(self, user_id: int, token: str, ip: str):
        try:
            return await self.models.user_sessions.create(user_id=user_id, token=token, ip_address=ip, status="active")
        except Exception as e:
            print(f"Error creating user session: {e}")
            raise ApplicationError.InternalServerError("Cannot create user session")
    
    async def find_one_user_session(self, user_id: int):
        try:
            return await self.models.user_sessions.filter(user_id=user_id).first()
        except Exception as e:
            print(f"Error finding user session: {e}")
            raise ApplicationError.InternalServerError("Cannot find user session")
    
    async def destroy_user_session(self, token: str):
        print(f"Destroying user session: {token}")
        try:
            return await self.models.user_sessions.filter(token=token).update(status="inactive")
        except Exception as e:
            print(f"Error destroying user session: {e}")
            raise ApplicationError.InternalServerError("Cannot destroy user session") 
    
    async def find_one_user_session_by_token(self, token: str):
        try:
            return await self.models.user_sessions.filter(token=token, status="active").first();
        except Exception as e:
            print(f"Error finding user session by token: {e}")
            raise ApplicationError.InternalServerError("Cannot find user session by token") 

    async def find_one_user_by_id(self, user_id: int):
        print(f"Finding user by id: {user_id}")
        try:
            user = await self.models.users.filter(id=user_id).first()
            if user:
                # Convert to dict and exclude password
                user_dict = {
                    "id": user.id,
                    "name": user.name,
                    "username": user.username,
                    "email": user.email,
                    "address": user.address,
                    "subscription_id": user.subscription_id,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                }
                return user_dict
            return None
        except Exception as e:
            print(f"Error finding user by id: {e}")
            raise ApplicationError.InternalServerError("Cannot find user by id")