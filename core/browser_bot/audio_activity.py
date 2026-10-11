"""Accumulate valid audio only after the single-participant observation threshold."""
import math
import os
import time
from datetime import datetime, timezone


class AudioActivityMonitor:
    max_gap_seconds = 20

    def __init__(self, threshold_minutes=None, clock=time.monotonic):
        raw = threshold_minutes if threshold_minutes is not None else os.getenv(
            'TELEMOST_SINGLE_PARTICIPANT_AUDIO_START_MINUTES', '1')
        try:
            self.threshold_minutes = float(raw)
            if not math.isfinite(self.threshold_minutes) or self.threshold_minutes < 0:
                raise ValueError('Invalid threshold')
        except (TypeError, ValueError):
            self.threshold_minutes = 1.0
        self.clock = clock
        self.reset()

    def reset(self):
        self.period = None
        self.eligible = False
        self.started_at = None
        self.baseline = None
        self.last_received = None
        self.speech_seconds = 0.0
        self.silence_seconds = 0.0
        self.currently_speaking = None

    def update_participants(self, single):
        period = single.get('started_at') if single.get('active') else None
        if not period or period != self.period:
            self.reset()
            self.period = period
        self.eligible = bool(period and single.get('elapsed_seconds', 0) > self.threshold_minutes * 60)

    def reset_audio(self):
        self.started_at = None
        self.baseline = None
        self.last_received = None
        self.speech_seconds = 0.0
        self.silence_seconds = 0.0
        self.currently_speaking = None

    def receive(self, payload, meeting_id):
        if not self.eligible or payload.get('meeting_id') != meeting_id:
            return
        now = self.clock()
        try:
            stamp = datetime.fromisoformat(payload['received_at'].replace('Z', '+00:00'))
            age = (datetime.now(timezone.utc) - stamp).total_seconds()
            values = tuple(float(payload[k]) for k in ('audio_seconds','speech_seconds','silence_seconds'))
            index = int(payload['chunk_index'])
            invalid_chunks = int(payload.get('invalid_chunks', 0))
            valid = (payload.get('valid') is True and -5 <= age <= self.max_gap_seconds
                     and all(math.isfinite(v) and v >= 0 for v in values)
                     and abs(values[0] - values[1] - values[2]) < .1)
        except (KeyError, TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            self.baseline = None
            self.currently_speaking = None
            return
        previous = self.baseline
        if self.last_received is not None and now - self.last_received > self.max_gap_seconds:
            self.reset_audio()
            previous = None
        if previous is not None and payload.get('session_id') != previous['session_id']:
            self.reset_audio()
            previous = None
        if previous is not None and index <= previous['index']:
            return
        if previous is not None:
            delta = tuple(v - old for v, old in zip(values, previous['values']))
            # Do not count backlogs, invalid audio, or backwards counters as silence.
            if (invalid_chunks == previous['invalid_chunks'] and all(v >= 0 for v in delta)
                    and delta[0] <= now - previous['time'] + 2):
                self.speech_seconds += delta[1]
                self.silence_seconds += delta[2]
                if self.started_at is None and delta[0] > 0:
                    self.started_at = previous['received_at']
        self.baseline = {'session_id': payload.get('session_id'), 'index': index,
                         'invalid_chunks': invalid_chunks, 'values': values, 'time': now,
                         'received_at': payload['received_at']}
        self.last_received = now
        self.currently_speaking = payload.get('is_speaking')

    def snapshot(self, single):
        self.update_participants(single)
        now = self.clock()
        total = self.speech_seconds + self.silence_seconds
        if not single.get('active'):
            status = 'not_single_participant'
        elif not self.eligible:
            status = 'waiting_threshold'
        elif self.last_received is None:
            status = 'waiting_audio'
        elif self.baseline is None or now - self.last_received > self.max_gap_seconds:
            status = 'stale'
        else:
            status = 'collecting'
        return {
            'status': status, 'start_after_minutes': self.threshold_minutes,
            'remaining_seconds': max(0, int(self.threshold_minutes * 60 - single.get('elapsed_seconds', 0))),
            'started_at': self.started_at, 'accumulated_seconds': round(total, 2),
            'speech_seconds': round(self.speech_seconds, 2), 'silence_seconds': round(self.silence_seconds, 2),
            'silence_percent': round(self.silence_seconds / total * 100, 1) if total > 0 else None,
            'currently_speaking': self.currently_speaking if status == 'collecting' else None,
            'valid': status == 'collecting',
        }
