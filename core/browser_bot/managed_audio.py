"""Audio accumulation authorized by the control service, with local safety guards."""
from functools import wraps
from threading import RLock

from .audio_activity import AudioActivityMonitor


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class ManagedAudioActivityMonitor(AudioActivityMonitor):
    def __init__(self, **kwargs):
        self.lock = RLock()
        self.authorized_period = None
        self.lease_until = 0
        super().__init__(threshold_minutes=0, **kwargs)

    @locked
    def reset(self):
        super().reset()
        self.authorized_period = None
        self.lease_until = 0

    @locked
    def reset_audio(self):
        super().reset_audio()

    @locked
    def update_participants(self, single):
        period = single.get('started_at') if single.get('active') else None
        if not period or period != self.period:
            self.reset()
            self.period = period
        if self.authorized_period and self.clock() >= self.lease_until:
            self.reset()
            self.period = period
        self.eligible = bool(period and period == self.authorized_period)

    @locked
    def command(self, action, period_id, single):
        self.update_participants(single)
        if action == 'reset':
            # A delayed reset cannot erase a newer single-participant period.
            if self.authorized_period == period_id:
                self.reset()
                self.period = single.get('started_at') if single.get('active') else None
            return {'accepted': True, 'action': action}
        if not self.period or self.period != period_id:
            raise ValueError('Participant period changed or observations are stale')
        already_started = self.authorized_period == period_id
        self.authorized_period = period_id
        self.lease_until = self.clock() + 30
        self.eligible = True
        return {'accepted': True, 'action': action, 'already_started': already_started}

    @locked
    def receive(self, payload, meeting_id):
        if self.clock() >= self.lease_until:
            self.reset()
            return
        super().receive(payload, meeting_id)

    @locked
    def snapshot(self, single):
        result = super().snapshot(single)
        if result['status'] == 'waiting_threshold':
            result['status'] = 'waiting_monitor'
        result.update(managed_by='control_service', period_id=self.authorized_period,
                      start_after_minutes=None, remaining_seconds=None)
        return result
