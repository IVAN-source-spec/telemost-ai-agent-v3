from fastapi import APIRouter

health_router = APIRouter(
    prefix="/api/v1/health",
    tags=["health"],
)

@health_router.get("/health")
async def health():
    return {
        "status": "ok",
    }