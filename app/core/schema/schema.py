
from pydantic import BaseModel, Field 
from typing import Optional


class LoginRequest(BaseModel):
    username:str
    password:str 


class RegisterRequest(BaseModel):
    username:str
    email:str 
    password:str
    confirm_passsowrd:str 
    address:str
    sunscription_id:Optional[int] = None

