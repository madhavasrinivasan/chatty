
from pydantic import BaseModel, Field 
from typing import Optional


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

