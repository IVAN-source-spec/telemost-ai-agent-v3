import os
import pyaudio
import wave
import threading
import time
from pathlib import Path
 
class AudioRecorder:
    def __init__(self, sample_rate=44100, channels=2, chunk=1024, log_prefix="[AudioRecorder]"):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk = chunk
        self.log_prefix = log_prefix
        self.format = pyaudio.paInt16
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.frames = []
        self._is_recording = False
        self._thread = None
        self._source_name = os.getenv("TELEMOST_AUDIO_SOURCE_NAME", "virtual_sink.monitor").strip()
        self._device_index = self._find_virtual_device()

    def _log(self, message: str) -> None:
        print(f"{self.log_prefix} {message}")
 
    def _find_virtual_device(self):
        """Find the configured PulseAudio monitor source for recording system audio."""
        target = self._source_name or "virtual_sink.monitor"
        for i in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(i)
            name = str(info.get('name', ''))
            if info['maxInputChannels'] > 0 and target in name:
                self._log(f"Found {target} at index {i}: {name}")
                return i

        self._log(f"Configured audio source was not found: {target}")
        for i in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(i)
            name = str(info.get('name', ''))
            if info['maxInputChannels'] > 0:
                self._log(f"Available input device {i}: {name}")

        # Fallback keeps old behavior for manual runs where only the generic pulse device is visible.
        for i in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(i)
            name = str(info.get('name', ''))
            if info['maxInputChannels'] > 0 and 'pulse' in name:
                self._log(f"Using pulse as fallback at index {i}: {name}")
                return i
        return None
 
    def start(self):
        if self._is_recording:
            return
        self.frames = []
        self._is_recording = True
 
        if self._device_index is None:
            self._log("No input device found")
            self._is_recording = False
            return
 
        try:
            self.stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=self._device_index,
                frames_per_buffer=self.chunk,
            )
        except Exception as e:
            self._log(f"Failed to open stream: {e}")
            self._is_recording = False
            return
 
        self._thread = threading.Thread(target=self._record, daemon=True)
        self._thread.start()
        self._log(f"Recording started from device {self._device_index}")
 
    def _record(self):
        while self._is_recording:
            try:
                data = self.stream.read(self.chunk, exception_on_overflow=False)
                self.frames.append(data)
            except Exception as e:
                self._log(f"Recording error: {e}")
                break
 
    def stop(self):
        if not self._is_recording:
            return
        self._is_recording = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        self._log(f"Recording stopped, captured {len(self.frames)} chunks")
 
    def save(self, filepath: str):
        if not self.frames:
            self._log("No audio data to save")
            return
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.audio.get_sample_size(self.format))
            wf.setframerate(self.sample_rate)
            wf.writeframes(b''.join(self.frames))
        self._log(f"Saved audio to {filepath}")
 
    def close(self):
        self.stop()
        if self.audio:
            self.audio.terminate()
