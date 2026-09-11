import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.orchestrator.contracts import (
    QueuePublishRequest,
    SchedulerQueueHandoff,
    SessionArtifactMetadata,
)
from ..dependencies import get_bot_selector, get_metadata_store, get_queue_publisher
from ..node_config import get_global_bot_id, get_node_id, get_node_name
from ..security import require_node_api_token
from ..schemas import AgendaActivationRequest, BotMeetingRequest, TaskResponse
from ..session_utils import generate_session_id
from ..task_store import create_task, get_task_by_external_event_id
from core.storage.meeting_storage import get_meeting_storage
from apps.worker.runtime_registry import get_active_bot

bots_router = APIRouter(prefix="/api/v1/bots", tags=["bots"])


def _find_meeting_dir(session_id: str | None) -> Path | None:
    if not session_id:
        return None
    recordings_dir = get_meeting_storage().recordings_dir
    try:
        candidates = [path for path in recordings_dir.rglob("*") if path.is_dir() and session_id in path.name]
    except Exception:
        return None
    return sorted(candidates)[-1] if candidates else None


def _live_agenda_status(bot_id: str | None) -> dict | None:
    if not bot_id:
        return None
    record = get_active_bot(bot_id)
    if record is None:
        return None
    return record.bot.agenda_control_status()


def _participant_status(bot: dict) -> dict:
    bot_id = bot.get("bot_id")
    record = get_active_bot(bot_id) if bot_id else None
    if record is not None:
        return record.bot.participant_metrics()
    return {
        "status": "idle" if bot.get("status") == "idle" else "unavailable",
        "session_id": bot.get("session_id"),
        "other_participants_count": None,
        "includes_bot": False,
        "valid": False,
        "measured_at": None,
        "last_valid_at": None,
        "last_valid_age_seconds": None,
        "poll_interval_seconds": 5,
        "error": None,
    }


def _load_agenda_status(bot: dict) -> dict | None:
    live_status = _live_agenda_status(bot.get("bot_id"))
    if live_status is not None:
        return live_status
    if not bot.get("agenda_present"):
        return {
            "agenda_active": False,
            "status": "idle" if bot.get("status") == "idle" else "missing",
            "items_count": 0,
        }
    meeting_dir = _find_meeting_dir(bot.get("session_id"))
    if meeting_dir is None:
        return {
            "enabled": True,
            "status": "pending",
            "preview": bot.get("agenda_preview"),
        }
    agenda_path = meeting_dir / "meeting_agenda.json"
    if not agenda_path.exists():
        return {
            "enabled": True,
            "status": "pending",
            "preview": bot.get("agenda_preview"),
            "meeting_dir": str(meeting_dir),
        }
    try:
        data = json.loads(agenda_path.read_text(encoding="utf-8"))
    except Exception as error:
        return {
            "enabled": True,
            "status": "error",
            "error": str(error),
            "meeting_dir": str(meeting_dir),
        }
    current_index = data.get("current_index")
    items = data.get("items") or []
    current_title = None
    if isinstance(current_index, int) and 1 <= current_index <= len(items):
        current_title = items[current_index - 1].get("title")
    return {
        "enabled": True,
        "status": "completed" if current_index is None else "active",
        "current_index": current_index,
        "current_title": current_title,
        "total": data.get("total") or len(items),
        "meeting_dir": str(meeting_dir),
        "updated_at": data.get("updated_at"),
    }


@bots_router.get("/")
async def list_bots(_auth=Depends(require_node_api_token), bot_selector=Depends(get_bot_selector)):
    bots = []
    if hasattr(bot_selector, "list_bots"):
        bots = await bot_selector.list_bots()

    node_id = get_node_id()
    node_name = get_node_name()
    bots = [
        {
            **bot,
            "agenda_status": _load_agenda_status(bot),
            "participant_metrics": _participant_status(bot),
            "node_id": node_id,
            "node_name": node_name,
            "global_bot_id": get_global_bot_id(bot["bot_id"]),
        }
        for bot in bots
    ]

    busy = sum(1 for bot in bots if bot.get("status") == "busy")
    idle = sum(1 for bot in bots if bot.get("status") == "idle")

    return {
        "summary": {
            "total": len(bots),
            "busy": busy,
            "idle": idle,
            "node_id": node_id,
            "node_name": node_name,
        },
        "bots": bots,
    }


