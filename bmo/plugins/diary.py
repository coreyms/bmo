"""BMO's diary: long-term memory that survives reboots.

"Remember that my favorite color is teal" writes a fact into var/diary.json.
Facts are handed to the brain, which folds them into the system prompt, so
"what's my favorite color?" needs no special handling — the LLM just knows.
Facts are stored verbatim (in the speaker's words) so nobody has to rewrite
pronouns; the prompt frames them as quotes from Sylas.

Dad note: the diary is plain JSON in var/diary.json — easy to review or edit.
"""

import json
import os
import threading

from bmo.router import Plugin, Result

MAX_FACTS = 30           # keeps the system prompt small and prefill fast
MAX_FACT_CHARS = 120

WIPE_WORDS = {"everything", "it all", "all of it", "your whole diary"}


class DiaryPlugin(Plugin):
    name = "diary"
    priority = 18        # after safety/system/smalltalk, before jokes/games

    def __init__(self, app):
        super().__init__(app)
        self.path = os.path.join(app.cfg.root, "var", "diary.json")
        self._lock = threading.Lock()
        self.facts = self._load()
        app.brain.set_facts(self.facts)
        self.add(r"^(?:please )?(?:can you )?remember (?:that )?(.+)$", self.remember)
        self.add(r"^(?:please )?(?:can you )?forget (?:that |about )?(.+)$", self.forget)
        self.add(r"^what (?:do you|all do you) remember(?: about (?:me|us|sylas))?$",
                 self.recall)
        self.add(r"^what(?:'s| is) in your diary$", self.recall)

    # ------------------------------------------------------------- handlers
    def remember(self, m, text):
        fact = m.group(1).strip().rstrip(".!?")
        if len(fact) > MAX_FACT_CHARS:
            return Result(speech="Whoa, that's a lot for my little diary! "
                                 "Can you say it shorter?")
        with self._lock:
            if any(f.lower() == fact.lower() for f in self.facts):
                return Result(speech="I already have that one in my diary!")
            if len(self.facts) >= MAX_FACTS:
                return Result(speech="My diary is totally stuffed! "
                                     "Ask me to forget something first.")
            self.facts.append(fact)
            self._save()
        self.app.logger.log("diary", f"remember: {fact}")
        return Result(speech="Okay! I wrote it in my diary.")

    def forget(self, m, text):
        needle = m.group(1).strip().rstrip(".!?").lower()
        with self._lock:
            if needle in WIPE_WORDS:
                n = len(self.facts)
                self.facts.clear()
                self._save()
                self.app.logger.log("diary", f"wiped {n} facts")
                return Result(speech="Poof! My diary is blank again.")
            hits = [f for f in self.facts if needle in f.lower()]
            if not hits:
                return Result(speech="Hmm, I don't have that in my diary.")
            for f in hits:
                self.facts.remove(f)
            self._save()
        self.app.logger.log("diary", f"forget: {'; '.join(hits)}")
        return Result(speech="Poof! I forgot it.")

    def recall(self, m, text):
        with self._lock:
            facts = list(self.facts)
        if not facts:
            return Result(speech="My diary is empty so far! "
                                 "Tell me something to remember.")
        listing = ". ".join(facts)
        return Result(speech=f"Here's what's in my diary! {listing}.")

    # ------------------------------------------------------------ persistence
    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            return [str(x)[:MAX_FACT_CHARS] for x in data][:MAX_FACTS]
        except Exception:
            return []

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.facts, f, indent=2)
        os.replace(tmp, self.path)
        self.app.brain.set_facts(self.facts)
