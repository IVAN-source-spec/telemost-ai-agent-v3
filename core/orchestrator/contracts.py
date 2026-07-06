from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class BotState:
    bot_id: str

class IdleBotSelector(Protocol):
    async def select_idle_bot(self) -> BotState | None:
        ...

@dataclass(frozen=True)
class QueuePublishRequest:
    queue_name: str
    payload: object

@dataclass(frozen=True)
class QueuePublishResult:
    accepted: bool
    message_id: str | None

class QueuePublisher(Protocol):
    async def publish(self, request: QueuePublishRequest) -> QueuePublishResult:
        ...

@dataclass(frozen=True)
class SchedulerQueueHandoff:
    session_id: str
    bot_id: str
    meeting_url: str
    title: str | None = None

@dataclass(frozen=True)
class SessionArtifactMetadata:
    session_id: str
    artifact_uri: str
    artifact_kind: str

class SessionArtifactMetadataStore(Protocol):
    def persist(self, metadata: SessionArtifactMetadata) -> None:
        ...
