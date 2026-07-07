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
        title: str | None = None,
        source: str | None = None,
        external_event_id: str | None = None,
        scheduled_start_at: str | None = None,
        scheduled_end_at: str | None = None,
        organizer: str | None = None,
        queue_name: str,
        selector: IdleBotSelector,
        queue_publisher: QueuePublisher,
        metadata_store: SessionArtifactMetadataStore,
        artifact_metadata: SessionArtifactMetadata,
) -> SchedulerResult:
    bot = await selector.select_idle_bot()
    if bot is None:
        raise RuntimeError("No idle bot available")

    if hasattr(selector, "assign_session"):
        await selector.assign_session(bot.bot_id, session_id, meeting_url, title or session_id)

    try:
        handoff = SchedulerQueueHandoff(
            session_id=session_id,
            bot_id=bot.bot_id,
            meeting_url=meeting_url,
            title=title,
            source=source,
            external_event_id=external_event_id,
            scheduled_start_at=scheduled_start_at,
            scheduled_end_at=scheduled_end_at,
            organizer=organizer,
        )
        result = await queue_publisher.publish(QueuePublishRequest(queue_name=queue_name, payload=handoff))
    except Exception:
        if hasattr(selector, "release_bot"):
            await selector.release_bot(bot.bot_id)
        raise

    if not result.accepted:
        if hasattr(selector, "release_bot"):
            await selector.release_bot(bot.bot_id)
        raise RuntimeError("Queue rejected handoff")

    metadata_store.persist(artifact_metadata)
    return SchedulerResult(session_id=session_id, bot_id=bot.bot_id, queue_message_id=result.message_id)
