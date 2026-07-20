"""Launch NES/SNES games in RetroArch by voice, fuzzy-matched by title."""

import difflib
import os
import re

from bmo.router import Plugin, Result

ROM_EXTS = {".nes", ".sfc", ".smc", ".zip", ".fig"}
JUNK = re.compile(r"\(.*?\)|\[.*?\]|\.[^.]+$")


def clean_title(filename):
    t = JUNK.sub("", filename)
    return re.sub(r"[_\-.]+", " ", t).strip().lower()


class GamesPlugin(Plugin):
    name = "games"
    priority = 40   # after music/jokes so "play some music" never launches a ROM

    def __init__(self, app):
        super().__init__(app)
        self.add(r"\bwhat games\b|\blist( my| the)? games\b", self.list_games)
        self.add(r"\b(?:quit|exit|close|end)\s+(?:the\s+|this\s+)?game\b|\bstop playing\b",
                 self.quit)
        self.add(r"\b(?:play|start|launch|open)\s+(?:the game\s+)?(.+)$", self.play)

    def library(self):
        """[(spoken_title, path, kind)] scanned fresh each time — Corey may
        drop ROMs in while BMO is running."""
        out = []
        for kind in ("nes", "snes"):
            d = self.app.cfg.path(self.app.cfg.get("games", f"roms_{kind}"))
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if os.path.splitext(f)[1].lower() in ROM_EXTS:
                    out.append((clean_title(f), os.path.join(d, f), kind))
        return out

    def quit(self, m, text):
        if self.app.quit_game():
            return Result(speech="Okay! Game over. Back to being BMO!")
        return Result(speech="We aren't playing a game right now!")

    def list_games(self, m, text):
        lib = self.library()
        if not lib:
            return Result(speech="I don't have any games yet! Your dad needs to "
                                 "put some in my roms folder.")
        names = [t.title() for t, _, _ in lib[:12]]
        more = f", and {len(lib) - 12} more" if len(lib) > 12 else ""
        return Result(speech="I have " + ", ".join(names) + more + ". Want to play one?")

    def play(self, m, text):
        want = m.group(1).strip().lower()
        lib = self.library()
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
