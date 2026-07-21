# BMO

A voice-activated robot friend on a Raspberry Pi 5, inspired by Adventure Time.
Built from scratch by Corey and Sylas (age 11) as a learning project covering
AI, speech, Linux, and game emulation. Full design doc: [bmo_plan.md](bmo_plan.md).

BMO boots straight into a fullscreen animated face and acts as the whole OS:

- **Say "BMO"** (or touch the screen) to wake it, then just talk — a local LLM
  answers out loud. No cloud, works with zero internet.
- **Launches NES, SNES, Sega, and Game Boy games by voice**: "BMO, play Super
  Mario World" starts RetroArch fullscreen; quitting the game (Esc or the pad
  hotkey — the mic is off while a game runs) brings the face back.
- Timers, alarms, and a stopwatch; jokes, riddles, stories, and word games;
  music, weather, volume control.
- **Long-term memory**: "BMO, remember that my favorite color is teal" — facts
  survive reboots and the LLM knows them in every conversation.
- **Kid-safety guardrails**: bad words, grown-up topics, and anything worrying
  get a gentle scripted redirect (never the LLM), on both what BMO hears
  *and* what it says. Matches are logged for parent review.
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
| Memory | diary facts folded into the system prompt | `bmo/plugins/diary.py`, `var/diary.json` |
| Safety | 3 tiers (crisis / profanity / grown-up), input + output | `bmo/plugins/safety.py` |

Everything is configured in [`config.toml`](config.toml).

## Things to say

After "BMO" (or a touch), try:

- **Games** — "play Super Mario World", "play Aladdin on Genesis", "what games
  do we have", "what Mega Man games do we have", "do we have Sonic"
- **Time** — "set a timer for five minutes", "set an alarm for 7 30 a m
  every school day" (also "every day"; up to 3 alarms, they survive
  restarts and ring for 3 minutes), "cancel my alarms", "start the
  stopwatch", "how long has it been", "what time is it"
- **Fun** — "tell me a joke", "tell me a riddle", "tell me a story",
  "let's play twenty questions", "play a word game"
- **Music & weather** — "play some music", "play the song …", "next song",
  "what's the weather"
- **Memory** — "remember that …", "what do you remember", "forget …"
- **Robot stuff** — "louder" / "quieter" / "set the volume to 50",
  "use your little brain" (fast model) / "big brain" (smart model),
  "what can you do", "stop", "good night"

Anything else goes to the LLM — just talk.

## Running on the Pi

BMO runs as a systemd user service: it autostarts on boot, restarts itself
after a crash, and appears in the Start Menu as **BMO**. Manual control:

```
systemctl --user start bmo      # or stop / restart / status
```

- Touch anywhere: wake BMO / interrupt it mid-sentence.
- Tap the **mic button** (bottom-right circle): mute/unmute the microphone.
  Red with a slash = BMO can't hear anything until you tap it again — touch
  and typing still work.
- Type + Enter (any keyboard): chat without voice — also works before a mic
  is attached.
- Hold the **top-right corner for 2.5 seconds** (or press Escape), or say
  **"exit to desktop"**: exit-to-desktop dialog for grown-ups — the screen
  washes red left-to-right while you hold, no keyboard needed. Exiting always
  takes a touch on the dialog, so a misheard phrase can't close BMO by itself.
- Drop game ROMs in `roms/nes/`, `roms/snes/`, `roms/genesis/`, `roms/sms/`,
  `roms/gamegear/`, `roms/gb/`, or `roms/gbc/`; songs in `music/`.
- Running timers, set alarms, and the stopwatch show as small pills in the
  top-left (live countdown). Tap one to adjust (+1 min), cancel, or close;
  when something rings, its pill flashes red — tap to hush it.
- The controller works on BMO's face too: **Start** wakes BMO or interrupts
  it mid-sentence. An ambiguous "play zelda" opens an on-screen picker —
  **D-pad** to choose, **Start**/**A** to launch, **B**/**Select** to cancel
  (tapping a row or saying the full name also works).
- In-game controller combos (hold **Select** plus): **Start** quit back to
  BMO, **R** save state, **L** load state.
- In-game keys: **F2** save state, **F4** load state, hold **R** to rewind
  time, **Esc** quit back to BMO.
- Conversation transcripts land in `logs/` as JSONL; safety triggers are
  logged there with `"kind": "safety"`. BMO's diary is plain JSON in
  `var/diary.json` — easy to review or edit.

Fresh Pi setup from a clone:

```
sudo git clone https://github.com/coreyms/bmo /opt/bmo
cd /opt/bmo && ./install/setup.sh
```

The setup script installs apt packages (RetroArch + NES/SNES/Genesis/Game Boy
cores, mpv, sox, portaudio), Ollama + both models, the Vosk and Piper models,
the systemd user service, and the RetroArch quit hotkeys (Esc or Select+Start).

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
