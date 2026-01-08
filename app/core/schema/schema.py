
from pydantic import BaseModel, Field 
from typing import Optional


class LoginRequest(BaseModel):
    username:str
    password:str 


class RegisterRequest(BaseModel):
    name: Optional[str] = None
    username: str
    email: str 
    password: str
    confirm_password: str 
    address: Optional[str] = None
    subscription_id: Optional[int] = None

