
from pydantic import BaseModel, Field 
from typing import Optional, List ,Dict, Any
from fastapi import Form
import json


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
    chatbot_id: Optional[int] = None
    name: str
    urls: Optional[List[str]] = None

    @classmethod
    def as_form(
        cls,
        chatbot_id: Optional[int] = Form(None),
        name: str = Form(...),
        urls: Optional[str] = Form(None), 
    ):
        parsed_urls = None
        if urls:
            # Try to parse as JSON first (if it's a JSON array string)
            try:
                parsed_urls = json.loads(urls)
                if isinstance(parsed_urls, list):
                    # Already a list, use it
                    pass
                else:
                    # Not a list, treat as comma-separated string
                    parsed_urls = [url.strip() for url in urls.split(",") if url.strip()]
            except (json.JSONDecodeError, TypeError):
                # If JSON parsing fails, treat as comma-separated string
                parsed_urls = [url.strip() for url in urls.split(",") if url.strip()]
        
        return cls(
            chatbot_id=chatbot_id,
            name=name,
            urls=parsed_urls,
        ) 


class llmresponse(BaseModel):
     response: str = Field(description="The response from the LLM in markdown format")
     source : List[str]= Field(description="The source of the response") 

class llmrequest(BaseModel):
    question: str = Field(description="The question to ask the LLM")
    store_id: Optional[str] = None  # e.g. "store_126"; if None, uses store_{user.id}
    mode: Optional[str] = Field(default="hybrid", description="Query mode: naive, local, global, hybrid")