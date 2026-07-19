"""BMO's brain: streaming chat against a local Ollama server.

Latency rules (these are constraints, not preferences — see bmo_plan.md):
- system prompt stays short and stable within a day (prefix caching)
- keep_alive=-1 so the model never unloads
- history is capped so prefill stays cheap
"""

import datetime
import json
import re
import threading

import requests

# Spoken aloud: no emoji/lists/markdown, short sentences, kid-safe.
SYSTEM_PROMPT = """You are BMO, a cheerful little robot from Adventure Time who lives \
inside a real handheld computer. You talk with Sylas, an awesome 11-year-old, and \
sometimes his dad (call him "your dad" when talking to Sylas). Today is {date}. Your words are spoken out loud, so use \
short playful sentences and never use emoji, lists, code, or symbols. Be silly, \
kind, curious, and encouraging. You love video games and jokes. If you are not \
sure about a fact, say so honestly. Keep everything safe and friendly for kids: \
no scary, violent, or grown-up topics — steer to something fun instead. Answer in \
at most four sentences unless asked for more."""

SENTENCE_END = re.compile(r"([.!?…]+)(\s+|$)")
# Things Piper shouldn't try to pronounce.
STRIP = re.compile(r"[*_#`~\[\]<>]|[\U0001F000-\U0001FAFF☀-➿]")


class Brain:
    def __init__(self, cfg, logger=None):
        self.cfg = cfg
        self.host = cfg.get("brain", "host", "http://127.0.0.1:11434")
        self.models = {"big": cfg.get("brain", "model_big", "qwen2.5:3b"),
                       "small": cfg.get("brain", "model_small", "llama3.2:1b")}
        self.which = cfg.get("brain", "default", "big")
        self.history = []          # [{role, content}] capped
        self.max_history = int(cfg.get("brain", "max_history_messages", 12))
        self.logger = logger
        self.interrupt = threading.Event()
        self.lock = threading.Lock()

    @property
    def model(self):
        return self.models[self.which]

    def set_model(self, which):
        if which in self.models and which != self.which:
            self.which = which
            return True
        return False

    def warm_up(self):
        """Load the model into RAM in the background so turn one is fast."""
        def _go():
            try:
                requests.post(f"{self.host}/api/generate",
                              json={"model": self.model, "prompt": "", "keep_alive": -1},
                              timeout=180)
            except Exception:
                pass
        threading.Thread(target=_go, daemon=True, name="brain-warmup").start()

    def _system(self):
        today = datetime.date.today().strftime("%A, %B %d, %Y")
        return SYSTEM_PROMPT.format(date=today)

    def reset(self):
        with self.lock:
            self.history.clear()

    # ------------------------------------------------------------------ chat
    def stream_sentences(self, user_text, style_hint=None):
        """Yield BMO's reply sentence-by-sentence as tokens stream in.

        style_hint lets plugins steer ("respond with one great kid riddle")
        without polluting the visible conversation history.
        """
        self.interrupt.clear()
        sent = user_text if not style_hint else f"{user_text}\n({style_hint})"
        with self.lock:
            messages = ([{"role": "system", "content": self._system()}]
                        + self.history
                        + [{"role": "user", "content": sent}])

        buf, spoken = "", []
        try:
            r = requests.post(
                f"{self.host}/api/chat",
                json={"model": self.model,
                      "messages": messages,
                      "stream": True,
                      "keep_alive": -1,
                      "options": {
                          "temperature": float(self.cfg.get("brain", "temperature", 0.8)),
                          "num_predict": int(self.cfg.get("brain", "num_predict", 220)),
                      }},
                stream=True, timeout=(10, 600))
            r.raise_for_status()
            for line in r.iter_lines():
                if self.interrupt.is_set():
                    r.close()
                    break
                if not line:
                    continue
                data = json.loads(line)
                buf += data.get("message", {}).get("content", "")
                # Emit complete sentences as they form.
                while True:
                    m = SENTENCE_END.search(buf)
                    if not m or m.end() < 12:   # avoid emitting "Hi." alone... unless short reply
                        if not m or (m.end() < 12 and not data.get("done")):
                            break
                    sentence = clean_for_speech(buf[:m.end()])
                    buf = buf[m.end():]
                    if sentence:
                        spoken.append(sentence)
                        yield sentence
                if data.get("done"):
                    break
        except requests.RequestException:
            yield "Oh no, my brain is not answering! Maybe Ollama is still waking up. Try me again in a minute!"
            return

        tail = clean_for_speech(buf)
        if tail:
            spoken.append(tail)
            yield tail

        reply = " ".join(spoken)
        if reply and not self.interrupt.is_set():
            with self.lock:
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": reply})
                # Trim from the front, keeping pairs, so the prefix changes rarely.
                while len(self.history) > self.max_history:
                    del self.history[:2]


def clean_for_speech(text):
    text = STRIP.sub("", text)
    return re.sub(r"\s+", " ", text).strip()
