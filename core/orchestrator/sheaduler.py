from dataclasses import dataclass
from .contracts import (
    IdleBotSelector,
    QueuePublisher,
    SessionArtifactMetadataStore,
    SchedulerQueueHandoff,
    SessionArtifactMetadata,
    QueuePublishRequest,
)


@dataclass(frozen=True)
class SchedulerResult:
    session_id: str
    bot_id: str
    queue_message_id: str | None


async def schedule_session(
        *,
        session_id: str,
        meeting_url: str,
        queue_name: str,
        selector: IdleBotSelector,
        queue_publisher: QueuePublisher,
        metadata_store: SessionArtifactMetadataStore,
        artifact_metadata: SessionArtifactMetadata,
) -> SchedulerResult:
    bot = await selector.select_idle_bot()
    if bot is None:
        raise RuntimeError("No idle bot available")

    handoff = SchedulerQueueHandoff(session_id=session_id, bot_id=bot.bot_id, meeting_url=meeting_url)
    result = await queue_publisher.publish(QueuePublishRequest(queue_name=queue_name, payload=handoff))
    if not result.accepted:
        raise RuntimeError("Queue rejected handoff")

    metadata_store.persist(artifact_metadata)
    return SchedulerResult(session_id=session_id, bot_id=bot.bot_id, queue_message_id=result.message_id)