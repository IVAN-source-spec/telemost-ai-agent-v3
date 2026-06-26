import asyncio
from core.orchestrator.contracts import QueuePublisher, QueuePublishRequest, QueuePublishResult

class MemoryQueuePublisher(QueuePublisher):
    def __init__(self):
        self._queues = {}
        self._counter = 0

    async def publish(self, request: QueuePublishRequest) -> QueuePublishResult:
        if request.queue_name not in self._queues:
            self._queues[request.queue_name] = asyncio.Queue()
        await self._queues[request.queue_name].put(request.payload)
        self._counter += 1
        return QueuePublishResult(accepted=True, message_id=f"msg-{self._counter}")

    async def pop(self, queue_name: str):
        q = self._queues.get(queue_name)
        if q is None:
            return None
        try:
            item = q.get_nowait()
            return item
        except asyncio.QueueEmpty:
            return None