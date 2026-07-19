#!/bin/bash
# BMO supervisor: restart on crash, but stop after a crash loop so a broken
# build falls through to the desktop instead of flapping forever.
# Exit 0 = clean quit (escape hatch). Exit 3 = another instance is running.

cd /opt/bmo || exit 1
crashes=0
window_start=$(date +%s)

while true; do
    .venv/bin/python -m bmo.main "$@"
    code=$?
    if [ "$code" -eq 0 ] || [ "$code" -eq 3 ]; then
        exit 0
    fi
    now=$(date +%s)
    if [ $((now - window_start)) -gt 60 ]; then
        crashes=0
        window_start=$now
    fi
    crashes=$((crashes + 1))
    echo "BMO crashed (exit $code), restart $crashes/3" >&2
    if [ "$crashes" -ge 3 ]; then
        echo "BMO crash loop — giving up so the desktop is reachable." >&2
        exit 1
    fi
    sleep 1
done
