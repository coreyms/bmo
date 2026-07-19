# BMO — Voice AI Companion (Raspberry Pi 5)

## Context

A learning project for Corey (experienced Python dev) and his son Sylas (11). The Pi 5 (8GB, Debian 13 Trixie, X11 desktop, 5" Elecrow 800×480 touchscreen) autologs in as `sylas` and should boot straight into **BMO** — an Adventure Time–styled voice assistant that acts as the whole OS experience: animated face, wake-word voice conversations, and voice-launching of NES/SNES emulators without ever showing the desktop. Non-commercial, built from scratch (no code from existing BMO projects). Sylas should be able to read and modify the code — a teaching vehicle for AI, TTS/STT, and Linux.

## Confirmed decisions

- **Language/UI**: Python 3.13 + **pygame-ce** fullscreen (800×480 native, resolution-independent drawing). Python is correct here: all heavy lifting (LLM, STT, TTS) runs in native libs; Python is glue + face, and it's the language Corey can teach in.
- **Cross-platform dev**: must also run windowed on Mac/Ubuntu (`--dev` flag). All platform specifics (audio device, emulator command, paths, fullscreen) live in config — no Pi-only code paths in core.
- **No Docker** (considered, rejected): BMO needs host display, touchscreen, mic/speaker, Bluetooth, and the ability to launch RetroArch fullscreen — all painful through a container, and impossible for audio/display on macOS (Docker runs in a VM there). Reproducibility comes from a venv + pinned `requirements.txt` + setup script instead; Ollama installs natively on all three platforms.
- **LLM**: **Ollama** with two models, switchable via config + voice command ("BMO, use your big brain"): `qwen2.5:3b` (default, smarter) and `llama3.2:1b` (snappy). Streaming responses.
  - **Latency constraints (required, not optional)**: system prompt kept short (~150 tokens) so prefill is cheap; `keep_alive=-1` so the model never unloads; rely on Ollama prefix caching (constant system-prompt prefix); cap conversation history window. Brain-switching loads ~2GB from SD (~20s+) — BMO covers with a "give me a second, I'm switching brains!" line.
  - Inject current date/time into the prompt each turn (small models have no clock).
- **Hybrid connectivity**: fully functional offline; online-only features (weather, later YouTube) degrade gracefully with a friendly BMO excuse.
- **STT + wake word**: **Vosk** (small en-US model, ~40MB) streaming from mic, behind a **swappable STT interface** in `ears.py` (engine choice is config, not code).
  - **Known risk #1 — wake word**: the Vosk restricted-grammar trick only works with in-vocabulary words; "beemo" is likely OOV, so phonetic stand-ins ("be mo", "b m o") must be tested. Wake-word validation with measured accept/reject rates is the FIRST task of Phase 3. Named plan B: openWakeWord custom "BMO" model or Porcupine free tier.
  - **Known risk #2 — kid speech**: Vosk small is trained on adult voices; test accuracy with Sylas's actual voice immediately. Ready-to-swap alternative: whisper.cpp `base.en` behind the same interface.
  - VAD endpointing must be generous — kids pause mid-sentence.
- **Conversation window**: after wake, stays listening across multiple prompts; sleeps after **2 min** of silence or on "BMO stop". Touch the screen also wakes BMO.
- **TTS**: **Piper** (`piper-tts` 1.5.0 from PyPI; standalone aarch64 binary as fallback if wheels misbehave). Young-sounding en_US voice pitch-shifted up (~+3–5 semitones) toward BMO's register. Sentence-by-sentence streaming so BMO starts talking shortly after the LLM starts.
- **Interruption ("barge-in")**: mic is gated while BMO speaks (no AEC assumed). Guaranteed interrupt path: **touch anywhere stops BMO talking**; plus a brief mic-open window between TTS sentences to catch "BMO stop". When buying the USB speaker/mic, prefer one advertising AEC / full-duplex — hardware AEC would enable true voice barge-in later.
- **Character**: best-effort BMO — playful, childlike, encouraging personality via system prompt; age-appropriate for an 11-year-old (curious-kid-safe instructions, no adult content, admits uncertainty).
- **Logging**: JSONL transcripts per session in `logs/` for Corey's review + STT debugging. `logs/` needs group-write (sylas's process writes; corey owns the repo). (Honest caveat: BMO runs as `sylas`, so logs are technically readable by Sylas — acceptable.)
- **Emulators**: **RetroArch from apt** (1.20) with **`libretro-nestopia`** (NES) and **`libretro-snes9x`** (SNES) — both confirmed available in Trixie apt. (Bonus for later: gambatte/mgba/genesisplusgx cores exist for GB/GBA/Genesis plugins.) ROMs in `roms/nes/` and `roms/snes/`; BMO fuzzy-matches spoken titles against filenames. Two 8BitDo SNES controllers pair over Bluetooth.
  - **Gameplay audio policy**: during a game, voice is disabled by default (config option: wake-word-only with raised threshold) — game audio + no AEC would cause garbage triggers. Games are quit via RetroArch's controller hotkey (Select+Start, configured by setup script), not by voice. BMO stays alive in the background (timers still fire) and the face returns when RetroArch exits.
