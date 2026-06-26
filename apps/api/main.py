from fastapi import FastAPI
from contextlib import asynccontextmanager
from apps.api.routers.auth import auth_router
from apps.api.routers.config import config_router
from apps.api.routers.dashboard import dashboard_router
from apps.api.routers.healts import health_router
from apps.api.routers.meetings import meetings_router


@asynccontextmanager
async def lifespan(
        app: FastAPI
):
    from apps.worker.worker import start_worker
    import asyncio

    async def start_with_delay():
        await asyncio.sleep(0.5)
        await start_worker()

    task = asyncio.create_task(start_with_delay())
    yield
    # === SHUTDOWN ===
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("[Lifespan] Worker cancelled")

app = FastAPI(
    lifespan=lifespan,
)

app.include_router(
    router=health_router
)
app.include_router(
    router=auth_router
)
app.include_router(
    router=config_router
)
app.include_router(
    router=dashboard_router
)
app.include_router(
    router=meetings_router
)


@app.get("/")
async def root():
    return {
        "message": "Telemost AI Agent API",
    }