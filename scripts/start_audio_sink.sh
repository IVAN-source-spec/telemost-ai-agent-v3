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

if ! pactl list short sinks | awk '{print $2}' | grep -qx 'virtual_sink'; then
  pactl load-module module-null-sink sink_name=virtual_sink sink_properties=device.description=Virtual_Sink >/dev/null
fi

pactl set-default-sink virtual_sink >/dev/null 2>&1 || true
pactl set-default-source virtual_sink.monitor >/dev/null 2>&1 || true

echo "--- telemost audio sink ---"
pactl list short sinks | grep 'virtual_sink' || true
pactl list short sources | grep 'virtual_sink.monitor' || true

if ! pactl list short sources | awk '{print $2}' | grep -qx 'virtual_sink.monitor'; then
  echo "virtual_sink.monitor was not found" >&2
  exit 1
fi