- **Robustness (BMO is "the OS")**: launched via a **supervisor wrapper** that auto-restarts on crash, with a crash-loop limit (e.g., 3 crashes in 60s → fall through to desktop) so a broken build doesn't flap forever. **Parent escape hatch**: 5-second long-press in a screen corner (or "BMO, developer mode") → confirm → quit to desktop.
- **V1 abilities**: launch/quit games, timers & alarms, jokes/riddles/word games (pure LLM), weather (Open-Meteo, no API key) & date/time, play local music/sounds (mpv). **Stretch**: YouTube search+play via yt-dlp + mpv.
- **Intent precedence**: "stop" is overloaded — router resolves by context: BMO speaking → shut up; alarm ringing → dismiss; music playing → stop music; otherwise "BMO stop" → sleep.

## Face & lip-sync design

**Drawing — fully procedural, no image assets.** BMO's face is dark line-art on teal: circles, arcs, and rounded rects via pygame primitives, drawn each frame at 60fps into a virtual 800×480 design space scaled to the window (free `--dev` resizability). The face is a **parametric model** — one `FaceState` dataclass holds ~8 floats:

- `eye_openness` (per eye — enables winks and blinks), `pupil_offset` (look direction)
- `mouth_openness` (0–1), `mouth_curve` (smile −1…+1), `mouth_width`

**Expressions are presets, animation is tweening.** Each named expression (idle, listening, thinking, speaking, happy, surprised, sleeping) is just a target set of those parameters; changing expression eases current → target over ~200ms. Blinks are quick scripted tweens fired on a 2–6s random timer; idle adds subtle pupil drift; thinking looks up-and-away with an animated "…". This is deliberately teachable: Sylas edits one number, the face visibly changes.

**Lip sync — precomputed amplitude envelope, indexed by playback clock.** Each sentence is fully synthesized by Piper before it plays, so:

1. One numpy pass over the sentence WAV: 20ms frames → RMS → noise gate → sqrt scaling (perceptual) → attack/release smoothing (~40ms attack, ~100ms release) → normalize to the utterance peak. Result: an envelope array, one value per 20ms.
2. During playback (sounddevice callback tracks frames played), the face reads `envelope[playback_position + latency_offset]` → drives `mouth_openness`. Compensating with the stream's reported output latency keeps mouth and sound aligned within ~1 frame.
3. Louder also widens the mouth slightly; near-zero returns to the expression's resting curve.

This is robust, cheap, and looks right at 5" screen size. **Stretch upgrade (no re-architecture needed):** derive 3–4 visemes from cheap DSP features on the same frames — zero-crossing rate/spectral centroid distinguishes "ee"-type sounds (wide mouth) from "ah" (open) from "oo" (round, low-frequency dominant) — the envelope array just becomes an array of (openness, shape) pairs.

## Architecture

```
/opt/bmo/                     # git repo, group-shared (corey develops, sylas runs)
├── bmo/
│   ├── main.py               # entry point, state machine, asyncio event loop
│   ├── face.py               # parametric pygame face (see design above)
│   ├── ears.py               # mic capture thread, swappable STT engine, wake word, VAD
│   ├── voice.py              # Piper TTS, pitch shift, playback, envelope → face
│   ├── brain.py              # Ollama client, personality prompt, streaming, history cap
│   ├── router.py             # intent router: fast-path commands before LLM
│   ├── plugins/              # one module per ability, auto-discovered
│   │   ├── games.py          # RetroArch launcher (fuzzy ROM match)
│   │   ├── timers.py, music.py, weather.py, system.py (volume/sleep/switch-brain)
│   └── config.py             # loads config.toml + per-platform overrides
├── config.toml               # models, audio devices, timeouts, paths, dev-mode
├── assets/                   # sounds, fonts
├── roms/nes/  roms/snes/     # Corey drops ROMs here (not in git)
├── music/
├── logs/                     # JSONL transcripts (not in git, group-writable)
└── install/                  # setup script, supervisor wrapper, bmo.desktop, autostart
```

