"""Continuous, fresh observations of exactly one other participant."""
import logging
import math
import os
import time
from datetime import datetime, timezone


class SingleParticipantMonitor:
    max_gap_seconds = 20

    def __init__(self, threshold_minutes=None, clock=time.monotonic):
        if threshold_minutes is None:
            threshold_minutes = os.getenv('TELEMOST_SINGLE_PARTICIPANT_ALERT_MINUTES', '30')
        try:
            threshold_minutes = float(threshold_minutes)
            if not math.isfinite(threshold_minutes) or threshold_minutes < 0:
                raise ValueError('invalid threshold')
        except (ValueError, TypeError):
            logging.getLogger(__name__).warning('Invalid single participant alert threshold; using 30 minutes')
            threshold_minutes = 30.0
        self.threshold_minutes = threshold_minutes
        self.clock = clock
        self.reset()

    def reset(self):
        self.started = None
        self.last_observed = None
        self.started_at = None

    def observe(self, count, valid, measured_at=None):
        now = self.clock()
        if not valid or count != 1:
            self.reset()
            return
        if self.last_observed is not None and now - self.last_observed > self.max_gap_seconds:
            self.reset()
        if self.started is None:
            self.started = now
            self.started_at = (measured_at or datetime.now(timezone.utc)).isoformat()
        self.last_observed = now

    def snapshot(self, meeting_active=True):
        now = self.clock()
        active = bool(meeting_active and self.started is not None
                      and self.last_observed is not None
                      and 0 <= now - self.last_observed <= self.max_gap_seconds)
        elapsed = max(0, now - self.started) if active else 0
        return {
            'active': active,
            'started_at': self.started_at if active else None,
            'elapsed_seconds': int(elapsed),
            'threshold_minutes': self.threshold_minutes,
            'enabled': self.threshold_minutes > 0,
            'alert': active and self.threshold_minutes > 0 and elapsed > self.threshold_minutes * 60,
        }
