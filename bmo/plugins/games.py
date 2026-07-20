"""Launch NES/SNES/Sega games in RetroArch by voice, fuzzy-matched by title.

Also answers library questions without the LLM: "what mega man games do
we have", "do we have sonic", "play aladdin on genesis"."""

import difflib
import os
import re
import time

from bmo.router import Plugin, Result

ROM_EXTS = {".nes", ".sfc", ".smc", ".zip", ".fig",
            ".md", ".gen", ".smd", ".bin", ".sms", ".gg"}

# Spoken system names -> library kind, and how BMO says each kind aloud.
SYSTEM_WORDS = {"nes": "nes", "nintendo": "nes", "regular nintendo": "nes",
                "snes": "snes", "super nintendo": "snes",
                "genesis": "genesis", "sega genesis": "genesis",
                "mega drive": "genesis", "sega": "genesis",
                "master system": "sms", "sega master system": "sms", "sms": "sms",
                "game gear": "gamegear", "gamegear": "gamegear"}
SYSTEM_SPOKEN = {"nes": "N E S", "snes": "Super Nintendo", "genesis": "Genesis",
                 "sms": "Master System", "gamegear": "Game Gear"}
SYSTEM_RX = re.compile(
    r"\s+(?:on|for)\s+(?:the\s+)?("
    + "|".join(sorted(SYSTEM_WORDS, key=len, reverse=True)) + r")$")
JUNK = re.compile(r"\(.*?\)|\[.*?\]|\.[^.]+$")


def clean_title(filename):
    t = JUNK.sub("", filename)
    return re.sub(r"[_\-.]+", " ", t).strip().lower()


