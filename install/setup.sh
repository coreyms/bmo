#!/bin/bash
# BMO setup for Raspberry Pi (Debian trixie). Idempotent — safe to re-run.
# Dev machines (Mac/Ubuntu) only need: python3 -m venv .venv && pip install -r
# requirements.txt, plus Ollama from ollama.com, then run with --dev.
set -e
cd /opt/bmo

echo "== apt packages =="
sudo apt-get install -y retroarch libretro-nestopia libretro-snes9x \
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

echo "== desktop integration =="
sudo cp install/bmo.desktop /usr/share/applications/bmo.desktop
sudo mkdir -p /home/sylas/.config/autostart
sudo cp install/bmo.desktop /home/sylas/.config/autostart/bmo.desktop
sudo chown -R sylas:sylas /home/sylas/.config/autostart
chmod +x install/bmo-run.sh

echo "== retroarch: quit hotkey (Select+Start) =="
for user in sylas corey; do
    dir=/home/$user/.config/retroarch
    sudo -u $user mkdir -p $dir
    cfg=$dir/retroarch.cfg
    sudo -u $user touch $cfg
    sudo -u $user sed -i '/^input_enable_hotkey_btn\|^input_exit_emulator_btn/d' $cfg
    # 8BitDo SNES pad: Select=6, Start=7 (verify with a pad connected)
    echo 'input_enable_hotkey_btn = "6"' | sudo -u $user tee -a $cfg >/dev/null
    echo 'input_exit_emulator_btn = "7"' | sudo -u $user tee -a $cfg >/dev/null
done

echo "Done. Reboot (or log in as sylas) and BMO starts automatically."
