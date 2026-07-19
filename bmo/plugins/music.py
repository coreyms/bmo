"""Play local music with mpv. Registered on app.music for the stop command."""

import difflib
import os
import subprocess

from bmo.router import Plugin, Result

AUDIO_EXTS = {".mp3", ".ogg", ".flac", ".wav", ".m4a", ".opus"}


class MusicPlugin(Plugin):
    name = "music"
    priority = 30

    def __init__(self, app):
        super().__init__(app)
        app.music = self
        self.proc = None
        self.add(r"\b(play|put on) (some |the )?(music|songs?|tunes?)\b", self.play_any)
        self.add(r"\bplay the song (.+)$", self.play_named)
        self.add(r"\b(stop|pause|turn off) the music\b", self.stop_cmd)
        self.add(r"\b(next|skip)( song| track)?\b", self.next_song)

    def _files(self):
        d = self.app.cfg.path(self.app.cfg.get("music", "dir", "music"))
        if not os.path.isdir(d):
            return []
        return [os.path.join(d, f) for f in sorted(os.listdir(d))
                if os.path.splitext(f)[1].lower() in AUDIO_EXTS]

    def playing(self):
        return self.proc is not None and self.proc.poll() is None

    def _start(self, args):
        self.stop()
        try:
            self.proc = subprocess.Popen(
                ["mpv", "--really-quiet", "--no-video"] + args,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False

    def play_any(self, m, text):
        files = self._files()
        if not files:
            return Result(speech="My music folder is empty! Your dad can put songs "
                                 "in it and then we'll have a dance party.")
        ok = self._start(["--shuffle"] + files)
        return Result(speech="Music time!" if ok else "Hmm, my music player broke!")

    def play_named(self, m, text):
        files = self._files()
        if not files:
            return Result(speech="My music folder is empty! Your dad can put songs in it.")
        want = m.group(1).lower()
        names = [os.path.splitext(os.path.basename(f))[0].lower() for f in files]
        scores = [difflib.SequenceMatcher(None, want, n).ratio() for n in names]
        best = max(range(len(files)), key=lambda i: scores[i])
        if scores[best] < 0.4 and want not in names[best]:
            return Result(speech="I don't know that song! Say: play some music, "
                                 "and I'll surprise you.")
        ok = self._start([files[best]])
        title = os.path.splitext(os.path.basename(files[best]))[0]
        return Result(speech=f"Playing {title}!" if ok else "Hmm, my music player broke!")

    def stop_cmd(self, m, text):
        if self.playing():
            self.stop()
            return Result(speech="Music off!")
        return Result(speech="No music is playing right now!")

    def next_song(self, m, text):
        if not self.playing():
            return None    # "next" might mean something else — let brain try
        files = self._files()
        ok = self._start(["--shuffle"] + files)
        return Result(speech="Next song!" if ok else "Hmm, my music player broke!")

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None
