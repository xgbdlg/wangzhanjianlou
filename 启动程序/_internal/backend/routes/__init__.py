# routes/__init__.py
# 初始化路由包

from fastapi import APIRouter

router = APIRouter()

from .init import router as init_router

router.include_router(init_router, prefix="/api")