def _active_bot_or_404(bot_id: str):
    record = get_active_bot(bot_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Active bot not found: {bot_id}")
    return record.bot


@bots_router.get("/{bot_id}/participants/status")
async def get_bot_participant_status(
    bot_id: str,
    _auth=Depends(require_node_api_token),
    bot_selector=Depends(get_bot_selector),
):
    record = get_active_bot(bot_id)
    if record is not None:
        return {
            "node_id": get_node_id(),
            "node_name": get_node_name(),
            "bot_id": bot_id,
            "global_bot_id": get_global_bot_id(bot_id),
            **record.bot.participant_metrics(),
        }

    configured_bots = await bot_selector.list_bots() if hasattr(bot_selector, "list_bots") else []
    configured = next((item for item in configured_bots if item.get("bot_id") == bot_id), None)
    if configured is None:
        raise HTTPException(status_code=404, detail=f"Bot not found: {bot_id}")
    return {
        "node_id": get_node_id(),
        "node_name": get_node_name(),
        "bot_id": bot_id,
        "global_bot_id": get_global_bot_id(bot_id),
        **_participant_status(configured),
    }


class BotLeaveRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=256)


@bots_router.post("/{bot_id}/leave", status_code=202)
async def leave_bot_meeting(
    bot_id: str,
    req: BotLeaveRequest,
    _auth=Depends(require_node_api_token),
):
    record = get_active_bot(bot_id)
    if record is None:
        raise HTTPException(status_code=409, detail="Бот уже не находится на встрече")
    if record.session_id != req.session_id:
        raise HTTPException(status_code=409, detail="Встреча изменилась. Обновите карточку")
    if record.bot.meeting_ended_at is not None and not record.bot.dashboard_exit_requested.is_set():
        raise HTTPException(status_code=409, detail="Участие бота уже завершено")
    return {**record.bot.request_dashboard_exit(), "session_id": record.session_id,
            "bot_id": bot_id, "node_id": get_node_id()}


@bots_router.get("/{bot_id}/agenda/status")
async def get_bot_agenda_status(
    bot_id: str,
    _auth=Depends(require_node_api_token),
):
    bot = _active_bot_or_404(bot_id)
    return bot.agenda_control_status()


@bots_router.post("/{bot_id}/agenda/activate")
async def activate_bot_agenda(
    bot_id: str,
    req: AgendaActivationRequest,
    _auth=Depends(require_node_api_token),
):
    if not req.raw_agenda.strip():
        raise HTTPException(status_code=400, detail="raw_agenda is required")
    bot = _active_bot_or_404(bot_id)
    result = await bot.activate_agenda_from_external(
        req.raw_agenda,
        source=req.source or "calendar_monitor",
        metadata={
            "calendar_event_id": req.calendar_event_id,
            "meeting_url": req.meeting_url,
            "author": req.author,
        },
    )
    return result


