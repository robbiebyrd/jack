#!/bin/bash
# Streams the USB sound card's microphone to the Mac over Roc Toolkit, where a roc-vad receiver
# device presents it as a microphone. Run by jack-mic.service; settings come from /etc/jack/jack.env
# (README "Settings"). The device is the card's ALSA name, which survives USB re-enumeration.
set -u

receiver="${JACK_MIC_RECEIVER:-10.10.0.22}"
device="${JACK_MIC_DEVICE:-hw:S3}"
source_port="${JACK_MIC_SOURCE_PORT:-10011}"
repair_port="${JACK_MIC_REPAIR_PORT:-10012}"
control_port="${JACK_MIC_CONTROL_PORT:-10013}"

exec roc-send \
  -i "alsa://$device" \
  -s "rtp+rs8m://$receiver:$source_port" \
  -r "rs8m://$receiver:$repair_port" \
  -c "rtcp://$receiver:$control_port"
