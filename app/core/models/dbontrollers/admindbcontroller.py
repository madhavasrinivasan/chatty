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