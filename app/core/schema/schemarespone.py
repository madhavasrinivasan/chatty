from typing import Generic, TypeVar, Optional
from pydantic import BaseModel
from pydantic.generics import GenericModel
from pydantic import Field

T = TypeVar("T")

class APIResponse(GenericModel, Generic[T]):
    status: int = Field(default=200)
    message: str
    data: Optional[T] = None
