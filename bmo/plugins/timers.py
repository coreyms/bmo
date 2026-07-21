"""Timers and alarms. The manager lives on app.timers; main calls tick().

Alarms are trusted with school mornings, so: every change is persisted to
var/alarms.json and reloaded at startup (a missed alarm is announced loudly,
never dropped in silence); ringing alarms re-chime for three minutes until
dismissed and force the volume up first; alarms can repeat daily/weekdays.
"""

import datetime
import json
import os
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

# Vosk hears clock digits as their homophones: "2:45" -> "to forty five".
HOMOPHONES = {"to": "two", "too": "two", "for": "four", "fore": "four",
              "won": "one", "ate": "eight", "oh": "zero", "o": "zero"}
CLOCK_WORDS = {**{k: v for k, v in WORDS.items() if k not in ("a", "an", "half")},
               "zero": 0}

REPEAT_RX = re.compile(
    r"\b(?:on |every |each )?(?:single )?"
    r"(?:(?P<daily>day|daily|morning)|(?P<week>(?:school|week) ?days?))\b")
REPEAT_SPOKEN = {"daily": "every day", "weekdays": "every school day",
                 "once": ""}

MAX_ALARMS = 3
ALARM_RING_SECS = 180
TIMER_RING_SECS = 45
CHIME_EVERY = 10
SHOUT_EVERY = 45
LATE_GRACE = 2 * 3600      # fire an alarm up to 2h late after downtime


def _elapsed(secs):
    """3725.4 -> '1 hour, 2 minutes and 5 seconds' (spoken-friendly)."""
    secs = int(secs)
    hours, rest = divmod(secs, 3600)
    mins, s = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    if s or not parts:
        parts.append(f"{s} second{'s' if s != 1 else ''}")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


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


def parse_clock(spec):
    """'to forty five p m' -> (14, 45). None when no time is found."""
    spec = spec.replace(".", " ").replace(":", " ").lower()
    if "noon" in spec:
        return (12, 0, True)
    if "midnight" in spec:
        return (0, 0, True)
    spec = " ".join(HOMOPHONES.get(w, w) for w in spec.split())
    mer = ("pm" if re.search(r"\bp ?m\b", spec)
           else "am" if re.search(r"\ba ?m\b", spec) else "")
    hour = minute = None
    m = re.search(r"\b(\d{1,2})(?:\s+(\d{1,2}))?\b", spec)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
    else:
        nums = [CLOCK_WORDS[w] for w in spec.split() if w in CLOCK_WORDS]
        if nums:
            hour, rest = int(nums[0]), nums[1:]
            minute = 0
            if rest:
                minute = int(rest[0])
                # "forty five" arrives as [40, 5]; "oh five" as [0, 5]
                if len(rest) > 1 and rest[0] in (0, 20, 30, 40, 50) and rest[1] < 10:
                    minute = int(rest[0] + rest[1])
    if hour is None or hour > 23 or minute is None or minute > 59:
        return None
    if mer == "pm" and hour < 12:
        hour += 12
    if mer == "am" and hour == 12:
        hour = 0
    return (hour, minute, mer != "")


