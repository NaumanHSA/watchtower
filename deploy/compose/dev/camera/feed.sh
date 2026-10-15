#!/bin/sh
# Publishes a looping video to the fake camera. Put any .mp4 (ideally one with faces) at
# deploy/compose/dev/media/sample.mp4; without it, a test pattern is sent instead.
set -e
if [ -f /media/sample.mp4 ]; then
  set -- -re -stream_loop -1 -i /media/sample.mp4
else
  set -- -re -f lavfi -i testsrc2=size=1280x720:rate=15
fi
exec ffmpeg -hide_banner -loglevel warning "$@" -an -c:v libx264 -preset ultrafast -tune zerolatency -g 30 \
  -pix_fmt yuv420p -f rtsp -rtsp_transport tcp rtsp://camera:8554/cam1
