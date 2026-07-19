# BMO

A voice-activated robot friend on a Raspberry Pi 5, inspired by Adventure Time.
Built from scratch by Corey and Sylas (age 11) as a learning project covering
AI, speech, Linux, and game emulation. Full design doc: [bmo_plan.md](bmo_plan.md).

BMO boots straight into a fullscreen animated face and acts as the whole OS:

- **Say "BMO"** (or touch the screen) to wake it, then just talk — a local LLM
  answers out loud. No cloud, works with zero internet.
- **Launches NES/SNES games by voice**: "BMO, play Super Mario World" starts
  RetroArch fullscreen; quitting the game brings the face back.
- Timers and alarms, jokes and word games, music, weather.
- Conversations stay open ~2 minutes after the last exchange; "BMO, stop"
  puts it back to sleep.

## How it works

| Piece | Tech | File |
|---|---|---|
| Face | pygame-ce, parametric (no images) | `bmo/face.py` |
| Hearing + wake word | Vosk small model, grammar-restricted while sleeping | `bmo/ears.py` |
| Voice + lip sync | Piper TTS → sox pitch shift → RMS envelope drives the mouth | `bmo/voice.py` |
| Brain | Ollama: qwen2.5:3b (default) or llama3.2:1b, streamed sentence-by-sentence | `bmo/brain.py` |
| Commands | regex intent router; plugins win, LLM is the fallback | `bmo/router.py`, `bmo/plugins/` |

Everything is configured in [`config.toml`](config.toml).

## Running on the Pi

BMO autostarts on boot (it's in `~/.config/autostart` for the main user) and
appears in the Start Menu as **BMO**. Manual launch:

```
/opt/bmo/install/bmo-run.sh
```

- Touch anywhere: wake BMO / interrupt it mid-sentence.
- Type + Enter (any keyboard): chat without voice — also works before a mic
  is attached.
- Hold the **top-right corner for 5 seconds** (or press Escape): exit-to-desktop
  dialog for grown-ups.
- Drop game ROMs in `roms/nes/` and `roms/snes/`; songs in `music/`.
- Conversation transcripts land in `logs/` as JSONL.

Fresh Pi setup from a clone:

```
sudo git clone https://github.com/coreyms/bmo /opt/bmo
cd /opt/bmo && ./install/setup.sh
```

The setup script installs apt packages (RetroArch + cores, mpv, sox,
portaudio), Ollama + both models, the Vosk and Piper models, the desktop
entry/autostart, and the RetroArch quit hotkey (Select+Start).

> **Power matters**: a Pi 5 needs a real 5V/5A (27W) USB-C supply. Phone
> chargers negotiate 15W and cause brownout crashes under LLM load.
> Ask us how we know.

## Dev setup on Mac or Ubuntu

BMO runs windowed on a laptop — same code, `--dev` flag. Games/autostart are
Pi-only; everything else (face, chat, TTS, STT, plugins) works anywhere.

**1. System packages**

```
# macOS (Homebrew)              # Ubuntu/Debian
brew install sox mpv            sudo apt install sox mpv libportaudio2 python3-venv
```

(macOS doesn't need portaudio — the `sounddevice` wheel bundles it.)

**2. Ollama + models** — install from [ollama.com](https://ollama.com)
(or `brew install ollama`), then:

```
ollama pull qwen2.5:3b
ollama pull llama3.2:1b
```

**3. Python env** (needs Python 3.11+):

```
git clone https://github.com/coreyms/bmo && cd bmo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**4. Speech models** (~100 MB, into `models/`):

```
curl -L -o /tmp/vosk.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip /tmp/vosk.zip -d models/
mkdir -p models/piper && cd models/piper
../../.venv/bin/python -m piper.download_voices en_US-amy-medium
cd ../..
```

**5. Run it:**

```
.venv/bin/python -m bmo.main --dev
```

Click to wake BMO; type + Enter to chat. With a mic and speakers attached,
voice works exactly like on the Pi. Escape opens the quit dialog.

## Writing a plugin

Add a file in `bmo/plugins/` — it's auto-discovered. Minimal example:

```python
from bmo.router import Plugin, Result

class HighFivePlugin(Plugin):
    name = "highfive"
    priority = 45          # lower runs first; LLM handles anything unmatched

    def __init__(self, app):
        super().__init__(app)
        self.add(r"\bhigh five\b", self.slap)

    def slap(self, match, text):
        return Result(speech="Up top! Smack! That was a good one.")
```

Return `Result(speech=...)` to answer directly, or
`Result(brain_text=..., style_hint=...)` to hand the request to the LLM with
steering. Plugins can reach everything through `self.app` (voice, brain,
timers, config) and may define a `tick()` method that runs every frame.

## Project layout

```
bmo/            the program (face, ears, voice, brain, router, main)
bmo/plugins/    one file per ability — add yours here!
install/        setup script, supervisor, desktop entry
config.toml     models, voices, wake phrases, timeouts
bmo_plan.md     the full design document (worth reading!)
roms/ music/    your games and songs (gitignored)
models/ logs/   downloaded speech models, transcripts (gitignored)
```

## License / credit

Personal, non-commercial fan project. BMO and Adventure Time belong to
Cartoon Network; this is a kid's learning project, not a product.
