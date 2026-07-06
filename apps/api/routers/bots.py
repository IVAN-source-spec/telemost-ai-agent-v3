from fastapi import APIRouter, Depends, HTTPException

from core.orchestrator.contracts import (
    QueuePublishRequest,
    SchedulerQueueHandoff,
    SessionArtifactMetadata,
)
from ..dependencies import get_bot_selector, get_metadata_store, get_queue_publisher
from ..schemas import BotMeetingRequest, TaskResponse
from ..session_utils import generate_session_id
from ..task_store import create_task

bots_router = APIRouter(prefix="/api/v1/bots", tags=["bots"])


@bots_router.get("/")
async def list_bots(bot_selector=Depends(get_bot_selector)):
    bots = []
    if hasattr(bot_selector, "list_bots"):
        bots = await bot_selector.list_bots()

    busy = sum(1 for bot in bots if bot.get("status") == "busy")
    idle = sum(1 for bot in bots if bot.get("status") == "idle")

    return {
        "summary": {
            "total": len(bots),
            "busy": busy,
            "idle": idle,
        },
        "bots": bots,
    }


async def _enqueue_bot_meeting(
    *,
    bot,
    req: BotMeetingRequest,
    bot_selector,
    queue_publisher,
    metadata_store,
) -> TaskResponse:
    session_id = generate_session_id()
    title = req.title.strip() if req.title and req.title.strip() else session_id

    if hasattr(bot_selector, "assign_session"):
        await bot_selector.assign_session(bot.bot_id, session_id, req.meeting_url, title)

    handoff = SchedulerQueueHandoff(
        session_id=session_id,
        bot_id=bot.bot_id,
        meeting_url=req.meeting_url,
    )

    try:
        result = await queue_publisher.publish(QueuePublishRequest(queue_name="meetings", payload=handoff))
    except Exception:
        if hasattr(bot_selector, "release_bot"):
            await bot_selector.release_bot(bot.bot_id)
        raise

    if not result.accepted:
        if hasattr(bot_selector, "release_bot"):
            await bot_selector.release_bot(bot.bot_id)
        raise HTTPException(status_code=409, detail="Queue rejected handoff")

    metadata_store.persist(
        SessionArtifactMetadata(
            session_id=session_id,
            artifact_uri=f"s3://telemost/{session_id}",
            artifact_kind="audio",
        )
    )
    create_task(session_id, status="queued")

    return TaskResponse(
        task_id=session_id,
        status="queued",
        bot_id=bot.bot_id,
        queue_message_id=result.message_id,
    )


@bots_router.post("/meetings", response_model=TaskResponse)
async def create_meeting_for_any_idle_bot(
    req: BotMeetingRequest,
    bot_selector=Depends(get_bot_selector),
    queue_publisher=Depends(get_queue_publisher),
    metadata_store=Depends(get_metadata_store),
):
    if not req.meeting_url.strip():
        raise HTTPException(status_code=400, detail="meeting_url is required")

    bot = await bot_selector.select_idle_bot()
    if bot is None:
        raise HTTPException(status_code=409, detail="No idle bot available")

    return await _enqueue_bot_meeting(
        bot=bot,
        req=req,
        bot_selector=bot_selector,
        queue_publisher=queue_publisher,
        metadata_store=metadata_store,
    )


@bots_router.post("/{bot_id}/meetings", response_model=TaskResponse)
async def create_meeting_for_bot(
    bot_id: str,
    req: BotMeetingRequest,
    bot_selector=Depends(get_bot_selector),
    queue_publisher=Depends(get_queue_publisher),
    metadata_store=Depends(get_metadata_store),
):
    if not req.meeting_url.strip():
        raise HTTPException(status_code=400, detail="meeting_url is required")

    if not hasattr(bot_selector, "select_bot"):
        raise HTTPException(status_code=501, detail="Bot selector does not support direct bot assignment")

    bot = await bot_selector.select_bot(bot_id)
    if bot is None:
        raise HTTPException(status_code=409, detail="Bot is not available")

    return await _enqueue_bot_meeting(
        bot=bot,
        req=req,
        bot_selector=bot_selector,
        queue_publisher=queue_publisher,
        metadata_store=metadata_store,
    )
