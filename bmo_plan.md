# BMO — Voice AI Companion (Raspberry Pi 5)

## Context

A learning project for Corey (experienced Python dev) and his son Sylas (11). The Pi 5 (8GB, Debian 13 Trixie, X11 desktop, 5" Elecrow 800×480 touchscreen) autologs in as `sylas` and should boot straight into **BMO** — an Adventure Time–styled voice assistant that acts as the whole OS experience: animated face, wake-word voice conversations, and voice-launching of NES/SNES emulators without ever showing the desktop. Non-commercial, built from scratch (no code from existing BMO projects). Sylas should be able to read and modify the code — a teaching vehicle for AI, TTS/STT, and Linux.

## Confirmed decisions

- **Language/UI**: Python 3.13 + **pygame-ce** fullscreen (800×480 native, resolution-independent drawing). Python is correct here: all heavy lifting (LLM, STT, TTS) runs in native libs; Python is glue + face, and it's the language Corey can teach in.
- **Cross-platform dev**: must also run windowed on Mac/Ubuntu (`--dev` flag). All platform specifics (audio device, emulator command, paths, fullscreen) live in config — no Pi-only code paths in core.
- **No Docker** (considered, rejected): BMO needs host display, touchscreen, mic/speaker, Bluetooth, and the ability to launch RetroArch fullscreen — all painful through a container, and impossible for audio/display on macOS (Docker runs in a VM there). Reproducibility comes from a venv + pinned `requirements.txt` + setup script instead; Ollama installs natively on all three platforms.
- **LLM**: **Ollama** with two models, switchable via config + voice command ("BMO, use your big brain"): `qwen2.5:3b` (default, smarter) and `llama3.2:1b` (snappy). Streaming responses.
- **Hybrid connectivity**: fully functional offline; online-only features (weather, later YouTube) degrade gracefully with a friendly BMO excuse.
- **STT + wake word**: **Vosk** (small en-US model, ~40MB) streaming from mic. Idle mode runs Vosk with a restricted grammar (`["beemo", "b m o", "[unk]"]`) as a free local wake-word detector; conversation mode uses full vocabulary. Touch the screen also wakes BMO.
- **Conversation window**: after wake, stays listening across multiple prompts; sleeps after **2 min** of silence or on "BMO stop".
- **TTS**: **Piper** (`piper-tts` 1.5.0 from PyPI; standalone aarch64 binary as fallback if wheels misbehave). Young-sounding en_US voice pitch-shifted up (~+3–5 semitones, sox/librosa) toward BMO's register. Sentence-by-sentence streaming so BMO starts talking ~2s after the LLM starts.
- **Lip sync**: amplitude-envelope → mouth-open height (good enough, per Corey). Phoneme-based visemes is a stretch goal.
- **Echo handling**: mic is gated while BMO speaks (cheap USB speaker+mic won't have AEC). "BMO stop" during speech handled via touch or brief gate-lift between sentences.
- **Character**: best-effort BMO — playful, childlike, encouraging personality via system prompt; age-appropriate for an 11-year-old (curious-kid-safe instructions, no adult content, admits uncertainty).
- **Logging**: JSONL transcripts per session in `logs/` for Corey's review + STT debugging. (Honest caveat: BMO runs as `sylas`, so logs are technically readable by Sylas — acceptable.)
- **Emulators**: **RetroArch from apt** (1.20 available) with libretro cores (NES: fceumm/nestopia; SNES: snes9x if packaged, else bsnes-mercury-performance — pick at install time). ROMs in `roms/nes/` and `roms/snes/`; BMO fuzzy-matches spoken titles against filenames ("BMO, play Super Mario World"). Two 8BitDo SNES controllers pair over Bluetooth. BMO stays alive while the game runs (timers still fire) and the face returns when RetroArch exits.
- **V1 abilities**: launch/quit games, timers & alarms, jokes/riddles/word games (pure LLM), weather (Open-Meteo, no API key) & date/time, play local music/sounds (mpv). **Stretch**: YouTube search+play via yt-dlp + mpv.

## Architecture

```
/opt/bmo/                     # git repo, group-shared (corey develops, sylas runs)
├── bmo/
│   ├── main.py               # entry point, state machine, asyncio event loop
│   ├── face.py               # pygame face: eyes, mouth, expressions, touch
│   ├── ears.py               # mic capture thread, Vosk STT, wake word, VAD endpointing
│   ├── voice.py              # Piper TTS, pitch shift, playback + amplitude → face
│   ├── brain.py              # Ollama client, personality prompt, streaming, history
│   ├── router.py             # intent router: fast-path commands before LLM
│   ├── plugins/              # one module per ability, auto-discovered
│   │   ├── games.py          # RetroArch launcher (fuzzy ROM match)
│   │   ├── timers.py, music.py, weather.py, system.py (volume/sleep/switch-brain)
│   └── config.py             # loads config.toml + per-platform overrides
├── config.toml               # models, audio devices, timeouts, paths, dev-mode
├── assets/                   # sounds, fonts
├── roms/nes/  roms/snes/     # Corey drops ROMs here (not in git)
├── music/
├── logs/                     # JSONL transcripts (not in git)
└── install/                  # setup script, bmo.desktop, autostart wiring
```

- **State machine**: `SLEEPING → LISTENING → THINKING → SPEAKING → LISTENING…` (2-min window) `→ SLEEPING`. Face expression tracks state (idle blinking / attentive / thinking animation / talking mouth).
- **Plugin API**: each plugin registers intents as keyword/regex patterns + handler. Router tries plugins first (fast, deterministic: "play …", "set a timer …", "stop"); everything else streams to the LLM. Later evolution: LLM tool-calling so BMO *decides* to use plugins — great teaching milestone.
- **Single instance**: lockfile; relaunching from the menu focuses/restarts cleanly.

## Launch integration (Pi only)

- `bmo.desktop` in `/usr/share/applications/` → shows in Start Menu as "BMO".
- Copy in `/home/sylas/.config/autostart/` → auto-launches fullscreen on sylas's autologin (X11 session confirmed).
- Setup script: apt packages (retroarch, libretro cores, mpv, sox, portaudio), Ollama install + model pulls, venv at `/opt/bmo/.venv`, Vosk model + Piper voice downloads.

## Implementation phases (each independently demoable)

1. **Face & shell** *(no audio hardware needed)* — repo skeleton, config system, pygame face with blinking/expressions/talking-mouth test animation, touch input, fullscreen on Pi + windowed `--dev`, Start Menu entry + autostart.
2. **Brain** *(no audio hardware needed)* — Ollama + both models, BMO personality prompt, streaming chat via on-screen debug text entry, transcript logging, model-switch command.
3. **Voice** *(after USB mic/speaker arrive)* — mic capture, Vosk wake word + STT endpointing, Piper voice with pitch shift, sentence-streaming playback with amplitude lip sync, mic gating, full conversation loop with 2-min window and "BMO stop".
4. **OS powers** — intent router + plugin system; RetroArch game launching (+ Bluetooth controller pairing), timers/alarms, music, weather, jokes mode.
5. **Polish & stretch** — YouTube plugin, better visemes, custom openWakeWord "BMO" model, whisper.cpp STT upgrade, whatever Sylas dreams up.

## Verification

- Phase 1: BMO appears in Start Menu; reboot Pi → BMO face fullscreen with no visible desktop; runs windowed on another machine with `--dev`.
- Phase 2: typed "why is the sky blue?" gets a streamed BMO-personality answer; measure tokens/s on both models.
- Phase 3: say "BMO" from across the room → wakes; multi-turn voice chat; time from end-of-speech to first spoken word ≤ ~4s (3B) / ~2s (1B); "BMO stop" sleeps it.
- Phase 4: "BMO, play Super Mario World" → RetroArch fullscreen with working controller; quitting the game returns to the face; timer set by voice fires audibly during gameplay.
- End-to-end: full show-and-tell rehearsal — wake, chat, joke, timer, launch game, return, sleep.
