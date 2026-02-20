from app.core import models as Models
from app.core.schema.applicationerror import ApplicationError
from tortoise import connections


class ChatbotDBController:
    def __init__(self):
        self.models = Models
        self.connection = connections.get("default") 


    async def create_user(self, body: dict):
        try:
            return await self.models.users.create(name=body["name"])
        except Exception as e:
            print(f"error creating user: {e}")
            raise ApplicationError.InternalServerError("cannnot create user") 

    