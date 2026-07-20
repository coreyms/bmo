"""Built-in commands: stop (context-aware), sleep, time/date, volume,
brain switching, developer mode, and "what can you do"."""

import datetime
import subprocess

from bmo.router import Plugin, Result


class SystemPlugin(Plugin):
    name = "system"
    priority = 10

    def __init__(self, app):
        super().__init__(app)
        self.add(r"^(stop|be quiet|quiet|shush|hush|shut up|never ?mind)\b", self.stop)
        self.add(r"\b(go to sleep|good ?night|see you later)\b", self.sleep)
        self.add(r"\bwhat time is it\b|\bwhat's the time\b", self.time)
        self.add(r"\bwhat (day|date) is (it|today)\b|\bwhat's today\b", self.date)
        self.add(r"\b(volume up|louder|turn it up)\b", lambda m, t: self.volume(+10)),
        self.add(r"\b(volume down|quieter|too loud|turn it down)\b", lambda m, t: self.volume(-10))
        self.add(r"\bset (the )?volume to (\d{1,3})\b", self.volume_set)
        self.add(r"\buse your (big|little|small) brain\b|\bswitch brains?\b", self.switch_brain)
        self.add(r"\bdeveloper mode\b", self.dev_mode)
        self.add(r"\bwhat can you do\b|\bhelp me\b", self.help)

    # "stop" means different things depending on what's happening (plan:
    # intent precedence). Order: talking > alarm > music > go to sleep.
    def stop(self, m, text):
        app = self.app
        if app.voice.busy():
            app.voice.stop()
            return Result()
        timers = getattr(app, "timers", None)
        if timers is not None and timers.ringing:
            timers.dismiss()
            return Result(speech="Okay! Alarm off.")
        music = getattr(app, "music", None)
        if music is not None and music.playing():
            music.stop()
            return Result(speech="Music off!")
        app.request_sleep()
        return Result()

    def sleep(self, m, text):
        self.app.request_sleep(say_bye=True)
        return Result()

    def time(self, m, text):
        now = datetime.datetime.now()
        return Result(speech=f"It's {now.strftime('%-I:%M %p').replace('AM', 'A M').replace('PM', 'P M')}.")

    def date(self, m, text):
        today = datetime.date.today()
        return Result(speech=f"Today is {today.strftime('%A, %B %-d')}!")

    def volume(self, delta):
        sign = "+" if delta > 0 else "-"
        ok = self._set_volume(f"{abs(delta)}%{sign}", f"{sign}{abs(delta)}%")
        return Result(speech="Okay!" if ok else "I can't find my volume knob yet!")

    def volume_set(self, m, text):
        pct = max(0, min(100, int(m.group(2))))
        ok = self._set_volume(f"{pct}%", f"{pct}%")
        return Result(speech=f"Volume {pct} percent!" if ok else "I can't find my volume knob yet!")

    def _set_volume(self, wpctl_value, pactl_value):
        # wpctl wants "10%+", pactl wants "+10%" — try PipeWire first.
        for cmd in (["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", wpctl_value],
                    ["pactl", "set-sink-volume", "@DEFAULT_SINK@", pactl_value]):
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=5)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    def switch_brain(self, m, text):
        which = "small" if m.group(1) in ("little", "small") else "big"
        if "switch" in text and m.group(1) is None:
            which = "small" if self.app.brain.which == "big" else "big"
        if self.app.brain.which == which:
            return Result(speech="I'm already using that brain, silly!")
        self.app.brain.set_model(which)
        self.app.brain.warm_up()
        size = "big" if which == "big" else "little"
        return Result(speech=f"Okay! Give me a second, I'm switching to my {size} brain. It takes a moment to wake up!")

    def dev_mode(self, m, text):
        self.app.request_exit_confirm()
        return Result()

    def help(self, m, text):
        return Result(speech="I can chat, tell jokes, set timers, play music, "
                             "check the weather, and start video games! "
                             "Try saying: play a game, or set a timer for ten minutes!")
