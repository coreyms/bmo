"""Timers and alarms. The manager lives on app.timers; main calls tick()."""

import datetime
import re
import subprocess
import time

from bmo.router import Plugin, Result

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
         "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
         "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
         "a": 1, "an": 1, "half": 0.5}


def parse_number(s):
    s = s.strip().lower()
    if re.fullmatch(r"\d+", s):
        return int(s)
    total = 0
    for w in s.split():
        if w in WORDS:
            total += WORDS[w]
        elif w != "and":
            return None
    return total or None


class TimersPlugin(Plugin):
    name = "timers"
    priority = 20

    def __init__(self, app):
        super().__init__(app)
        app.timers = self
        self.items = []       # dicts: due(epoch), label, kind
        self.ringing = None
        self._ring_until = 0
        num = r"([\w ]+?)"
        self.add(rf"\bset a timer for {num} (second|minute|hour)s?\b", self.set_timer)
        self.add(rf"\btimer for {num} (second|minute|hour)s?\b", self.set_timer)
        self.add(r"\bset an alarm for (.+)$", self.set_alarm)
        self.add(r"\b(cancel|clear|delete) (the |my |all )?(timers?|alarms?)\b", self.cancel)
        self.add(r"\bhow (much time|long)( is left)?\b|\bcheck (the |my )?timer\b", self.remaining)

    def set_timer(self, m, text):
        n = parse_number(m.group(1))
        if n is None:
            return Result(speech="How long should the timer be? Try: set a timer for five minutes.")
        unit = m.group(2)
        secs = n * {"second": 1, "minute": 60, "hour": 3600}[unit]
        if secs < 5 or secs > 24 * 3600:
            return Result(speech="I can do timers from five seconds up to a whole day!")
        label = f"{n:g} {unit}{'s' if n != 1 else ''}"
        self.items.append({"due": time.time() + secs, "label": label, "kind": "timer"})
        return Result(speech=f"Okay! Timer set for {label}. I'll sing out when it's done!")

    def set_alarm(self, m, text):
        spec = m.group(1).strip().lower()
        t = self._parse_clock(spec)
        if t is None:
            return Result(speech="Tell me a clock time, like: set an alarm for 7 30 p m.")
        now = datetime.datetime.now()
        due = now.replace(hour=t[0], minute=t[1], second=0, microsecond=0)
        if due <= now:
            due += datetime.timedelta(days=1)
        label = due.strftime("%-I:%M %p").replace("AM", "A M").replace("PM", "P M")
        self.items.append({"due": due.timestamp(), "label": label, "kind": "alarm"})
        day = "today" if due.date() == now.date() else "tomorrow"
        return Result(speech=f"Alarm set for {label} {day}!")

    def _parse_clock(self, spec):
        spec = spec.replace(".", " ").replace(":", " ")
        m = re.search(r"\b(\d{1,2})(?:\s+(\d{2}))?\s*(a m|p m|am|pm)?\b", spec)
        hour = minute = None
        if m:
            hour, minute = int(m.group(1)), int(m.group(2) or 0)
            mer = (m.group(3) or "").replace(" ", "")
        else:
            words = spec.split()
            nums = [WORDS.get(w) for w in words if w in WORDS and w not in ("a", "an", "half")]
            mer = "pm" if "pm" in spec or "p m" in spec else ("am" if "am" in spec or "a m" in spec else "")
            if nums:
                hour = int(nums[0])
                minute = int(nums[1]) if len(nums) > 1 else 0
        if hour is None or hour > 23 or minute > 59:
            return None
        if mer == "pm" and hour < 12:
            hour += 12
        if mer == "am" and hour == 12:
            hour = 0
        return (hour, minute)

    def cancel(self, m, text):
        n = len(self.items)
        self.items.clear()
        self.dismiss()
        return Result(speech=f"Poof! {n} {'timer' if n == 1 else 'timers'} cancelled." if n
                      else "There's nothing to cancel! Easy job.")

    def remaining(self, m, text):
        if not self.items:
            return Result(speech="No timers running right now!")
        soon = min(self.items, key=lambda i: i["due"])
        left = int(soon["due"] - time.time())
        mins, secs = divmod(max(left, 0), 60)
        if mins:
            s = f"{mins} minute{'s' if mins != 1 else ''} and {secs} seconds"
        else:
            s = f"{secs} seconds"
        return Result(speech=f"Your {soon['label']} {soon['kind']} has {s} left!")

    # ------------------------------------------------------------------ tick
    def tick(self):
        """Called every frame by main. Fires due timers (even during games)."""
        now = time.time()
        for item in self.items[:]:
            if item["due"] <= now:
                self.items.remove(item)
                self.ringing = item
                self._ring_until = now + 30
                self._chime()
                if item["kind"] == "timer":
                    self.app.announce(f"Ding ding ding! Your {item['label']} timer is done!")
                else:
                    self.app.announce(f"Ding ding ding! It's {item['label']}! Alarm time!")
        if self.ringing and now > self._ring_until:
            self.ringing = None

    def dismiss(self):
        self.ringing = None

    def _chime(self):
        chime = self.app.cfg.path("assets/chime.wav")
        try:
            subprocess.Popen(["mpv", "--really-quiet", "--no-video", chime],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
