#!/usr/bin/env bash
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export PULSE_SERVER="${PULSE_SERVER:-unix:${XDG_RUNTIME_DIR}/pulse/native}"

for _ in $(seq 1 20); do
  if pactl info >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if ! pactl info >/dev/null 2>&1; then
  echo "PulseAudio is not available at ${PULSE_SERVER}" >&2
  exit 1
fi

ensure_sink() {
  local name="$1"
  local description="$2"
  if ! pactl list short sinks | awk '{print $2}' | grep -qx "$name"; then
    pactl load-module module-null-sink sink_name="$name" sink_properties="device.description=${description}" >/dev/null
  fi
}

ensure_sink virtual_sink Virtual_Sink
ensure_sink virtual_sink_bot_1 Virtual_Sink_Bot_1
ensure_sink virtual_sink_bot_2 Virtual_Sink_Bot_2
ensure_sink virtual_sink_bot_3 Virtual_Sink_Bot_3

pactl set-default-sink virtual_sink >/dev/null 2>&1 || true
pactl set-default-source virtual_sink.monitor >/dev/null 2>&1 || true

echo "--- telemost audio sinks ---"
pactl list short sinks | grep 'virtual_sink' || true
echo "--- telemost audio sources ---"
pactl list short sources | grep 'virtual_sink' || true

for source in virtual_sink.monitor virtual_sink_bot_1.monitor virtual_sink_bot_2.monitor virtual_sink_bot_3.monitor; do
  if ! pactl list short sources | awk '{print $2}' | grep -qx "$source"; then
    echo "$source was not found" >&2
    exit 1
  fi
done