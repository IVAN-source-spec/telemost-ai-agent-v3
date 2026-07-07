from pydantic import BaseModel
from typing import Optional


class CreateMeetingRequest(BaseModel):
    session_id: str
    meeting_url: str
    title: Optional[str] = None
    source: Optional[str] = None
    external_event_id: Optional[str] = None
    scheduled_start_at: Optional[str] = None
    scheduled_end_at: Optional[str] = None
    organizer: Optional[str] = None
    artifact_uri: Optional[str] = None
    artifact_kind: Optional[str] = "audio"


class BotMeetingRequest(BaseModel):
    meeting_url: str
    title: Optional[str] = None
    source: Optional[str] = None
    external_event_id: Optional[str] = None
    scheduled_start_at: Optional[str] = None
    scheduled_end_at: Optional[str] = None
    organizer: Optional[str] = None


class TaskResponse(BaseModel):
    task_id: str
    status: str
    bot_id: Optional[str] = None
    queue_message_id: Optional[str] = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[dict] = None
    created_at: str
    metadata: Optional[dict] = None


class ReconnectConfigRequest(BaseModel):
    max_attempts: int
    interval_sec: int


class ReconnectConfigResponse(BaseModel):
    max_attempts: int
    interval_sec: int


class QrResponse(BaseModel):
    url: str
    qr_code: str


class DashboardResponse(BaseModel):
    summary: dict
    reconnect_policy: dict
