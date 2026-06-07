from pydantic import BaseModel
from typing import Optional


class RankingItem(BaseModel):
    property: dict
    score: float
    tag: str
    breakdown: dict
    has_evaluation: bool
    legal_status: Optional[str]
