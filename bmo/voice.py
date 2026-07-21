"""BMO's voice: in-process Piper TTS -> playback-rate pitch shift -> envelope
lip sync, with a persistent disk cache so repeated lines play instantly.

Speed design (this pipeline was painfully slow as a per-sentence CLI):
- The Piper model loads ONCE and stays in memory (~2-3s saved per sentence).
- Pitch is done by playing samples at a higher rate (free, no sox pass);
  synthesis is slowed by the same factor so speech stays a natural speed.
- Every synthesized sentence is cached in var/tts-cache/ keyed by text+voice;
  cache hits skip synthesis entirely.
- A pronunciation table (config [tts.pronounce]) fixes words the engine
  reads wrong (Sylas -> "Silas") at the speech layer only — captions still
  show the real spelling.

With no output device present, everything still runs silently (the mouth
animates), so the face demo works before hardware arrives.
"""

import hashlib
import math
import os
import queue
import re
import subprocess
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
CACHE_MAX_MB = 200


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
        semis = float(cfg.get("tts", "pitch_semitones", 4.0))
        self.pitch_factor = 2 ** (semis / 12.0)
        self.length_scale = float(cfg.get("tts", "length_scale", 1.0))
        self.pronounce = dict(cfg.get("tts", "pronounce", {}) or {})
        self.cache_dir = os.path.join(cfg.root, "var", "tts-cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self._piper = None                # loaded lazily, kept forever
        self._piper_lock = threading.Lock()
        self._have_tts = os.path.exists(self.voice_path or "")
        self._have_audio = self._detect_output()
        self.worker = threading.Thread(target=self._run, daemon=True, name="voice")
        self.worker.start()
        if self._have_tts:
            threading.Thread(target=self._preload, daemon=True,
                             name="voice-preload").start()

    def _preload(self):
        """Load the TTS model at startup so the first sentence isn't slow."""
        self._prune_cache()
        try:
            self._ensure_piper()
            if self.logger:
                self.logger.log("info", "voice: piper model loaded")
        except Exception as e:
            if self.logger:
                self.logger.log("error", f"voice preload: {e}")

    def _prune_cache(self):
        """Cap the TTS cache, oldest-used first (cache hits touch mtime)."""
        try:
            files = []
            for name in os.listdir(self.cache_dir):
                p = os.path.join(self.cache_dir, name)
                st = os.stat(p)
                files.append((st.st_mtime, st.st_size, p))
            total = sum(s for _, s, _ in files)
            for _, size, p in sorted(files):
                if total <= CACHE_MAX_MB * 1024 * 1024:
                    break
                os.remove(p)
                total -= size
        except Exception:
            pass

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
    _flush = 0     # bumped by stop(); queue entries carry the value they
                   # were enqueued under, so pre-stop sentences never play

    def say(self, sentence):
        if sentence and sentence.strip():
            self.q.put((self._flush, sentence.strip()))

    def stop(self):
        """Interrupt: flush queue and halt current playback.

        Never touches sounddevice here — only the worker thread may. A
        cross-thread sd.stop() against the worker's sd.play() can deadlock
        inside ALSA, freezing the face mid-word with the mic muted. The
        worker polls `interrupt` every frame and stops itself."""
        self._flush += 1
        self.interrupt.set()
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def busy(self):
        return self.speaking.is_set() or not self.q.empty()

    # ---------------------------------------------------------------- worker
    def _run(self):
        while True:
            flush_no, sentence = self.q.get()
            if flush_no != self._flush:
                # grabbed just as stop() drained the queue — discard, but
                # still settle the speaking state or busy() sticks forever
                if self.q.empty():
                    self.face.speaking = False
                    self.speaking.clear()
                    self.events.put(("speak_end", sentence))
                continue
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
            self._mouth_only(sentence)
            return
        # Pitch shift for free: play faster than synthesized. Synthesis is
        # slowed by the same factor (see _synth_fresh) so pacing stays right.
        playback_rate = int(rate * self.pitch_factor)
        env = compute_envelope(samples, playback_rate)
        if self._have_audio or self.refresh_audio():
            self._play_with_mouth(samples, playback_rate, env)
        else:
            self._mouth_from_env(env)

    # ------------------------------------------------------------------- tts
    def _spoken_text(self, text):
        """Apply the pronunciation table (word-boundary, case-insensitive)."""
        for word, said in self.pronounce.items():
            text = re.sub(rf"\b{re.escape(word)}\b", said, text, flags=re.I)
        return text

    def _cache_path(self, spoken):
        # Key on the effective synthesis scale (length_scale * pitch_factor —
        # what _synth_fresh actually uses), so changing pitch_semitones in
        # config can never replay stale audio at the wrong pace.
        ls = self.length_scale * self.pitch_factor
        key = hashlib.sha1(
            f"{os.path.basename(self.voice_path)}|{ls:g}|{spoken}"
            .encode()).hexdigest()
        return os.path.join(self.cache_dir, key + ".wav")

    def _synthesize(self, text):
        """-> (int16 samples, sample_rate) or (None, 0)."""
        if not self._have_tts:
            return None, 0
        spoken = self._spoken_text(text)
        path = self._cache_path(spoken)
        try:
            if os.path.exists(path):
                os.utime(path)                      # mark used, for LRU pruning
                with wave.open(path, "rb") as w:
                    return (np.frombuffer(w.readframes(w.getnframes()),
                                          dtype=np.int16), w.getframerate())
        except Exception:
            pass                                    # bad cache file: resynth
        try:
            samples, rate = self._synth_fresh(spoken)
            try:
                with wave.open(path, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
                    w.writeframes(samples.tobytes())
            except Exception:
                pass                                # cache write is best-effort
            return samples, rate
        except Exception as e:
            if self.logger:
                self.logger.log("error", f"tts: {e}")
            return None, 0

    def _ensure_piper(self):
        with self._piper_lock:
            if self._piper is None:
                from piper import PiperVoice
                self._piper = PiperVoice.load(self.voice_path)
        return self._piper

    def _synth_fresh(self, spoken):
        """In-process synthesis with the resident model; CLI as a fallback."""
        ls = self.length_scale * self.pitch_factor
        try:
            piper = self._ensure_piper()
            kwargs = {}
            try:
                from piper import SynthesisConfig
                kwargs["syn_config"] = SynthesisConfig(length_scale=ls)
            except ImportError:
                pass
            chunks, rate = [], None
            try:
                gen = piper.synthesize(spoken, **kwargs)
            except TypeError:                       # API without syn_config
                gen = piper.synthesize(spoken)
            for ch in gen:
                rate = getattr(ch, "sample_rate", rate)
                buf = getattr(ch, "audio_int16_bytes", None)
                if buf is None and isinstance(ch, (bytes, bytearray)):
                    buf = bytes(ch)
                if buf:
                    chunks.append(buf)
            if not chunks:
                raise RuntimeError("piper API returned no audio")
            return np.frombuffer(b"".join(chunks), dtype=np.int16), rate or 22050
        except Exception as api_err:
            if self.logger:
                self.logger.log("info", f"tts api fallback: {api_err}")
            return self._synth_cli(spoken, ls)

    def _synth_cli(self, spoken, length_scale):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="bmo-tts-") as td:
            out = os.path.join(td, "out.wav")
            cmd = [os.path.join(self.cfg.root, ".venv", "bin", "piper"),
                   "--model", self.voice_path, "--output_file", out,
                   "--length-scale", str(length_scale)]
            subprocess.run(cmd, input=spoken.encode(), check=True,
                           capture_output=True, timeout=60)
            with wave.open(out, "rb") as w:
                return (np.frombuffer(w.readframes(w.getnframes()),
                                      dtype=np.int16), w.getframerate())

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
        """No speaker: run the mouth track in real time, silently."""
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
