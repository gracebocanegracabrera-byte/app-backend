from fastapi import APIRouter
from app.api.v1 import auth, properties, dashboard
from app.api.v1.agents import a3, a1, a2, a4, a5

router = APIRouter()
router.include_router(auth.router)
router.include_router(a3.router)
router.include_router(a1.router)
router.include_router(a2.router)
router.include_router(a4.router)
router.include_router(a5.router)
router.include_router(properties.router)
router.include_router(dashboard.router)
