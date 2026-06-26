from pydantic import BaseModel
from typing import Optional


class CreateMeetingRequest(BaseModel):
    session_id: str
    meeting_url: str
    artifact_uri: Optional[str] = None
    artifact_kind: Optional[str] = "audio"


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