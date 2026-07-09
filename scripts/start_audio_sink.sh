#!/usr/bin/env bash
set -uo pipefail

pulseaudio --start >/dev/null 2>&1 || true

if ! pactl list short sinks | awk '{print $2}' | grep -qx 'virtual_sink'; then
  pactl load-module module-null-sink sink_name=virtual_sink sink_properties=device.description=Virtual_Sink >/dev/null
fi

echo "--- telemost audio sink ---"
pactl list short sinks | grep 'virtual_sink' || true
pactl list short sources | grep 'virtual_sink.monitor' || true

if ! pactl list short sources | awk '{print $2}' | grep -qx 'virtual_sink.monitor'; then
  echo "virtual_sink.monitor was not found" >&2
  exit 1
fi
