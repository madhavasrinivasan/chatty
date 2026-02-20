from app.core import models as Models
from app.core.schema.applicationerror import ApplicationError
from tortoise import connections
import datetime
from typing import List, Dict
import uuid
import json

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

    async def create_background_task(self, chatbot_id: int, user_id: int, task_data: Dict):
        try:
            return await self.models.background_tasks.create(
                chatbot_id=chatbot_id,
                user_id=user_id,
                task_type="create_vectors",
                task_data=task_data,
                status=self.models.background_task_status.pending
            )
        except Exception as e:
            print(f"Error creating background task: {e}")
            raise ApplicationError.InternalServerError("Cannot create background task")

    async def get_pending_background_tasks(self):
        try:
            return await self.models.background_tasks.filter(
                status=self.models.background_task_status.pending
            ).limit(1).all()
        except Exception as e:
            print(f"Error getting pending tasks: {e}")
            raise ApplicationError.InternalServerError("Cannot get pending tasks")

    async def update_background_task_status(self, task_id: int, status: str, error_message: str = None):
        try:
            update_data = {"status": status}
            if error_message:
                update_data["error_message"] = error_message
            return await self.models.background_tasks.filter(id=task_id).update(**update_data)
        except Exception as e:
            print(f"Error updating task status: {e}")
            raise ApplicationError.InternalServerError("Cannot update task status")

    async def create_vector_store(self, nodes):
        values = []
        placeholders = []

        for i, node in enumerate(nodes):
            placeholders.append(
                f"(${i*5+1}, ${i*5+2}, ${i*5+3}, ${i*5+4}, ${i*5+5})"
            )

            # Get embedding from metadata (stored there since Document objects don't allow arbitrary attributes)
            embedding = node.metadata.pop("embedding", [])
            
            values.extend([
                node.metadata["user_id"],
                node.metadata["chatbot_id"],
                node.page_content,  # Langchain Document uses .page_content, not .text
                json.dumps(node.metadata),
                json.dumps(embedding)
            ])

        sql = f"""
        INSERT INTO vector_store
        (user_id, chatbot_id, content, metadata, vector)
        VALUES {", ".join(placeholders)}
        """

        conn = connections.get("default")
        await conn.execute_query(sql, values)

    async def bulk_insert_vectors(nodes):
        values = []
        placeholders = []

        for i, node in enumerate(nodes):
            placeholders.append(
                f"(${i*6+1}, ${i*6+2}, ${i*6+3}, ${i*6+4}, ${i*6+5}, ${i*6+6})"
            )

            # Get embedding from metadata (stored there since Document objects don't allow arbitrary attributes)
            embedding = node.metadata.pop("embedding", [])
            
            values.extend([
                str(uuid.uuid4()),
                node.metadata["chatbot_id"],
                node.metadata["user_id"],
                node.page_content,  # Langchain Document uses .page_content, not .text
                json.dumps(node.metadata),
                embedding
            ])

        sql = f"""
        INSERT INTO vector_store
        (id, user_id, chatbot_id, content, metadata, vector)
        VALUES {", ".join(placeholders)}
        """

        conn = connections.get("default")
        await conn.execute_query(sql, values)


     
    async def get_response(self, user_id: int, embedding: list[float]):
        try:
            # Convert to JSON string - Tortoise handles this
            vector_json = json.dumps(embedding)
            
            sql = """
                SELECT id, content, metadata,
                       vector <=> $1::vector AS distance
                FROM vector_store
                WHERE user_id = $2 
                  AND vector <=> $1::vector < 0.5
                ORDER BY vector <=> $1::vector ASC
                LIMIT 20;
            """
            
            conn = connections.get("default")
            rows = await conn.execute_query_dict(sql, [vector_json, user_id])
            return rows
        except Exception as e:
            print(f"Error getting response: {e}")
            raise ApplicationError.InternalServerError("Cannot get response")


    async def find_one_ecom_store(self, store_id: str):
        try:
            return await self.models.ecom_store.filter(store_id=store_id).first()
        except Exception as e:
            print(f"Error finding ecom store: {e}")
            raise ApplicationError.InternalServerError("Cannot find ecom store")


    async def create_ecom_store(self, user_id: int, store_id: str, store_name: str, access_token: str, refresh_token: str, expires_at: datetime, store_type: str):
        try:
            return await self.models.ecom_store.create(user_id=user_id, store_id=store_id, store_name=store_name, access_token=access_token, refresh_token=refresh_token, expires_at=expires_at, store_type=store_type)
        except Exception as e:
            print(f"Error creating ecom store: {e}")
            raise ApplicationError.InternalServerError("Cannot create ecom store")