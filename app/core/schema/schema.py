
from pydantic import BaseModel, Field 
from typing import Optional, List
from fastapi import Form


class RegisterRequest(BaseModel):
    name: Optional[str] = None
    username: str
    email: str 
    password: str
    confirm_password: str 
    address: Optional[str] = None
    subscription_id: Optional[int] = None 


class LoginRequest(BaseModel):
    email: str
    password: str 


class UploadKnowledgeBaseRequest(BaseModel):
    chatbot_id: int
    name: str
    urls: Optional[List[str]] = None

    @classmethod
    def as_form(
        cls,
        chatbot_id: int = Form(...),
        name: str = Form(...),
        urls: Optional[str] = Form(None), 
    ):
        return cls(
            chatbot_id=chatbot_id,
            name=name,
            urls=urls.split(",") if urls else None,
        )