from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import websockets


class VoiceCommandsAudioClient:
    """Non-blocking WebSocket sender for realtime voice-command audio chunks."""

    def __init__(
        self,
        *,
        node_id: str | None = None,
        bot_id: str,
        meeting_id: str | None,
        meeting_title: str | None,
        sample_rate: int,
        channels: int,
        chunk_size: int,
        sample_width: int = 2,
        debug_file: str | Path | None = None,
        service_url: str | None = None,
        queue_max_chunks: int | None = None,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.node_id = node_id
        self.bot_id = bot_id
        self.meeting_id = meeting_id
        self.meeting_title = meeting_title
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.sample_width = sample_width
        self.service_url = service_url or os.getenv(
            "VOICE_COMMANDS_SERVICE_URL",
            "ws://127.0.0.1:8020/ws/audio",
        )
        default_debug = f"voice_commands_debug_{self.bot_id}.jsonl"
        self.debug_file = Path(debug_file or os.getenv("VOICE_COMMANDS_DEBUG_FILE", default_debug))
        max_chunks = queue_max_chunks or int(os.getenv("VOICE_COMMANDS_QUEUE_MAX_CHUNKS", "500"))
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=max(10, max_chunks))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._dropped_chunks = 0
        self._sent_chunks = 0
        self.event_handler = event_handler
        self.delete_debug_after_stop = os.getenv(
            "VOICE_COMMANDS_DELETE_DEBUG_AFTER_STOP",
            "False",
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.reconnect_delay_seconds = float(os.getenv("VOICE_COMMANDS_RECONNECT_DELAY_SECONDS", "3"))
        self.reconnect_max_attempts = int(os.getenv("VOICE_COMMANDS_RECONNECT_MAX_ATTEMPTS", "0"))

    @classmethod
    def enabled(cls) -> bool:
        return os.getenv("VOICE_COMMANDS_ENABLED", "False").strip().lower() in {"1", "true", "yes", "on"}

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.debug_file.parent.mkdir(parents=True, exist_ok=True)
        self._write_debug({"event": "voice_stream_starting", "service_url": self.service_url})
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._write_debug({
            "event": "voice_stream_stopped",
            "sent_chunks": self._sent_chunks,
            "dropped_chunks": self._dropped_chunks,
        })
        if self.delete_debug_after_stop:
            self._delete_debug_file()
        self._started = False

    def on_audio_chunk(self, data: bytes) -> None:
        if not self._started or self._stop_event.is_set():
            return
        try:
            self._queue.put_nowait(bytes(data))
        except queue.Full:
            self._dropped_chunks += 1

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as error:
            self._write_debug({"event": "voice_stream_thread_error", "error": str(error)})

    async def _run(self) -> None:
        reconnect_attempt = 0
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self.service_url, max_size=None) as websocket:
                    reconnect_attempt = 0
                    receiver = asyncio.create_task(self._receive_loop(websocket))
                    await websocket.send(json.dumps({
                        "type": "start",
                        "node_id": self.node_id,
                        "bot_id": self.bot_id,
                        "meeting_id": self.meeting_id,
                        "meeting_title": self.meeting_title,
                        "sample_rate": self.sample_rate,
                        "channels": self.channels,
                        "chunk_size": self.chunk_size,
                        "sample_width": self.sample_width,
                        "format": "pcm_s16le",
                        "started_at": self._utc_now(),
                    }, ensure_ascii=False))

                    while not self._stop_event.is_set():
                        item = await asyncio.to_thread(self._queue.get)
                        if item is None:
                            return
                        await websocket.send(item)
                        self._sent_chunks += 1

                    try:
                        await websocket.send(json.dumps({"type": "stop", "sent_at": self._utc_now()}, ensure_ascii=False))
                        await asyncio.sleep(0.2)
                    finally:
                        receiver.cancel()
                        try:
                            await receiver
                        except asyncio.CancelledError:
                            pass
            except Exception as error:
                if self._stop_event.is_set():
                    break
                reconnect_attempt += 1
                payload = {
                    "type": "voice_service_unavailable",
                    "service_url": self.service_url,
                    "error": str(error),
                    "received_at": self._utc_now(),
                    "will_reconnect": True,
                    "reconnect_attempt": reconnect_attempt,
                }
                self._write_debug({"event": "voice_stream_connection_error", **payload})
                if self.event_handler is not None:
                    try:
                        self.event_handler(payload)
                    except Exception as handler_error:
                        self._write_debug({"event": "voice_service_event_handler_error", "error": str(handler_error)})
                if self.reconnect_max_attempts > 0 and reconnect_attempt >= self.reconnect_max_attempts:
                    break
                await asyncio.sleep(max(0.5, self.reconnect_delay_seconds))

    async def _receive_loop(self, websocket: Any) -> None:
        async for raw in websocket:
            payload: Any
            if isinstance(raw, bytes):
                payload = {"type": "binary_message", "bytes": len(raw)}
            else:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"type": "text_message", "text": raw}
            self._write_debug({"event": "voice_service_message", "payload": payload})
            if self.event_handler is not None:
                try:
                    self.event_handler(payload)
                except Exception as error:
                    self._write_debug({"event": "voice_service_event_handler_error", "error": str(error)})


    def _delete_debug_file(self) -> None:
        try:
            if self.debug_file.exists():
                self.debug_file.unlink()
        except Exception:
            pass

    def _write_debug(self, payload: dict[str, Any]) -> None:
        record = {
            "written_at": self._utc_now(),
            "node_id": self.node_id,
            "bot_id": self.bot_id,
            "meeting_id": self.meeting_id,
            "meeting_title": self.meeting_title,
            **payload,
        }
        try:
            with self.debug_file.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()
