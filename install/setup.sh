#!/bin/bash
# BMO setup for Raspberry Pi (Debian trixie). Idempotent — safe to re-run.
# Dev machines (Mac/Ubuntu) only need: python3 -m venv .venv && pip install -r
# requirements.txt, plus Ollama from ollama.com, then run with --dev.
set -e
cd /opt/bmo

echo "== apt packages =="
sudo apt-get install -y retroarch libretro-nestopia libretro-snes9x \
    libretro-genesisplusgx libretro-gambatte \
    libretro-core-info retroarch-assets mpv sox libportaudio2 python3-venv

echo "== python venv =="
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -U pip wheel
.venv/bin/pip install -q -r requirements.txt

echo "== ollama + models =="
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b
ollama pull llama3.2:1b

echo "== speech models =="
if [ ! -d models/vosk-model-small-en-us-0.15 ]; then
    curl -sL -o /tmp/vosk.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip -qo /tmp/vosk.zip -d models/ && rm /tmp/vosk.zip
fi
if [ ! -f models/piper/en_US-amy-medium.onnx ]; then
    mkdir -p models/piper && cd models/piper
    /opt/bmo/.venv/bin/python -m piper.download_voices en_US-amy-medium
    cd /opt/bmo
fi

echo "== desktop integration (systemd user service) =="
sudo cp install/bmo.desktop /usr/share/applications/bmo.desktop
sudo mkdir -p /home/sylas/.config/systemd/user
sudo cp install/bmo.service /home/sylas/.config/systemd/user/bmo.service
sudo chown -R sylas:sylas /home/sylas/.config/systemd
sudo rm -f /home/sylas/.config/autostart/bmo.desktop
sudo systemctl -M sylas@ --user daemon-reload
sudo systemctl -M sylas@ --user enable bmo
sudo chgrp -R bmo /opt/bmo/var /opt/bmo/logs 2>/dev/null || true
sudo chmod -R g+ws /opt/bmo/var /opt/bmo/logs 2>/dev/null || true

echo "== disable duplicate polkit agent (lxpolkit is the session agent) =="
for user in sylas corey; do
    sudo -u $user mkdir -p /home/$user/.config/autostart
    printf '[Desktop Entry]\nType=Application\nName=PolicyKit agent (disabled; lxpolkit runs)\nHidden=true\n' \
        | sudo -u $user tee /home/$user/.config/autostart/polkit-mate-authentication-agent-1.desktop >/dev/null
done

echo "== retroarch: quit hotkeys (Esc + Select+Start, verified 2026-07-21) =="
for user in sylas corey; do
    dir=/home/$user/.config/retroarch
    sudo -u $user mkdir -p $dir/autoconfig/udev
    # 8BitDo Zero 2 pad profile (X-input mode) — Debian ships no autoconfig db
    sudo -u $user cp "$(dirname "$0")/retroarch-autoconfig/"*.cfg $dir/autoconfig/udev/ 2>/dev/null || true
    cfg=$dir/retroarch.cfg
    sudo -u $user touch $cfg
    sudo -u $user sed -i '/^input_enable_hotkey_btn\|^input_exit_emulator_btn\|^input_exit_emulator \|^quit_press_twice\|^video_fullscreen\|^rewind_enable\|^input_save_state_btn\|^input_load_state_btn\|^libretro_info_path/d' $cfg
    {
        # RetroArch's first-run default points this at an empty dir; with the
        # savestate-bypass off it then claims cores can't save states at all.
        # Bypass on = trust the core's own answer, not the info database.
        echo 'libretro_info_path = "/usr/share/libretro/info"'
        echo 'core_info_savestate_bypass = "true"'
        # single-press Esc quit, verified on the Pi keyboard 2026-07-19
        echo 'input_exit_emulator = "escape"'
        echo 'quit_press_twice = "false"'
        echo 'video_fullscreen = "true"'
        # 8BitDo Zero 2 (X-input): Select=6, Start=7, L=4, R=5 — verified
        # 2026-07-21. Select gates the combos: +Start quit, +R save, +L load.
        echo 'input_enable_hotkey_btn = "6"'
        echo 'input_exit_emulator_btn = "7"'
        echo 'input_save_state_btn = "5"'
        echo 'input_load_state_btn = "4"'
        # rewind is off by default (CPU cost) but free on a Pi 5 with 8/16-bit
        # cores; hold R to rewind. F2/F4 save/load state also still work.
        echo 'rewind_enable = "true"'
    } | sudo -u $user tee -a $cfg >/dev/null
done

echo "Done. Reboot (or log in as sylas) and BMO starts automatically."
