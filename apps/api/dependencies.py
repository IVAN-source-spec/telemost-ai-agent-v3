from infra.queue.memory_queue import MemoryQueuePublisher
from infra.bot_pool.memory_selector import MemoryBotSelector
from infra.storage.memory_metadata import MemoryMetadataStore

_queue_publisher = MemoryQueuePublisher()

_bot_selector = MemoryBotSelector()
_metadata_store = MemoryMetadataStore()

def get_queue_publisher():
    return _queue_publisher

def get_bot_selector():
    return _bot_selector

def get_metadata_store():
    return _metadata_store

queue_publisher_instance = _queue_publisher
bot_selector_instance = _bot_selector
