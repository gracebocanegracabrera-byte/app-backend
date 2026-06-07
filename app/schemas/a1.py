from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from uuid import UUID


class ScrapeResponse(BaseModel):
    imported: int
    total: int
    message: str


class A1StatusResponse(BaseModel):
    active_properties: int
    last_updated: Optional[datetime]
    sources: list[str]


class PropertyOut(BaseModel):
    id: UUID
    title: str
    price: Optional[float]
    area_m2: Optional[float]
    zone: Optional[str]
    district: Optional[str]
    property_type: Optional[str]
    bedrooms: Optional[float]
    image_url: Optional[str]
    source_name: Optional[str]
    source_url: Optional[str]
    listing_type: str = "sale"
    is_active: bool

    model_config = ConfigDict(from_attributes=True)
