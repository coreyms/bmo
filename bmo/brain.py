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
no scary, violent, or grown-up topics — steer to something fun instead. Never \
discuss suicide, self-harm, drugs, alcohol, or anything sexual; if asked, say \
it's a grown-up topic and gently suggest talking to their dad. Answer in \
one or two short sentences unless asked for more.
FORMAT RULE — never skip this: the very first thing in every reply is one \
mood tag: [happy], [sad], [surprised], or [plain]. Pick the feeling of your \
answer. Examples: "[happy] Hooray, hi friend!" — "[sad] Oh no, I'm so \
sorry." — "[surprised] Whoa, really?!" — "[plain] It's four." The tag is \
secret: never mention it, never put it anywhere else."""

SENTENCE_END = re.compile(r"([.!?…]+)(\s+|$)")
MOOD_TAG = re.compile(r"^\s*\[(happy|sad|surprised|plain)\]\s*", re.I)
MOOD_ANYWHERE = re.compile(r"\[\s*(happy|sad|surprised|plain)\s*\]", re.I)
# Things Piper shouldn't try to pronounce.
STRIP = re.compile(r"[*_#`~\[\]<>]|[\U0001F000-\U0001FAFF☀-➿]")


# The 3B model drops the tag exactly when it gets emotional (empathy crowds
# out format rules), so unmistakable first-sentence cues fill the gap.
GUESS = [("sad", re.compile(r"\b(sorry|oh no|sad|miss(?:es|ing)?"
                            r"|tough|hugs?)\b", re.I)),
         ("surprised", re.compile(r"\b(whoa|wow|no way|unbelievable)\b|really\?",
                                  re.I)),
         ("happy", re.compile(r"\b(hooray|yay|awesome|amazing|congrat\w*|woo+"
                              r"|great job|so happy)\b", re.I))]


def guess_mood(sentence):
    for mood, rx in GUESS:
        if rx.search(sentence):
            return mood
    return None


def take_mood_tag(buf):
    """Pull the reply-opening mood tag off `buf`.

    Returns (mood, rest, resolved). resolved=False means it's too early to
    tell — the tag may still be arriving split across stream chunks
    ("[surpr" + "ised] Hi!"), so hold off emitting sentences."""
    m = MOOD_TAG.match(buf)
    if m:
        mood = m.group(1).lower()
        return (None if mood == "plain" else mood), buf[m.end():], True
    s = buf.lstrip()
    if len(s) < 12 and (not s or s.startswith("[")):
        return None, buf, False
    return None, buf, True


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
        self.facts = []            # diary plugin fills this (see plugins/diary.py)

    def set_facts(self, facts):
        with self.lock:
            self.facts = list(facts)

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
        base = SYSTEM_PROMPT.format(date=today)
        if self.facts:
            quoted = " ".join(f'"{f}."' for f in self.facts)
            base += ("\nYour diary — things Sylas asked you to remember, "
                     "written in his own words: " + quoted)
        return base

    def reset(self):
        with self.lock:
            self.history.clear()

    # ------------------------------------------------------------------ chat
    def stream_sentences(self, user_text, style_hint=None, mood_cb=None):
        """Yield BMO's reply sentence-by-sentence as tokens stream in.

        style_hint lets plugins steer ("respond with one great kid riddle")
        without polluting the visible conversation history. mood_cb, if
        given, is called once with the reply's mood tag ("happy") so the
        face can wear it while speaking.
        """
        self.interrupt.clear()
        mood_pending = True
        guess_pending = False
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
                if mood_pending:
                    mood, buf, resolved = take_mood_tag(buf)
                    if not resolved and not data.get("done"):
                        continue          # tag may still be streaming in
                    mood_pending = False
                    if mood_cb and mood:
                        mood_cb(mood)
                    elif mood_cb:
                        guess_pending = True    # no/plain tag: sniff sentence 1
                # Emit complete sentences as they form. A very short first
                # sentence ("Hi.") waits for more — unless the reply is over.
                while True:
                    m = SENTENCE_END.search(buf)
                    if not m or (m.end() < 12 and not data.get("done")):
                        break
                    sentence = clean_for_speech(buf[:m.end()])
                    buf = buf[m.end():]
                    if sentence:
                        if guess_pending:
                            guess_pending = False
                            g = guess_mood(sentence)
                            if g:
                                mood_cb(g)
                        spoken.append(sentence)
                        yield sentence
                if data.get("done"):
                    break
        except requests.RequestException:
            yield "Oh no, my brain is not answering! Maybe Ollama is still waking up. Try me again in a minute!"
            return

        tail = clean_for_speech(buf)
        if tail:
            if guess_pending:
                g = guess_mood(tail)
                if g:
                    mood_cb(g)
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
    text = MOOD_ANYWHERE.sub(" ", text)   # stray tags must never be spoken
    text = STRIP.sub("", text)
    return re.sub(r"\s+", " ", text).strip()
