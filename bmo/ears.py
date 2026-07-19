"""BMO's ears: mic capture + wake word + speech-to-text.

The STT engine sits behind a small interface so we can swap Vosk for
whisper.cpp later without touching the rest of the app (plan risk #2).

Wake mode runs Vosk with a restricted grammar (wake phrases + [unk]) which
acts as a free local wake-word detector. Conversation mode runs the full
vocabulary. With no input device present, Ears simply reports unavailable —
the app falls back to touch + typed input.
"""

import json
import queue
import threading

try:
    import sounddevice as sd
except Exception:
    sd = None

from vosk import KaldiRecognizer, Model, SetLogLevel

SetLogLevel(-1)  # keep Vosk quiet in our logs

MODE_WAKE = "wake"
MODE_LISTEN = "listen"


class VoskEngine:
    def __init__(self, cfg):
        self.rate = int(cfg.get("audio", "mic_rate", 16000))
        self.model = Model(cfg.path(cfg.get("stt", "vosk_model")))
        self.wake_phrases = [p.lower() for p in cfg.get("wake", "phrases", ["beemo"])]

    def recognizer(self, mode):
        if mode == MODE_WAKE:
            grammar = json.dumps(self.wake_phrases + ["[unk]"])
            return KaldiRecognizer(self.model, self.rate, grammar)
        return KaldiRecognizer(self.model, self.rate)

    def is_wake(self, text):
        t = text.lower().strip()
        return any(p in t for p in self.wake_phrases)


class Ears:
    """Owns the mic stream and a worker thread. Emits events into the shared
    UI queue: ("wake", phrase), ("partial", text), ("utterance", text)."""

    def __init__(self, cfg, events, logger=None):
        self.cfg = cfg
        self.events = events
        self.logger = logger
        self.mode = MODE_WAKE
        self.enabled = False
        self.muted = threading.Event()      # gated while BMO speaks
        self._audio_q = queue.Queue()
        self._mode_lock = threading.Lock()
        self._stream = None
        try:
            self.engine = VoskEngine(cfg)
        except Exception as e:
            self.engine = None
            if logger:
                logger.log("error", f"ears: vosk model failed to load: {e}")

    # ---------------------------------------------------------------- device
    def start(self):
        """Try to open the mic. Safe to call again later (hotplug)."""
        if self.enabled or self.engine is None or sd is None:
            return self.enabled
        try:
            device = self.cfg.get("audio", "input_device", "") or None
            if device:
                device = self._match_device(device)
            self._stream = sd.InputStream(
                samplerate=self.engine.rate, channels=1, dtype="int16",
                blocksize=int(self.engine.rate * 0.25), device=device,
                callback=self._on_audio)
            self._stream.start()
            self.enabled = True
            threading.Thread(target=self._worker, daemon=True, name="ears").start()
            if self.logger:
                self.logger.log("info", "ears: microphone active")
        except Exception as e:
            self.enabled = False
            if self.logger:
                self.logger.log("info", f"ears: no microphone ({e.__class__.__name__})")
        return self.enabled

    def _match_device(self, needle):
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0 and needle.lower() in dev["name"].lower():
                return i
        return None

    def _on_audio(self, indata, frames, t, status):
        if not self.muted.is_set():
            self._audio_q.put(bytes(indata))

    # ------------------------------------------------------------------ mode
    def set_mode(self, mode):
        with self._mode_lock:
            self.mode = mode
            self._mode_dirty = True

    def mute(self, on):
        if on:
            self.muted.set()
            # drop anything BMO's own voice already leaked in
            try:
                while True:
                    self._audio_q.get_nowait()
            except queue.Empty:
                pass
        else:
            self.muted.clear()

    # ---------------------------------------------------------------- worker
    def _worker(self):
        rec = self.engine.recognizer(self.mode)
        current_mode = self.mode
        self._mode_dirty = False
        while self.enabled:
            try:
                chunk = self._audio_q.get(timeout=0.5)
            except queue.Empty:
                continue
            with self._mode_lock:
                if self._mode_dirty or current_mode != self.mode:
                    current_mode = self.mode
                    rec = self.engine.recognizer(current_mode)
                    self._mode_dirty = False
            if rec.AcceptWaveform(chunk):
                text = json.loads(rec.Result()).get("text", "").strip()
                if not text or text == "[unk]":
                    continue
                if current_mode == MODE_WAKE:
                    if self.engine.is_wake(text):
                        self.events.put(("wake", text))
                else:
                    self.events.put(("utterance", text))
            elif current_mode == MODE_LISTEN:
                partial = json.loads(rec.PartialResult()).get("partial", "")
                if partial:
                    self.events.put(("partial", partial))