class GamesPlugin(Plugin):
    name = "games"
    priority = 40   # after music/jokes so "play some music" never launches a ROM

    def __init__(self, app):
        super().__init__(app)
        self._more = None      # unread tail of a long listing + timestamp
        self.add(r"^(?:yes|yeah|yep|sure|okay|ok|more|yes please|more please|"
                 r"tell me more|keep going|hear more|the rest|what else)$",
                 self.hear_more)
        self.add(r"^(?:no|nope|nah|no thanks|no thank you|that's enough|"
                 r"i'm good)$", self.no_more)
        self.add(r"\bwhat games\b|\blist( my| the)? games\b", self.list_games)
        self.add(r"\b(?:what|which)\s+(.+?)\s+games?(?:\s+(?:do (?:we|you|i) have|"
                 r"are there|have we got))?$", self.search)
        self.add(r"^do (?:we|you|i) have (?:any |a |the game )?(.+?)(?:\s+games?)?$",
                 self.have)
        self.add(r"\b(?:quit|exit|close|end)\s+(?:the\s+|this\s+)?game\b|\bstop playing\b",
                 self.quit)
        self.add(r"\b(?:play|start|launch|open)\s+(?:the game\s+)?(.+)$", self.play)

    def library(self):
        """[(spoken_title, path, kind)] scanned fresh each time — Corey may
        drop ROMs in while BMO is running."""
        out = []
        for kind in ("nes", "snes", "genesis", "sms", "gamegear"):
            d = self.app.cfg.path(self.app.cfg.get("games", f"roms_{kind}"))
            if not d or not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if os.path.splitext(f)[1].lower() in ROM_EXTS:
                    out.append((clean_title(f), os.path.join(d, f), kind))
        return out

    def quit(self, m, text):
        if self.app.quit_game():
            return Result(speech="Okay! Game over. Back to being BMO!")
        return Result(speech="We aren't playing a game right now!")

    # ------------------------------------------------- library search (no LLM)
    def _hits(self, q, lib):
        """Titles containing every query word (whole words, so 'mega man'
        never matches Mega Bomberman); deduped across systems."""
        words = q.split()
        return sorted({t for t, _, _ in lib
                       if all(w in t.split() for w in words)})

    def _format_names(self, hits, lib):
        """Spoken name per title; duplicates across systems get labeled."""
        kinds = {}
        for t, _, k in lib:
            kinds.setdefault(t, set()).add(k)
        out = []
        for t in hits:
            ks = sorted(kinds.get(t, ()))
            if len(ks) > 1:
                out.append(f"{t.title()} on "
                           + " and ".join(SYSTEM_SPOKEN[k] for k in ks))
            else:
                out.append(t.title())
        return out

    CHUNK = 8

    def _speak_list(self, intro, names):
        """Speak the first chunk; long lists park the rest behind a question."""
        chunk = ", ".join(names[:self.CHUNK])
        if len(names) <= self.CHUNK:
            self._more = None
            return Result(speech=f"{intro} {chunk}.")
        self._more = {"names": names, "pos": self.CHUNK, "at": time.time()}
        return Result(speech=f"{intro} {chunk}. That's {self.CHUNK} of "
                             f"{len(names)} — want to hear more?")

    def hear_more(self, m, text):
        p = self._more
        if not p or time.time() - p["at"] > 120:
            return None            # nothing pending — not our "yes"
        chunk = p["names"][p["pos"]:p["pos"] + self.CHUNK]
        p["pos"] += len(chunk)
        p["at"] = time.time()
        left = len(p["names"]) - p["pos"]
        if left > 0:
            return Result(speech=f"{', '.join(chunk)}. "
                                 f"{left} more to go — keep going?")
        self._more = None
        return Result(speech=f"{', '.join(chunk)}. And that's all of them!")

    def no_more(self, m, text):
        if self._more:
            self._more = None
            return Result(speech="Okay!")
        return None                # not our "no"

    def search(self, m, text):
        """'what mega man games do we have' / 'which genesis games are there'"""
        q = m.group(1).strip().lower()
        lib = self.library()
        if not lib:
            return Result(speech="I don't have any games yet! Your dad needs to "
                                 "put some in my roms folder.")
        kind = SYSTEM_WORDS.get(q)
        if kind:                       # whole-system listing
            hits = sorted(t for t, _, k in lib if k == kind)
            if not hits:
                return Result(speech=f"No {SYSTEM_SPOKEN[kind]} games yet!")
            return self._speak_list(
                f"We have {len(hits)} {SYSTEM_SPOKEN[kind]} games!",
                [t.title() for t in hits])
        hits = self._hits(q, lib)
        if not hits:
            return Result(speech=f"I don't see any {q} games in my library. "
                                 "Say list games to hear what I have!")
        n = len(hits)
        return self._speak_list(
            f"We have {n} {q} game{'s' if n != 1 else ''}:",
            self._format_names(hits, lib))

    def have(self, m, text):
        """'do we have sonic' — answers if it's a game, else lets the LLM try."""
        q = m.group(1).strip().lower()
        lib = self.library()
        hits = self._hits(q, lib) if lib else []
        if not hits:
            return None            # probably not a game question — brain's turn
        n = len(hits)
        if n > self.CHUNK:
            return self._speak_list(f"Yes! We have {n} {q} games:",
                                    self._format_names(hits, lib))
        return Result(speech=f"Yes! We have {n} {q} game{'s' if n != 1 else ''}: "
                             f"{', '.join(self._format_names(hits, lib))}. "
                             f"Say play {hits[0].title()} to start!")

    def list_games(self, m, text):
        lib = self.library()
        if not lib:
            return Result(speech="I don't have any games yet! Your dad needs to "
                                 "put some in my roms folder.")
        hits = sorted({t for t, _, _ in lib})
        return self._speak_list(f"I have {len(hits)} games!",
                                self._format_names(hits, lib))

    def play(self, m, text):
        want = m.group(1).strip().lower()
        kind_want = None
        sm = SYSTEM_RX.search(want)
        if sm:                          # "play aladdin on genesis"
            kind_want = SYSTEM_WORDS[sm.group(1)]
            want = want[:sm.start()].strip()
        lib = self.library()
        if kind_want:
            lib = [e for e in lib if e[2] == kind_want]
            if not lib:
                return Result(speech=f"I don't have any "
                                     f"{SYSTEM_SPOKEN[kind_want]} games yet!")
        if not lib:
            if any(w in want for w in ("game", "mario", "zelda", "kirby", "nintendo")):
                return Result(speech="I don't have any games yet! Your dad needs to "
                                     "put some in my roms folder.")
            return None   # not obviously a game request — let others/brain try
        scored = []
        for i, (t, path, _) in enumerate(lib):
            r = difflib.SequenceMatcher(None, want, t).ratio()
            if want == t:
                r = 2.0          # exact title always wins
            elif want in t or all(w in t.split() for w in want.split()):
                # contained phrase bumps the score ("mario" inside "super
                # mario world"); shorter titles win ties so "mario" finds
                # "mario paint", not some 40-character spin-off
                r = max(r, 0.85 + 0.14 * len(want) / len(t))
            fname = os.path.basename(path).lower()
            if r < 2.0 and "(j)" in fname and "(u" not in fname:
                r -= 0.03        # near-tie: prefer the English release
            scored.append((r, -len(t), i))
        score, _, best = max(scored)
        title, path, kind = lib[best]
        if score >= 0.5 and kind_want is None:
            # same title on several systems and no system asked for -> clarify
            others = sorted({k for t2, _, k in lib if t2 == title})
            if len(others) > 1:
                systems = " and ".join(SYSTEM_SPOKEN[k] for k in others)
                return Result(speech=f"We have {title.title()} on {systems}! "
                                     f"Say play {title.title()} on one of those.")
        if score >= 0.5:
            core = self.app.cfg.find_core(kind)
            if not core:
                return Result(speech="Uh oh, my game-playing part is missing! "
                                     "Tell your dad the emulator core is not installed.")
            ok = self.app.launch_game(core, path, title.title())
            return Result(speech=f"Let's play {title.title()}! Here we go!" if ok
                          else "Hmm, the game would not start. Sorry!")
        if score >= 0.3:
            return Result(speech=f"Hmm, I don't have that one. Did you mean {title.title()}?")
        return None   # doesn't sound like our library — brain can riff on it
