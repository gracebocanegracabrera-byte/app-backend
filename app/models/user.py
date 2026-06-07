from sqlalchemy import Column, String, DateTime, Boolean, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(SAEnum("buyer", "advisor", "admin", name="user_role"), default="buyer")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    legal_disclaimer_accepted = Column(Boolean, default=False, nullable=False)
    legal_disclaimer_accepted_at = Column(DateTime(timezone=True), nullable=True)
    privacy_policy_accepted = Column(Boolean, default=False, nullable=False)
    privacy_policy_version = Column(String(10), nullable=True)
    data_processing_consent = Column(Boolean, default=False, nullable=False)
