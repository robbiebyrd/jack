#!/bin/bash
# Blinks the Pi's onboard ACT and PWR LEDs to show that a deploy just landed,
# then restores each LED's normal trigger, even if the flash is cut short.
# Needs root to write /sys/class/leds.
# Usage: flash-leds.sh [seconds]   (default 10)
set -euo pipefail

LEDS_DIR="${JACK_LEDS_DIR:-/sys/class/leds}"
LEDS=(ACT PWR)
BLINK_MS=100
SAVED_TRIGGERS=()

# A trigger file lists every trigger with the active one in brackets, e.g. "none [mmc0] timer".
active_trigger() {
  grep -o '\[[^]]*\]' "$1" | tr -d '[]'
}

restore_triggers() {
  local i
  for i in "${!SAVED_TRIGGERS[@]}"; do
    echo "${SAVED_TRIGGERS[$i]}" > "$LEDS_DIR/${LEDS[$i]}/trigger" || true
  done
}

main() {
  local seconds="${1:-10}"
  local led

  for led in "${LEDS[@]}"; do
    SAVED_TRIGGERS+=("$(active_trigger "$LEDS_DIR/$led/trigger")")
  done

  trap restore_triggers EXIT
  # Turn termination into a normal exit so the EXIT trap restores the LEDs.
  trap 'exit 143' TERM INT

  for led in "${LEDS[@]}"; do
    echo timer > "$LEDS_DIR/$led/trigger"
    echo "$BLINK_MS" > "$LEDS_DIR/$led/delay_on"
    echo "$BLINK_MS" > "$LEDS_DIR/$led/delay_off"
  done
  sleep "$seconds"
}

main "$@"