- **State machine**: `SLEEPING → LISTENING → THINKING → SPEAKING → LISTENING…` (2-min window) `→ SLEEPING`, plus `GAMING` (voice off, timers alive). Face expression tracks state.
- **Threading**: pygame render loop owns the main thread at 60fps; audio capture, STT, TTS synthesis, and Ollama streaming run in threads/tasks and communicate via queues + the shared `FaceState`.
- **Plugin API**: each plugin registers intents as keyword/regex patterns + handler. Router tries plugins first (deterministic, fast); everything else streams to the LLM. Later evolution: LLM tool-calling so BMO *decides* to use plugins — great teaching milestone.
- **Single instance**: lockfile; relaunching from the menu focuses/restarts cleanly.

## Launch integration (Pi only)

- `bmo.desktop` in `/usr/share/applications/` → shows in Start Menu as "BMO".
- Autostart entry in `/home/sylas/.config/autostart/` runs the **supervisor wrapper** (auto-restart + crash-loop limit) fullscreen on sylas's autologin (X11 confirmed).
- Setup script: apt packages (retroarch, libretro-nestopia, libretro-snes9x, mpv, sox, portaudio), Ollama install + model pulls, venv at `/opt/bmo/.venv`, Vosk model + Piper voice downloads, RetroArch quit-hotkey config.

## Implementation phases (each independently demoable)

1. **Face & shell** *(no audio hardware needed)* — repo skeleton, config system, parametric face with expressions/blinking/talking-mouth test animation, touch input, fullscreen on Pi + windowed `--dev`, Start Menu entry, autostart + supervisor wrapper, parent escape hatch.
2. **Brain** *(no audio hardware needed)* — Ollama + both models, BMO personality prompt (short!), streaming chat via on-screen debug text entry, transcript logging, model-switch command, latency measurement.
3. **Voice** *(after USB mic/speaker arrive)* — **first: wake-word validation with measured accept/reject rates (incl. Sylas's voice); switch to plan B if weak.** Then: Vosk STT endpointing, Piper voice with pitch shift, sentence-streaming playback with envelope lip sync, mic gating + touch-to-interrupt, full conversation loop with 2-min window and "BMO stop".
4. **OS powers** — intent router + plugin system; RetroArch game launching + Bluetooth controller pairing + quit hotkey; timers/alarms (audible over games), music, weather, jokes mode.
5. **Polish & stretch** — YouTube plugin, DSP visemes, custom openWakeWord "BMO" model, whisper.cpp STT upgrade, GB/GBA/Genesis cores, whatever Sylas dreams up.

## Verification

- Phase 1: BMO appears in Start Menu; reboot Pi → BMO face fullscreen with no visible desktop; face runs 60fps; kill the process → supervisor restarts it; escape hatch reaches desktop; runs windowed elsewhere with `--dev`.
- Phase 2: typed "why is the sky blue?" gets a streamed BMO-personality answer; measure tokens/s and time-to-first-token on both models, warm and cold.
- Phase 3: wake-word test protocol passes (>90% accept for Sylas + Corey at ~2m, rare false accepts overnight); multi-turn voice chat; end-of-speech → first spoken word ≤ ~4s warm (3B) / ~2s (1B); touch interrupts speech; "BMO stop" sleeps it.
- Phase 4: "BMO, play Super Mario World" → RetroArch fullscreen with working controller; Select+Start quits back to the face; timer set by voice fires audibly during gameplay.
- End-to-end: cold boot → conversational in ≤ ~90s; full show-and-tell rehearsal — wake, chat, joke, timer, launch game, return, sleep.

## Post-completion reminder

When BMO is fully up and running: **Corey must change his Linux password** (shared during dev setup on 2026-07-18, deliberately deferred).