class TimersPlugin(Plugin):
    name = "timers"
    priority = 20

    def __init__(self, app):
        super().__init__(app)
        app.timers = self
        self.items = []       # dicts: due(epoch), label, kind, repeat
        self.ringing = None
        self._ring_until = 0
        self._next_chime = 0
        self._next_shout = 0
        self._missed = []
        self.stopwatch = None      # epoch start, or None
        self._load()
        num = r"([\w ]+?)"
        self.add(rf"\bset a timer for {num} (second|minute|hour)s?\b", self.set_timer)
        self.add(rf"\btimer for {num} (second|minute|hour)s?\b", self.set_timer)
        # Anchor on "alarm for/at <time>", verb optional: Vosk regularly
        # mishears "set an" ("certain alarm for..."), and the time is the
        # part that matters. "wake me up at 7" is the kid-native phrasing.
        self.add(r"\b(?:an |the |my |a )?alarms? (?:for|at) (.+)$", self.set_alarm)
        self.add(r"\bwake me (?:up )?at (.+)$", self.set_alarm)
        self.add(r"\b(cancel|clear|delete) (the |my |all )?(timers?|alarms?)\b", self.cancel)
        # stopwatch intents outrank `remaining` — "how long has it been" is
        # a stopwatch question first, a timer question only as fallback
        self.add(r"\b(start|begin|set) (a |the |my )?stop ?watch\b|^stop ?watch$",
                 self.stopwatch_start)
        self.add(r"\b(stop|end|cancel|reset|clear) (the |my )?stop ?watch\b",
                 self.stopwatch_stop)
        self.add(r"\bhow long has it been\b|\bcheck (the |my )?stop ?watch\b",
                 self.stopwatch_check)
        self.add(r"\bhow (much time|long)( is left)?\b|\bcheck (the |my )?timer\b", self.remaining)

    # ---------------------------------------------------------- persistence
    def save(self):
        """Alarms must survive restarts — atomic write on every change."""
        try:
            path = self.app.cfg.path("var/alarms.json")
            with open(path + ".tmp", "w") as f:
                json.dump({"items": self.items}, f)
            os.replace(path + ".tmp", path)
        except Exception as e:
            if self.app.logger:
                self.app.logger.log("error", f"alarms save: {e}")

    def _load(self):
        try:
            with open(self.app.cfg.path("var/alarms.json")) as f:
                data = json.load(f)
        except Exception:
            return
        now = time.time()
        for it in data.get("items", []):
            rep = it.get("repeat", "once")
            if it["due"] > now or now - it["due"] <= LATE_GRACE:
                self.items.append(it)      # future — or late enough to still fire
            elif it["kind"] == "alarm" and rep != "once":
                it["due"] = _next_due_ts(it["due"], rep, now)
                self.items.append(it)      # recurring: quietly roll forward
            elif it["kind"] == "alarm":
                self._missed.append(it["label"])   # announce, never silent-drop

    # -------------------------------------------------------------- setting
    def set_timer(self, m, text):
        n = parse_number(m.group(1))
        if n is None:
            return Result(speech="How long should the timer be? Try: set a timer for five minutes.")
        unit = m.group(2)
        secs = n * {"second": 1, "minute": 60, "hour": 3600}[unit]
        if secs < 5 or secs > 24 * 3600:
            return Result(speech="I can do timers from five seconds up to a whole day!")
        label = f"{n:g} {unit}{'s' if n != 1 else ''}"
        self.items.append({"due": time.time() + secs, "label": label,
                           "kind": "timer", "repeat": "once"})
        self.save()
        return Result(speech=f"Okay! Timer set for {label}. I'll sing out when it's done!")

    def set_alarm(self, m, text):
        # questions about alarms ("do we have an alarm for school?") are
        # not requests to set one — let other plugins or the brain answer
        if re.match(r"^(do|does|is|are|was|when|what|what's|whats|why|how)\b", text):
            return None
        spec = m.group(1).strip().lower()
        rm = REPEAT_RX.search(spec)
        repeat = "once"
        if rm:
            repeat = "daily" if rm.group("daily") else "weekdays"
            spec = REPEAT_RX.sub(" ", spec)
        t = parse_clock(spec)
        if t is None:
            return Result(speech="Tell me a clock time, like: set an alarm for "
                                 "7 30 a m, or 2 45 p m every school day.")
        if len([i for i in self.items if i["kind"] == "alarm"]) >= MAX_ALARMS:
            return Result(speech=f"I can only remember {MAX_ALARMS} alarms! "
                                 "Tap one to cancel it, or say cancel my alarms.")
        hour, minute, had_meridiem = t
        now = datetime.datetime.now()
        # No am/pm said -> take whichever reading comes first ("2 45" at
        # 10am means 2:45 PM today, not 2:45 AM tomorrow).
        hours = [hour] if had_meridiem or hour == 0 or hour > 12 else [hour, (hour + 12) % 24]
        cands = []
        for hh in hours:
            c = now.replace(hour=hh % 24, minute=minute, second=0, microsecond=0)
            while c <= now or (repeat == "weekdays" and c.weekday() >= 5):
                c += datetime.timedelta(days=1)
            cands.append(c)
        due = min(cands)
        label = due.strftime("%-I:%M %p").replace("AM", "A M").replace("PM", "P M")
        self.items.append({"due": due.timestamp(), "label": label,
                           "kind": "alarm", "repeat": repeat})
        self.save()
        when = REPEAT_SPOKEN[repeat] or (
            "today" if due.date() == now.date() else "tomorrow")
        return Result(speech=f"Alarm set for {label} {when}! You can count on me.")

    # ------------------------------------------------------------- stopwatch
    def stopwatch_start(self, m, text):
        if self.stopwatch is not None:
            return Result(speech=f"The stopwatch is already going — "
                                 f"{_elapsed(time.time() - self.stopwatch)} so far!")
        self.stopwatch = time.time()
        return Result(speech="Stopwatch started! Go go go!")

    def stopwatch_stop(self, m, text):
        if self.stopwatch is None:
            return Result(speech="The stopwatch isn't running! Say start the "
                                 "stopwatch to begin.")
        s = _elapsed(time.time() - self.stopwatch)
        self.stopwatch = None
        return Result(speech=f"Stopwatch stopped at {s}! Nice one!")

    def stopwatch_check(self, m, text):
        if self.stopwatch is not None:
            return Result(speech=f"The stopwatch says {_elapsed(time.time() - self.stopwatch)}!")
        if self.items:
            return self.remaining(m, text)
        return Result(speech="I'm not timing anything right now!")

    def cancel(self, m, text):
        # "cancel my timer" must not nuke the school alarms (and vice versa)
        kind = "alarm" if "alarm" in m.group(3) else "timer"
        victims = [i for i in self.items if i["kind"] == kind]
        if not victims:
            return Result(speech=f"There's no {kind} to cancel! Easy job.")
        for v in victims:
            self.items.remove(v)
        self.dismiss()
        self.save()
        n = len(victims)
        return Result(speech=f"Poof! {n} {kind}{'s' if n != 1 else ''} cancelled.")

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
        for label in self._missed:
            self.app.announce(f"Uh oh! I was switched off and missed your "
                              f"{label} alarm. I'm really sorry!")
        self._missed = []
        for item in self.items[:]:
            if item["due"] <= now:
                is_alarm = item["kind"] == "alarm"
                if is_alarm and item.get("repeat", "once") != "once":
                    item["due"] = _next_due_ts(item["due"], item["repeat"], now + 60)
                else:
                    self.items.remove(item)
                self.save()
                self.ringing = dict(item)
                self._ring_until = now + (ALARM_RING_SECS if is_alarm else TIMER_RING_SECS)
                self._next_chime = now          # chime loop takes over
                self._next_shout = now + SHOUT_EVERY
                if is_alarm:
                    self._force_volume()
                    self.app.announce(f"Ding ding ding! It's {item['label']}! "
                                      "Alarm time! Wake up wake up!")
                else:
                    self.app.announce(f"Ding ding ding! Your timer for "
                                      f"{item['label']} is done!")
        if self.ringing:
            if now >= self._next_chime:
                self._chime()
                self._next_chime = now + CHIME_EVERY
            if self.ringing["kind"] == "alarm" and now >= self._next_shout:
                self.app.announce(f"Wake up! It's {self.ringing['label']}! "
                                  "Say stop when you're up!")
                self._next_shout = now + SHOUT_EVERY
            if now > self._ring_until:
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

    def _force_volume(self):
        """An alarm must not whisper because of last night's volume."""
        for cmd in ((["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
                     ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.85"]),
                    (["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
                     ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "85%"])):
            try:
                if all(subprocess.run(c, capture_output=True, timeout=3)
                       .returncode == 0 for c in cmd):
                    return
            except Exception:
                continue


def _next_due_ts(prev_due, repeat, after):
    """Next occurrence of prev_due's clock time, after `after`."""
    d = datetime.datetime.fromtimestamp(prev_due)
    cand = datetime.datetime.fromtimestamp(after).replace(
        hour=d.hour, minute=d.minute, second=0, microsecond=0)
    while (cand.timestamp() <= after
           or (repeat == "weekdays" and cand.weekday() >= 5)):
        cand += datetime.timedelta(days=1)
    return cand.timestamp()
