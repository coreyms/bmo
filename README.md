# BMO

A voice-activated robot friend for Raspberry Pi 5, inspired by Adventure Time.
Built from scratch by Corey and Sylas as a learning project. Full design:
[bmo_plan.md](bmo_plan.md).

## What BMO does

- Animated face on the 5" touchscreen; boots straight into BMO (no desktop)
- Say "BMO" to wake it, then just talk — a local LLM answers (no cloud)
- Launches NES/SNES games by voice ("BMO, play Super Mario World")
- Timers, alarms, jokes, music, weather

## Running

On the Pi it autostarts. Manually: menu → Games → BMO, or:

    /opt/bmo/install/bmo-run.sh

Dev mode (windowed, works on Mac/Ubuntu too):

    .venv/bin/python -m bmo.main --dev

With no mic/speaker attached you can still chat by typing — just start
typing and press Enter. Touch (or click) wakes BMO; touching while BMO
talks interrupts it. Hold the top-right corner 5 seconds (or press
Escape) for the exit-to-desktop dialog.

## Layout

    bmo/          the program (face, ears, voice, brain, router)
    bmo/plugins/  one file per ability — add yours here!
    config.toml   settings (models, voices, timeouts)
    roms/nes|snes drop game ROMs here
    music/        drop songs here
    logs/         conversation transcripts
    models/       downloaded speech models (not in git)

## Fresh install

    sudo git clone <repo> /opt/bmo && cd /opt/bmo && ./install/setup.sh