async def _enqueue_bot_meeting(
    *,
    bot,
    req: BotMeetingRequest,
    bot_selector,
    queue_publisher,
    metadata_store,
) -> TaskResponse:
    existing = get_task_by_external_event_id(req.external_event_id)
    if existing is not None:
        existing_metadata = existing.get("metadata") or {}
        same_calendar_occurrence = existing_metadata.get("scheduled_start_at") == req.scheduled_start_at
        if same_calendar_occurrence:
            if hasattr(bot_selector, "release_bot"):
                await bot_selector.release_bot(bot.bot_id)
            return TaskResponse(
                task_id=existing["task_id"],
                status=existing["status"],
                bot_id=existing_metadata.get("bot_id"),
                queue_message_id=existing_metadata.get("queue_message_id"),
            )

    session_id = generate_session_id()
    title = req.title.strip() if req.title and req.title.strip() else session_id

    if hasattr(bot_selector, "assign_session"):
        await bot_selector.assign_session(bot.bot_id, session_id, req.meeting_url, title, req.agenda, req.expected_participants)

    handoff = SchedulerQueueHandoff(
        session_id=session_id,
        bot_id=bot.bot_id,
        meeting_url=req.meeting_url,
        title=title,
        agenda=req.agenda,
        expected_participants=req.expected_participants,
        source=req.source,
        external_event_id=req.external_event_id,
        scheduled_start_at=req.scheduled_start_at,
        scheduled_end_at=req.scheduled_end_at,
        organizer=req.organizer,
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
    create_task(session_id, status="queued", metadata={
        "bot_id": bot.bot_id,
        "global_bot_id": get_global_bot_id(bot.bot_id),
        "node_id": get_node_id(),
        "queue_message_id": result.message_id,
        "meeting_url": req.meeting_url,
        "title": title,
        "agenda": req.agenda,
        "expected_participants": req.expected_participants,
        "source": req.source,
        "external_event_id": req.external_event_id,
        "scheduled_start_at": req.scheduled_start_at,
        "scheduled_end_at": req.scheduled_end_at,
        "organizer": req.organizer,
    })

    return TaskResponse(
        task_id=session_id,
        status="queued",
        bot_id=bot.bot_id,
        queue_message_id=result.message_id,
    )


@bots_router.post("/meetings", response_model=TaskResponse)
async def create_meeting_for_any_idle_bot(
    req: BotMeetingRequest,
    _auth=Depends(require_node_api_token),
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
    _auth=Depends(require_node_api_token),
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


class BotAudioMonitorRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=256)
    period_id: str = Field(min_length=1, max_length=256)
    action: Literal["start", "reset"]


@bots_router.post("/{bot_id}/audio-monitor")
async def control_bot_audio_monitor(
    bot_id: str, req: BotAudioMonitorRequest, _auth=Depends(require_node_api_token),
):
    record = get_active_bot(bot_id)
    if record is None or record.session_id != req.session_id or record.bot.meeting_ended_at is not None:
        raise HTTPException(status_code=409, detail="Meeting changed or ended")
    try:
        with record.bot.audio_activity_monitor.lock:
            single = record.bot.single_participant_monitor.snapshot(
                meeting_active=record.bot.meeting_started_at is not None and record.bot.meeting_ended_at is None)
            result = record.bot.audio_activity_monitor.command(req.action, req.period_id, single)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {**result, "session_id": req.session_id, "period_id": req.period_id}


class DashboardAgendaRequest(BaseModel):
    source: Literal['control_dashboard', 'calendar_monitor'] = 'control_dashboard'
    session_id: str = Field(min_length=1, max_length=256)
    raw_agenda: str = Field(min_length=1, max_length=20000)
    action: Literal["validate", "activate"]


@bots_router.post("/{bot_id}/agenda/dashboard")
async def dashboard_agenda(bot_id: str, req: DashboardAgendaRequest, _auth=Depends(require_node_api_token)):
    from core.browser_bot.agenda_tracker import AgendaTracker
    record = get_active_bot(bot_id)
    if (record is None or record.session_id != req.session_id
            or record.bot.meeting_ended_at is not None or record.bot.meeting_started_at is None):
        raise HTTPException(status_code=409, detail="Meeting ended or changed")
    bot = record.bot
    current = bot.agenda_control_status()
    if current.get('agenda_active'):
        return {'status':'already_active','source':current.get('source'),'items_count':current.get('items_count')}
    items, error = AgendaTracker.validate_agenda(req.raw_agenda)
    if error:
        return {'status':'invalid_agenda','error':error}
    if req.action == 'validate':
        return {'status':'valid_agenda','items_count':len(items)}
    return await bot.activate_agenda_from_external(req.raw_agenda, source=req.source,
        metadata={'expected_session_id':req.session_id})
