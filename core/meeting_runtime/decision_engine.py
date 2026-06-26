from enum import Enum
from dataclasses import dataclass
from .participant_policy import should_leave
from .reconnect_policy import next_retry

class RuntimeActionName(str, Enum):
    STAY = "stay"
    LEAVE = "leave"
    RECONNECT = "reconnect"
    GIVE_UP = "give_up"

@dataclass(frozen=True)
class RuntimeConfig:
    alone_leave_threshold: int
    max_reconnect_attempts: int
    reconnect_interval_sec: int

@dataclass(frozen=True)
class RuntimeState:
    participant_count: int
    alone_for_seconds: int
    reconnect_attempt: int

@dataclass(frozen=True)
class RuntimeAction:
    name: RuntimeActionName
    delay_seconds: int | None = None

def decide_action(state: RuntimeState, config: RuntimeConfig, connection_dropped: bool) -> RuntimeAction:
    if should_leave(state.alone_for_seconds, config.alone_leave_threshold):
        return RuntimeAction(RuntimeActionName.LEAVE)
    if connection_dropped and state.participant_count > 1:
        delay = next_retry(state.reconnect_attempt, config.max_reconnect_attempts, config.reconnect_interval_sec)
        if delay is None:
            return RuntimeAction(RuntimeActionName.GIVE_UP)
        return RuntimeAction(RuntimeActionName.RECONNECT, delay)
    return RuntimeAction(RuntimeActionName.STAY)