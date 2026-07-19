"""BMO's voice: Piper TTS -> sox pitch shift -> playback with envelope lip sync.

Each sentence is fully synthesized before it plays, so the mouth track
(one openness value per 20ms) is precomputed and playback just indexes it.
With no output device present (pre-hardware), everything still runs — the
mouth animates silently, which is exactly what we demo before the speaker
arrives.
"""

import math
import os
import queue
import subprocess
import tempfile
import threading
import time
import wave

import numpy as np

try:
    import sounddevice as sd
    _SD_ERR = None
except Exception as e:      # missing libportaudio etc.
    sd = None
    _SD_ERR = e

FRAME_MS = 20


def compute_envelope(samples, rate, frame_ms=FRAME_MS):
    """Mouth-openness per frame: RMS -> gate -> sqrt -> attack/release smooth."""
    n = max(1, int(rate * frame_ms / 1000))
    frames = max(1, len(samples) // n)
    x = samples[: frames * n].astype(np.float32).reshape(frames, n)
    rms = np.sqrt((x ** 2).mean(axis=1))
    peak = rms.max() + 1e-9
    gate = peak * 0.07
    env = np.clip((rms - gate) / (peak - gate + 1e-9), 0.0, 1.0) ** 0.5
    out = np.zeros_like(env)
    cur = 0.0
    a_up = 1 - math.exp(-frame_ms / 40.0)    # fast attack
    a_dn = 1 - math.exp(-frame_ms / 110.0)   # slower release
    for i, v in enumerate(env):
        cur += (v - cur) * (a_up if v > cur else a_dn)
        out[i] = cur
    return out


class Voice:
    """Owns the speak queue. `say()` is called by any thread; a worker
    synthesizes and plays sentences in order, driving face.mouth_drive."""

    def __init__(self, cfg, face, events, logger=None):
        self.cfg = cfg
        self.face = face
        self.events = events              # thread-safe queue back to the UI
        self.logger = logger
        self.q = queue.Queue()
        self.interrupt = threading.Event()
        self.speaking = threading.Event()
        self.voice_path = cfg.path(cfg.get("tts", "voice", ""))
        self.pitch_cents = int(float(cfg.get("tts", "pitch_semitones", 4.0)) * 100)
        self.length_scale = float(cfg.get("tts", "length_scale", 1.0))
        self._have_tts = os.path.exists(self.voice_path or "")
        self._have_audio = self._detect_output()
        self.worker = threading.Thread(target=self._run, daemon=True, name="voice")
        self.worker.start()

    def _detect_output(self):
        if sd is None:
            return False
        try:
            return sd.query_devices(kind="output") is not None
        except Exception:
            return False

    def refresh_audio(self):
        """Re-probe (USB speaker may get plugged in while running)."""
        if sd is not None:
            try:
                sd._terminate(); sd._initialize()
            except Exception:
                pass
        self._have_audio = self._detect_output()
        return self._have_audio

    # ------------------------------------------------------------------- api
    def say(self, sentence):
        if sentence and sentence.strip():
            self.q.put(sentence.strip())

    def stop(self):
        """Interrupt: flush queue and halt current playback."""
        self.interrupt.set()
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        if sd is not None:
            try:
                sd.stop()
            except Exception:
                pass

    def busy(self):
        return self.speaking.is_set() or not self.q.empty()

    # ---------------------------------------------------------------- worker
    def _run(self):
        while True:
            sentence = self.q.get()
            self.interrupt.clear()
            self.speaking.set()
            self.face.speaking = True
            self.events.put(("speak_start", sentence))
            try:
                self._speak_one(sentence)
            except Exception as e:
                if self.logger:
                    self.logger.log("error", f"voice: {e}")
            finally:
                self.face.set_mouth_drive(0.0)
                if self.q.empty():
                    self.face.speaking = False
                    self.speaking.clear()
                    self.events.put(("speak_end", sentence))

    def _speak_one(self, sentence):
        samples, rate = self._synthesize(sentence)
        if samples is None:
            # No TTS available: simulate mouth from text so the face demo works.
            self._mouth_only(sentence)
            return
        env = compute_envelope(samples, rate)
        if self._have_audio or self.refresh_audio():
            self._play_with_mouth(samples, rate, env)
        else:
            self._mouth_from_env(env)

    # ------------------------------------------------------------------- tts
    def _synthesize(self, text):
        """Piper CLI -> wav -> optional sox pitch shift -> (int16 samples, rate)."""
        if not self._have_tts:
            return None, 0
        try:
            with tempfile.TemporaryDirectory(prefix="bmo-tts-") as td:
                raw = os.path.join(td, "raw.wav")
                out = os.path.join(td, "out.wav")
                cmd = [os.path.join(self.cfg.root, ".venv", "bin", "piper"),
                       "--model", self.voice_path, "--output_file", raw]
                if self.length_scale != 1.0:
                    cmd += ["--length-scale", str(self.length_scale)]
                subprocess.run(cmd, input=text.encode(), check=True,
                               capture_output=True, timeout=60)
                final = raw
                if self.pitch_cents:
                    r = subprocess.run(["sox", raw, out, "pitch", str(self.pitch_cents)],
                                       capture_output=True, timeout=30)
                    if r.returncode == 0:
                        final = out
                with wave.open(final, "rb") as w:
                    rate = w.getframerate()
                    data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                return data, rate
        except Exception as e:
            if self.logger:
                self.logger.log("error", f"tts: {e}")
            return None, 0

    # -------------------------------------------------------------- playback
    def _play_with_mouth(self, samples, rate, env):
        latency = 0.08
        try:
            sd.play(samples, rate, blocking=False)
            stream = sd.get_stream()
            latency = float(getattr(stream, "latency", 0.08) or 0.08)
        except Exception as e:
            if self.logger:
                self.logger.log("error", f"audio out: {e}")
            self._have_audio = False
            self._mouth_from_env(env)
            return
        t0 = time.monotonic()
        dur = len(samples) / rate
        while time.monotonic() - t0 < dur + 0.15:
            if self.interrupt.is_set():
                sd.stop()
                break
            idx = int((time.monotonic() - t0 - latency) * 1000 / FRAME_MS)
            if 0 <= idx < len(env):
                self.face.set_mouth_drive(float(env[idx]))
            time.sleep(1 / 60)
        self.face.set_mouth_drive(0.0)

    def _mouth_from_env(self, env):
        """No speaker yet: run the mouth track in real time, silently."""
        t0 = time.monotonic()
        total = len(env) * FRAME_MS / 1000
        while time.monotonic() - t0 < total:
            if self.interrupt.is_set():
                break
            idx = int((time.monotonic() - t0) * 1000 / FRAME_MS)
            if idx < len(env):
                self.face.set_mouth_drive(float(env[idx]))
            time.sleep(1 / 60)
        self.face.set_mouth_drive(0.0)

    def _mouth_only(self, text):
        """No TTS at all: wiggle roughly like speech for the demo."""
        dur = max(0.6, min(len(text) * 0.055, 8.0))
        t0 = time.monotonic()
        while time.monotonic() - t0 < dur:
            if self.interrupt.is_set():
                break
            t = time.monotonic() - t0
            level = max(0.0, 0.45 + 0.4 * math.sin(t * 11) + 0.15 * math.sin(t * 23))
            self.face.set_mouth_drive(min(1.0, level))
            time.sleep(1 / 60)
        self.face.set_mouth_drive(0.0)
