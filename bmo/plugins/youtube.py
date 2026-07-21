"""YouTube videos over BMO's face, by voice — with a picker.

A blind "top search hit" launch stacks two error sources (fuzzy search x
imperfect speech recognition), so "watch minecraft parkour" shows the top
six results in the same on-screen picker games use: D-pad + Start, a tap,
or voice ("the first one", "number three"). During playback the ears drop
to wake-word-only mode — say "BMO" to stop, or press Start.

The safety plugin screens every query before it gets here; queries and
picks are logged for parent review.
"""

import subprocess
import time

from bmo.router import Plugin, Result

SEARCH_N = 6
NUMBER_WORDS = {"first": 0, "one": 0, "top": 0,
                "second": 1, "two": 1,
                "third": 2, "three": 2,
                "fourth": 3, "four": 3,
                "fifth": 4, "five": 4,
                "sixth": 5, "six": 5,
                "last": -1}


class YouTubePlugin(Plugin):
    name = "youtube"
    priority = 30    # ahead of games (40) so "play X on youtube" wins

    def __init__(self, app):
        super().__init__(app)
        self.add(r"\bplay\s+(.+?)\s+on youtube$", self.watch)
        self.add(r"\b(?:watch|youtube)\s+(.+)$", self.watch)
        self.add(r"\bplay\s+(?:the\s+)?(.+?)\s+video$", self.watch)
        self.add(r"\b(?:stop|quit|close|end)\s+(?:the\s+)?(?:video|youtube)\b",
                 self.stop)
        # picking from any open on-screen menu by voice (works for the
        # game picker too)
        self.add(r"^(?:play |pick |choose |select )?(?:the )?(?:number )?"
                 r"(first|second|third|fourth|fifth|sixth|one|two|three|four"
                 r"|five|six|top|last)(?: one| video| game)?$", self.pick)

    def _search(self, q, n=SEARCH_N):
        """Top-n flat search: [(title, url, duration_str)]. Fast — no
        per-video format probing."""
        try:
            out = subprocess.run(
                ["yt-dlp", f"ytsearch{n}:{q}", "--flat-playlist",
                 "--print", "%(title)s\t%(url)s\t%(duration_string)s",
                 "--no-warnings"],
                capture_output=True, text=True, timeout=25)
            vids = []
            for line in out.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[0] and parts[1].startswith("http"):
                    dur = parts[2] if len(parts) > 2 and parts[2] not in ("NA", "") else ""
                    vids.append((parts[0], parts[1], dur))
            return vids
        except Exception as e:
            if self.app.logger:
                self.app.logger.log("error", f"youtube search: {e}")
            return []

    def watch(self, m, text):
        q = m.group(1).strip()
        if not q or q in ("a", "the", "it"):
            return Result(speech="What should I look for on YouTube?")
        if self.app.game_running():
            return Result(speech="One screen at a time! Say stop first.")
        self.app.voice.say(f"Searching YouTube for {q}!")   # cover the wait
        vids = self._search(q)
        if not vids:
            return Result(speech="I couldn't find anything! Maybe the "
                                 "internet is being shy. Try again?")
        items = [((f"{t}  ({d})" if d else t), t, url, "youtube")
                 for t, url, d in vids]
        self.app.game_menu = {"items": items, "sel": 0, "top": 0,
                              "at": time.time(), "rects": []}
        return Result(speech=f"I found {len(vids)} videos! Pick one with the "
                             "controller or a tap, or say: the first one.",
                      expression="happy")

    def pick(self, m, text):
        menu = self.app.game_menu
        if not menu:
            return None          # bare number with nothing to pick from
        idx = NUMBER_WORDS[m.group(1)]
        if idx == -1:
            idx = len(menu["items"]) - 1
        if idx >= len(menu["items"]):
            return Result(speech=f"There are only {len(menu['items'])} to "
                                 "pick from!")
        self.app._menu_launch(idx)
        return Result()          # _menu_launch does the talking

    def stop(self, m, text):
        if self.app._video and self.app.quit_game():
            return Result(speech="Okay! Video off.")
        return None      # not a video situation — someone else's "stop"
