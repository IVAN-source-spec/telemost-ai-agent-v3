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
    from apps.transcription_monitor import monitor_loop
    import asyncio

    async def start_with_delay():
        await asyncio.sleep(0.5)
        await start_worker()

    async def start_transcription_monitor():
        await asyncio.sleep(1.0)
        await monitor_loop()

    task = asyncio.create_task(start_with_delay())
    transcription_task = asyncio.create_task(start_transcription_monitor())
    yield
    # === SHUTDOWN ===
    task.cancel()
    transcription_task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("[Lifespan] Worker cancelled")
    try:
        await transcription_task
    except asyncio.CancelledError:
        print("[Lifespan] Transcription monitor cancelled")

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
