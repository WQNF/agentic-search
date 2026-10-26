from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
import redis.asyncio as redis

router = APIRouter()

@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    # 检查数据库连接
    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    
    # 检查 Redis 连接
    redis_ok = True
    try:
        r = redis.Redis(host="redis", port=6379, db=0)
        await r.ping()
        await r.close()
    except Exception:
        redis_ok = False
    
    return {
        "status": "ok" if db_ok and redis_ok else "degraded",
        "database": db_ok,
        "redis": redis_ok
    }