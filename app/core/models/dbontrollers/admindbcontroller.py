from app.core import models as Models
from app.core.schema.applicationerror import ApplicationError
from tortoise import connections
from typing import List
import uuid

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

    
    async def create_chatbot(self, body: dict):
        try:
            return await self.models.chatbot_settings.create(
                user_id=body["id"],
                api_key=str(uuid.uuid4()),
            )
        except Exception as e:
            print(f"Error creating chatbot: {e}")
            raise ApplicationError.InternalServerError("Cannot create chatbot")



    async def add_assest(self, chatbot_id: int, files: List[dict]):
        try:
            # Convert dictionaries to model instances
            asset_instances = []
            for file_dict in files:
                # Convert string asset_type to enum
                asset_type_str = file_dict["asset_type"]
                asset_type_enum = self.models.asset_type(asset_type_str)
                
                asset_instances.append(
                    self.models.user_assets(
                        asset_type=asset_type_enum,
                        user_id=file_dict["user_id"],
                        chatbot_id=file_dict["chatbot_id"],
                        name=file_dict["name"]
                    )
                )
            return await self.models.user_assets.bulk_create(asset_instances)
        
        except Exception as e:
            print(f"Error adding assest: {e}")
            raise ApplicationError.InternalServerError("Cannot add assest")


    async def bulk_insert_vectors(nodes):
        values = []
        placeholders = []

        for i, node in enumerate(nodes):
            placeholders.append(
                f"(${i*6+1}, ${i*6+2}, ${i*6+3}, ${i*6+4}, ${i*6+5}, ${i*6+6})"
            )

            values.extend([
                str(uuid.uuid4()),
                node.metadata["chatbot_id"],
                node.metadata["user_id"],
                node.text,
                json.dumps(node.metadata),
                node.embedding
            ])

        sql = f"""
        INSERT INTO vector_store
        (id, user_id, chatbot_id, content, metadata, vector)
        VALUES {", ".join(placeholders)}
        """

        conn = connections.get("default")
        await conn.execute_query(sql, values)