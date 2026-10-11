#!/usr/bin/env sh
set -eu

sink_name="${PULSE_SINK:-}"
source_name="${PULSE_SOURCE:-}"

if [ -z "$sink_name" ]; then
  echo "[AudioSink] PULSE_SINK is empty; skipping virtual sink setup"
  exit 0
fi

if [ -z "$source_name" ]; then
  source_name="${sink_name}.monitor"
fi

pulse_ready=0
for _ in $(seq 1 40); do
  if pactl info >/dev/null 2>&1; then
    pulse_ready=1
    break
  fi
  sleep 0.25
done

if [ "$pulse_ready" -ne 1 ]; then
  echo "[AudioSink] PulseAudio is unavailable at ${PULSE_SERVER:-default}" >&2
  exit 1
fi

if ! pactl list short sinks | awk '{print $2}' | grep -Fxq "$sink_name"; then
  pactl load-module module-null-sink \
    sink_name="$sink_name" \
    sink_properties="device.description=$sink_name" >/dev/null
  echo "[AudioSink] Created sink: $sink_name"
else
  echo "[AudioSink] Sink already exists: $sink_name"
fi

for _ in $(seq 1 20); do
  if pactl list short sources | awk '{print $2}' | grep -Fxq "$source_name"; then
    echo "[AudioSink] Source ready: $source_name"
    exit 0
  fi
  sleep 0.1
done

echo "[AudioSink] Source was not created: $source_name" >&2
exit 1
