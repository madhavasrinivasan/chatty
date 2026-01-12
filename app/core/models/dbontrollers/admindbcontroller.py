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