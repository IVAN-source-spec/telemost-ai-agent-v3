from core.orchestrator.contracts import SessionArtifactMetadataStore, SessionArtifactMetadata

class MemoryMetadataStore(SessionArtifactMetadataStore):
    def __init__(self):
        self._items = {}

    def persist(self, metadata: SessionArtifactMetadata) -> None:
        self._items[metadata.session_id] = metadata