from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Literal
from uuid import UUID


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str
    password: str
    role: Literal["buyer", "advisor", "admin"] = "buyer"
    legal_disclaimer_accepted: bool = False
    data_processing_consent: bool = False
    privacy_policy_accepted: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    name: str
    role: str
