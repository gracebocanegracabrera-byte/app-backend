from sqlalchemy import Column, String, Float, Boolean, DateTime, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from app.core.database import Base


class Property(Base):
    __tablename__ = "properties"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    price = Column(Float)
    area_m2 = Column(Float)
    zone = Column(String)
    district = Column(String)
    property_type = Column(String)
    bedrooms = Column(Float)
    image_url = Column(String)
    source_url = Column(String)
    source_name = Column(String)
    raw_data = Column(JSONB, default=dict)
    listing_type = Column(
        SAEnum("sale", "rent", name="listing_type"),
        default="sale",
        nullable=False,
    )
    is_active = Column(Boolean, default=True)
    status = Column(
        SAEnum("available", "reserved", "sold", name="property_status"),
        default="available",
    )
    scraped_at = Column(DateTime(timezone=True), server_default=func.now())
