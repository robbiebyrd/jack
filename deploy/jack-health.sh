#!/bin/bash
# Debug log for the Pi's hangs: once a second, one journal line with the power and throttle flags,
# core voltage, temperature, CPU clock, load, free memory, Wi-Fi link and Jack's own /status.
# Run by jack-health.service; journald syncs within a second, so the lines before a hang survive.
set -u

proc="${JACK_HEALTH_PROC:-/proc}"
samples="${JACK_HEALTH_SAMPLES:-0}"  # 0: run until stopped
interval_s="${JACK_HEALTH_INTERVAL_S:-1}"
status_url="${JACK_HEALTH_STATUS_URL:-http://127.0.0.1:8080/status}"

# vcgencmd prints `name=value`; keep the value.
reading() {
  "$@" 2>/dev/null | sed 's/^[^=]*=//'
}

# get_throttled's bits, as the Raspberry Pi documentation numbers them; capitals are "right now".
throttle_flags() {
  local bits=$(( $1 )) flags=""
  (( bits & 0x1 )) && flags+=" UNDER-VOLTAGE-NOW"
  (( bits & 0x2 )) && flags+=" FREQ-CAPPED-NOW"
  (( bits & 0x4 )) && flags+=" THROTTLED-NOW"
  (( bits & 0x8 )) && flags+=" TEMP-LIMIT-NOW"
  (( bits & 0x10000 )) && flags+=" under-voltage-since-boot"
  (( bits & 0x20000 )) && flags+=" freq-capped-since-boot"
  (( bits & 0x40000 )) && flags+=" throttled-since-boot"
  (( bits & 0x80000 )) && flags+=" temp-limit-since-boot"
  printf '%s' "$flags"
}

sample() {
  local throttled core temp arm_hz load mem wifi jack
  throttled=$(reading vcgencmd get_throttled)
  throttled=${throttled:-0x0}
  core=$(reading vcgencmd measure_volts core)
  temp=$(reading vcgencmd measure_temp)
  arm_hz=$(reading vcgencmd measure_clock arm)
  read -r load _ < "$proc/loadavg"
  mem=$(awk '/^MemAvailable:/ {print $2}' "$proc/meminfo")
  wifi=$(awk '$1 == "wlan0:" {gsub(/\./, "", $3); gsub(/\./, "", $4); print "wifi_link=" $3 " wifi_level=" $4}' \
    "$proc/net/wireless")
  if jack=$(curl -sf -m 0.5 "$status_url" 2>/dev/null); then
    jack=$(printf '%s' "$jack" | tr -d '\n')
  else
    jack=unreachable
  fi
  printf 'health throttled=%s%s core=%s temp=%s arm_hz=%s load=%s mem_avail_kb=%s %s jack=%s\n' \
    "$throttled" "$(throttle_flags "$throttled")" "$core" "$temp" "$arm_hz" "$load" "$mem" "${wifi:-wifi=none}" "$jack"
}

count=0
while :; do
  sample
  count=$((count + 1))
  if (( samples > 0 && count >= samples )); then
    break
  fi
  sleep "$interval_s"
done
