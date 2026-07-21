"""BMO entry point: pygame render loop + state machine.

States: SLEEPING -> LISTENING -> THINKING -> SPEAKING -> LISTENING ... and
GAMING while RetroArch owns the screen. The render loop owns the main thread;
brain/voice/ears work in threads and talk back through the `events` queue —
worker threads never touch pygame.

Run: python -m bmo.main [--dev] [--config path]
"""

import argparse
import datetime
import fcntl
import json
import math
import os
import queue
import random
import subprocess
import sys
import threading
import time

import pygame

from bmo.brain import Brain
from bmo.config import Config, app_dirs
from bmo.face import Face
from bmo.plugins.safety import mask as safety_mask
from bmo.router import Router

SLEEPING, LISTENING, THINKING, SPEAKING, GAMING = (
    "sleeping", "listening", "thinking", "speaking", "gaming")

STATE_FACE = {SLEEPING: "sleeping", LISTENING: "listening",
              THINKING: "thinking", SPEAKING: "speaking", GAMING: "gaming"}

GREETINGS = ["Yes? I'm listening!", "Hi! What's up?", "BMO is here!",
             "Ooh hi! What are we doing?", "Ready for adventure!"]

# Spoken instantly when a request heads to the LLM — masks thinking time.
# After first use these are cached audio, so they start in milliseconds.
THINKING_ACKS = ["Hmm, let me think!", "Okay okay, thinking!", "Thinking thinking...",
                 "Let me use my robot brain!", "Hmm hmm hmm...", "One second, computing!"]

GOODBYES = ["Okay! See you later!", "Bye bye! Come back soon!",
            "Goodbye, friend! I'll be right here!", "Bye! Have fun out there!",
            "Later, friend! Beep boop!", "Bye bye! Don't forget about me!"]

CORNER = 90          # px hot corner (top-right) for the parent escape hatch
HOLD_SECS = 2.5


class Logger:
    def __init__(self, cfg):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = os.path.join(cfg.root, "logs", f"session-{stamp}.jsonl")
        self._lock = threading.Lock()

    def log(self, kind, text, **meta):
        entry = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                 "kind": kind, "text": text, **meta}
        with self._lock:
            with open(self.path, "a") as f:
                f.write(json.dumps(entry) + "\n")


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        app_dirs(cfg)
        self.logger = Logger(cfg)
        self.events = queue.Queue()

        pygame.init()
        pygame.display.set_caption("BMO")
        if cfg.dev:
            self.screen = pygame.display.set_mode(
                (cfg.get("display", "width", 800), cfg.get("display", "height", 480)),
                pygame.RESIZABLE)
        else:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)

        self.face = Face(cfg)
        self.brain = Brain(cfg, self.logger)
        from bmo.voice import Voice
        self.voice = Voice(cfg, self.face, self.events, self.logger)
        from bmo.ears import Ears, MODE_LISTEN, MODE_WAKE
        self._mode_listen, self._mode_wake = MODE_LISTEN, MODE_WAKE
        self.ears = Ears(cfg, self.events, self.logger)
        self.ears.start()
        self.router = Router(self)   # plugins may attach app.timers / app.music

        self.state = SLEEPING
        self.window_deadline = 0
        self.typing = ""
        self.confirm_exit = False
        self._corner_down_at = None
        self._last_touch = 0.0
        self._game_proc = None
        self.running = True

        # 8BitDo pad drives BMO too (menu pick, wake, interrupt). Verified
        # X-input map: B=0 A=1 Y=2 X=3 L=4 R=5 Select=6 Start=7, dpad=axes 0/1.
        pygame.joystick.init()
        self._joys = {}
        self._open_joysticks()
        self._axis_step = {}       # axis -> last digital step, for edges
        self.game_menu = None      # set by games plugin on ambiguous "play X"
        self.timer_panel = None    # tap a status chip -> view/adjust/cancel
        self._chip_hits = []       # [(rect, payload)] from _draw_status_chips

        self.work_q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="router-brain").start()
        self.brain.warm_up()
        self.logger.log("info", f"BMO started (dev={cfg.dev}, model={self.brain.model})")

    # ----------------------------------------------------------------- state
    _mood = None      # face preset to wear while speaking ("happy"), or None

    def set_state(self, state):
        if state == self.state:
            return
        self.state = state
        if state in (LISTENING, SLEEPING, GAMING):
            self._mood = None      # the feeling belongs to the reply, not after
        self.face.set_expression(STATE_FACE[state])
        if self.ears.enabled:
            if state == SLEEPING:
                self.ears.set_mode(self._mode_wake)
            elif state == LISTENING:
                self.ears.set_mode(self._mode_listen)

    def wake(self, greet=True):
        self.set_state(LISTENING)
        self.window_deadline = time.time() + self.cfg.get("wake", "window_seconds", 120)
        if greet:
            self.voice.say(random.choice(GREETINGS))

    def request_sleep(self, say_bye=False):
        self.voice.stop()
        self.brain.interrupt.set()
        if say_bye:
            self.voice.say(random.choice(GOODBYES))
        self.set_state(SLEEPING)
        self.face.caption_top = ""
        self.face.caption_bottom = ""

    def request_exit_confirm(self):
        self.confirm_exit = True

    def _interrupt_speech(self):
        """Touch or Start button mid-speech: 'stop, you misheard me'."""
        self._gen += 1        # invalidate the in-flight LLM/worker output
        self.voice.stop()
        self.brain.interrupt.set()
        self.ears.mute(False)   # don't wait on a speak_end that may be gone
        self.set_state(LISTENING)
        self.window_deadline = time.time() + self.cfg.get("wake", "window_seconds", 120)

    # ------------------------------------------------------------------ work
    def submit(self, text):
        text = text.strip()
        if not text:
            return
        # A new request preempts whatever BMO is saying or thinking about:
        # kill playing audio, queued audio, in-flight LLM stream, old subtitle.
        self._gen += 1
        if self.voice.busy() or self._worker_busy:
            self.brain.interrupt.set()
            self.voice.stop()
        try:
            while True:
                self.work_q.get_nowait()
        except queue.Empty:
            pass
        self.face.caption_bottom = ""
        # Drop already-queued speech events (they'd repaint the old subtitle
        # this same frame); keep everything that isn't speech bookkeeping.
        keep = []
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] not in ("speak_start", "speak_end", "idle", "done"):
                    keep.append(ev)
        except queue.Empty:
            pass
        for ev in keep:
            self.events.put(ev)
        self.ears.mute(False)     # a dropped speak_end can't leave the mic dead
        self._mood = None         # each request earns its own feeling
        self.logger.log("user", text)
        self.face.caption_top = f"You: {safety_mask(text)}"
        self.set_state(THINKING)
        self.work_q.put(text)

    _worker_busy = False
    _last_ack = None
    _gen = 0          # bumped per submit; stale speech from older gens is dropped

    def _worker(self):
        while True:
            text = self.work_q.get()
            self._worker_busy = True
            gen = self._gen
            try:
                try:
                    res = self.router.route(text)
                except Exception as e:
                    self.logger.log("error", f"router: {e}")
                    continue
                if gen != self._gen:      # superseded while routing
                    continue
                if res.expression:
                    self._mood = res.expression
                if res.speech:
                    self.logger.log("bmo", res.speech)
                    for part in res.speech.split("\n"):   # \n = beat between parts
                        if gen != self._gen:
                            break
                        self.voice.say(part)
                elif res.brain_text:
                    # never the same ack twice in a row — kids notice
                    choices = [a for a in THINKING_ACKS if a != self._last_ack]
                    self._last_ack = random.choice(choices)
                    self.voice.say(self._last_ack)
                    reply = []
                    def _mood_cb(mood, g=gen):
                        if g == self._gen:
                            self._mood = mood
                    for sentence in self.brain.stream_sentences(
                            res.brain_text, res.style_hint, mood_cb=_mood_cb):
                        if gen != self._gen:  # a newer prompt owns the stage now
                            break
                        # last-resort output guard: the LLM must not wander
                        # into unsafe territory either
                        from bmo.plugins.safety import check as safety_check
                        from bmo.router import normalize as _norm
                        if safety_check(_norm(sentence)):
                            self.brain.interrupt.set()
                            self.logger.log("safety", sentence, tier="output")
                            self.voice.say("Oops! My brain went somewhere weird. "
                                           "Let's talk about something else!")
                            break
                        self.voice.say(sentence)
                        reply.append(sentence)
                    if reply:
                        self.logger.log("bmo", " ".join(reply), model=self.brain.model)
                else:
                    # handled silently (stop/sleep) — return to listening if awake
                    self.events.put(("idle", gen))
            finally:
                self._worker_busy = False
                self.events.put(("done", gen))

    def announce(self, text):
        """Out-of-band speech (timer/alarm firing) — even during games."""
        self.logger.log("bmo", text, kind2="announce")
        self.voice.say(text)

    # ----------------------------------------------------------------- games
    def game_running(self):
        return self._game_proc is not None and self._game_proc.poll() is None

    def launch_game(self, core, rom, title):
        if self.game_running():
            return False     # don't stack a second RetroArch on the first
        self.logger.log("info", f"launching game: {title}")
        if getattr(self, "music", None):
            self.music.stop()

        def _run():
            deadline = time.time() + 8      # let the intro line finish
            while self.voice.busy() and time.time() < deadline:
                time.sleep(0.1)
            self.events.put(("game_start", title))
            try:
                self._game_proc = subprocess.Popen(
                    [self.cfg.get("games", "retroarch", "retroarch"),
                     "-f", "-L", core, rom],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._game_proc.wait()
            except Exception as e:
                self.logger.log("error", f"retroarch: {e}")
            self.events.put(("game_over", title))

        threading.Thread(target=_run, daemon=True, name="game").start()
        return True

    def quit_game(self):
        """Terminate a running game; the waiter thread fires game_over."""
        if self.game_running():
            self._game_proc.terminate()
            return True
        return False

    # ------------------------------------------------------------------ loop
    def run(self):
        clock = pygame.time.Clock()
        fps = self.cfg.get("display", "fps", 60)
        while self.running:
            dt = clock.tick(fps) / 1000.0
            self._handle_events()
            self._drain_worker_events()
            self._tick_plugins()
            self._check_window()
            self._check_corner_hold()
            self.face.update(dt)
            self.face.draw(self.screen)
            self._draw_status_chips()
            self._draw_mic_button()
            self._draw_corner_hold()
            self._draw_game_menu()
            self._draw_timer_panel()
            if self.confirm_exit:
                self._draw_confirm()
            pygame.display.flip()
        pygame.quit()

    def _tick_plugins(self):
        for p in self.router.plugins:
            tick = getattr(p, "tick", None)
            if tick:
                try:
                    tick()
                except Exception as e:
                    self.logger.log("error", f"tick {p.name}: {e}")

    def _check_window(self):
        if (self.state == LISTENING and self.window_deadline
                and time.time() > self.window_deadline
                and not self.voice.busy()):
            self.logger.log("info", "conversation window timed out")
            self.request_sleep()

    # ---------------------------------------------------------------- events
    def _handle_events(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type == pygame.KEYDOWN:
                self._on_key(ev)
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                self._on_touch(*ev.pos)
            elif ev.type == pygame.MOUSEBUTTONUP:
                self._corner_down_at = None
            elif ev.type == pygame.FINGERDOWN:
                w, h = self.screen.get_size()
                self._on_touch(int(ev.x * w), int(ev.y * h))
            elif ev.type == pygame.FINGERUP:
                self._corner_down_at = None
            elif ev.type == pygame.JOYDEVICEADDED:
                self._open_joysticks()
            elif ev.type == pygame.JOYDEVICEREMOVED:
                self._joys.pop(ev.instance_id, None)
            elif ev.type == pygame.JOYBUTTONDOWN:
                self._on_pad_button(ev.button)
            elif ev.type == pygame.JOYAXISMOTION:
                self._on_pad_axis(ev.axis, ev.value)

    def _on_key(self, ev):
        if ev.key == pygame.K_ESCAPE:
            self.confirm_exit = not self.confirm_exit
        elif ev.key == pygame.K_RETURN:
            if self.typing:
                if self.state == SLEEPING:
                    self.wake(greet=False)
                self.submit(self.typing)
                self.typing = ""
        elif ev.key == pygame.K_BACKSPACE:
            self.typing = self.typing[:-1]
            self.face.caption_top = f"> {self.typing}"
        elif ev.unicode and ev.unicode.isprintable():
            self.typing += ev.unicode
            self.face.caption_top = f"> {self.typing}"

    def _on_touch(self, x, y):
        now = time.monotonic()
        if now - self._last_touch < 0.05:   # mouse+finger double-report
            return
        self._last_touch = now
        w, _ = self.screen.get_size()

        if self.confirm_exit:
            self._confirm_click(x, y)
            return
        if self.game_menu:
            for vi, r in enumerate(self.game_menu.get("rects", [])):
                if r.collidepoint(x, y):
                    idx = self.game_menu["top"] + vi   # rects are the window
                    self.game_menu["sel"] = idx
                    self._menu_launch(idx)
                    return
            self.game_menu = None      # tap anywhere else = cancel
            return
        if self.timer_panel:
            for i, r in enumerate(self.timer_panel.get("rects", [])):
                if r.collidepoint(x, y):
                    self.timer_panel["sel"] = i
                    self._panel_act(self.timer_panel["rows"][i][1])
                    return
            self.timer_panel = None    # tap anywhere else = close
            return
        for r, payload in self._chip_hits:
            if r.collidepoint(x, y):
                self._open_chip(payload)
                return
        if getattr(self, "_btn_mic", None) and self._btn_mic.collidepoint(x, y):
            muted = self.ears.toggle_user_mute()
            self.logger.log("info", f"mic {'muted' if muted else 'unmuted'} via touch")
            if muted:
                self.face.caption_top = ""   # drop any half-heard partial
            return
        if x > w - CORNER and y < CORNER:
            self._corner_down_at = now
            return
        if self.state == SLEEPING:
            self.wake(greet=True)
        elif self.voice.busy():
            self._interrupt_speech()

    # ------------------------------------------------------------- controller
    def _open_joysticks(self):
        for i in range(pygame.joystick.get_count()):
            try:
                j = pygame.joystick.Joystick(i)
                self._joys[j.get_instance_id()] = j
            except Exception:
                pass

    def _on_pad_button(self, btn):
        if self.state == GAMING:
            return                      # RetroArch owns the pad during games
        menu = self.game_menu
        if menu:
            if btn in (7, 1):           # Start or A picks
                self._menu_launch(menu["sel"])
            elif btn in (6, 0):         # Select or B cancels
                self.game_menu = None
                self.voice.say("Okay, never mind!")
            return
        panel = self.timer_panel
        if panel:
            if btn in (7, 1):           # Start or A activates the row
                self._panel_act(panel["rows"][panel["sel"]][1])
            elif btn in (6, 0):         # Select or B closes
                self.timer_panel = None
            return
        if btn == 7:                    # Start behaves like a touch
            if self.state == SLEEPING:
                self.wake(greet=True)
            elif self.voice.busy():
                self._interrupt_speech()

    def _on_pad_axis(self, axis, value):
        if self.state == GAMING or axis > 1:
            return
        step = 1 if value > 0.5 else (-1 if value < -0.5 else 0)
        prev = self._axis_step.get(axis, 0)
        self._axis_step[axis] = step
        # dpad is digital: act on the press edge only, vertical axis = 1
        if axis == 1 and step != 0 and prev == 0 and self.game_menu:
            m = self.game_menu
            m["sel"] = (m["sel"] + step) % len(m["items"])
            # keep the selection inside the visible window (list scrolls)
            if m["sel"] < m["top"]:
                m["top"] = m["sel"]
            elif m["sel"] >= m["top"] + self.MENU_VISIBLE:
                m["top"] = m["sel"] - self.MENU_VISIBLE + 1
        elif axis == 1 and step != 0 and prev == 0 and self.timer_panel:
            p = self.timer_panel
            p["sel"] = (p["sel"] + step) % len(p["rows"])
            p["at"] = time.time()

    def _menu_launch(self, idx):
        menu = self.game_menu
        self.game_menu = None
        label, title, path, kind = menu["items"][idx]
        core = self.cfg.find_core(kind)
        if not core:
            self.voice.say("Uh oh, my game-playing part is missing! "
                           "Tell your dad the emulator core is not installed.")
            return
        if self.launch_game(core, path, title):
            self.voice.say(f"Let's play {title}! Here we go!")

    def _check_corner_hold(self):
        if self._corner_down_at is None:
            return
        held = pygame.mouse.get_pressed()[0]   # SDL mirrors touch into mouse state
        if not held:
            self._corner_down_at = None
            return
        if time.monotonic() - self._corner_down_at >= HOLD_SECS:
            self._corner_down_at = None
            self.confirm_exit = True

    # ------------------------------------------------- worker-thread events
    def _drain_worker_events(self):
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "wake" and self.state == SLEEPING:
                self.logger.log("info", f"wake word: {payload}")
                self.wake(greet=True)
            elif kind == "utterance" and self.state == LISTENING:
                self.submit(payload)
            elif kind == "partial" and self.state == LISTENING:
                self.face.caption_top = safety_mask(payload)
            elif kind == "speak_start":
                if self.state not in (GAMING, SLEEPING):
                    self.set_state(SPEAKING)
                    # the reply's mood overrides the plain speaking face;
                    # lip sync still drives the mouth on top of it
                    if self._mood:
                        self.face.set_expression(self._mood)
                self.ears.mute(True)
                self.face.caption_bottom = f"BMO: {payload}"
            elif kind == "speak_end":
                # An announcement finishing mid-game must not reopen the mic;
                # game_over unmutes when RetroArch gives the screen back.
                if self.state != GAMING:
                    self.ears.mute(False)
                # Only leave SPEAKING when the whole reply is finished —
                # between-sentence gaps must not flap the state/expression.
                if (self.state == SPEAKING and not self._worker_busy
                        and not self.voice.busy()):
                    self.set_state(LISTENING)
                    self.window_deadline = time.time() + self.cfg.get(
                        "wake", "window_seconds", 120)
            elif kind == "done":
                # payload is the request generation: a superseded task's done
                # must not yank the new request out of THINKING early.
                if (payload == self._gen and self.state in (SPEAKING, THINKING)
                        and not self.voice.busy()):
                    self.set_state(LISTENING)
                    self.window_deadline = time.time() + self.cfg.get(
                        "wake", "window_seconds", 120)
            elif kind == "idle":
                if payload == self._gen and self.state == THINKING:
                    self.set_state(LISTENING)
            elif kind == "game_start":
                self.game_menu = None
                self.set_state(GAMING)
                self.ears.mute(True)
                pygame.display.iconify()
            elif kind == "game_over":
                # take the screen back from RetroArch
                if self.cfg.dev:
                    self.screen = pygame.display.set_mode(
                        self.screen.get_size(), pygame.RESIZABLE)
                else:
                    self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                self.ears.mute(False)
                self.wake(greet=False)
                self.voice.say("That was fun! What next?")

    # --------------------------------------------------------- status chips
    @staticmethod
    def _fmt_dur(secs):
        secs = max(0, int(secs))
        h, rest = divmod(secs, 3600)
        m, s = divmod(rest, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @staticmethod
    def _alarm_text(item):
        return item["label"].replace(" A M", " AM").replace(" P M", " PM")

    def _draw_status_chips(self):
        """Small translucent pills, top-left: live timer countdowns, set
        alarms, running stopwatch. Absent when nothing is active."""
        self._chip_hits = []
        tm = getattr(self, "timers", None)
        if tm is None or self.state == GAMING:
            return
        now = time.time()
        chips = []                       # (glyph, text, payload, urgent)
        if tm.ringing:
            g = "bell" if tm.ringing["kind"] == "alarm" else "timer"
            chips.append((g, "Ding! (tap)", "ringing", True))
        items = sorted(tm.items, key=lambda i: i["due"])
        for it in items[:3]:
            if it["kind"] == "timer":
                chips.append(("timer", self._fmt_dur(it["due"] - now), it, False))
            else:
                chips.append(("bell", self._alarm_text(it), it, False))
        if len(items) > 3:
            chips.append(("more", f"+{len(items) - 3} more", "list", False))
        if tm.stopwatch is not None:
            chips.append(("watch", self._fmt_dur(now - tm.stopwatch), "stopwatch", False))
        if not chips:
            return
        _, h = self.screen.get_size()
        font = self.face._get_font(max(14, h // 24))
        light = (243, 247, 244)
        y = 12
        for glyph, text, payload, urgent in chips:
            gs = font.get_height() - 4            # glyph box size
            tsurf = font.render(text, True, light)
            cw = gs + tsurf.get_width() + 22
            ch = font.get_height() + 10
            pill = pygame.Surface((cw, ch), pygame.SRCALPHA)
            if urgent:
                alpha = 150 + int(90 * (0.5 + 0.5 * math.sin(time.time() * 6)))
                pygame.draw.rect(pill, (190, 60, 60, alpha), pill.get_rect(),
                                 border_radius=ch // 2)
            else:
                pygame.draw.rect(pill, (10, 60, 70, 140), pill.get_rect(),
                                 border_radius=ch // 2)
            self._draw_glyph(pill, glyph, 8, (ch - gs) // 2, gs, light)
            pill.blit(tsurf, (gs + 14, 5))
            rect = pygame.Rect(12, y, cw, ch)
            self.screen.blit(pill, rect.topleft)
            self._chip_hits.append((rect, payload))
            y += ch + 6

    @staticmethod
    def _draw_glyph(surf, kind, x, y, s, color):
        if kind == "timer":              # hourglass: two triangles
            pygame.draw.polygon(surf, color,
                                [(x, y), (x + s, y), (x + s // 2, y + s // 2)])
            pygame.draw.polygon(surf, color,
                                [(x, y + s), (x + s, y + s), (x + s // 2, y + s // 2)])
        elif kind == "bell":             # dome + base + clapper
            r = s // 2
            pygame.draw.circle(surf, color, (x + r, y + r - 1), r - 1)
            pygame.draw.line(surf, color, (x, y + s - 3), (x + s, y + s - 3), 2)
            pygame.draw.circle(surf, color, (x + r, y + s - 1), 2)
        elif kind == "watch":            # stopwatch: ring + stem
            r = s // 2
            pygame.draw.circle(surf, color, (x + r, y + r + 1), r - 1, 2)
            pygame.draw.line(surf, color, (x + r, y - 1), (x + r, y + 2), 2)
        else:                            # "more": three dots
            for i in range(3):
                pygame.draw.circle(surf, color,
                                   (x + 2 + i * (s // 2 - 1), y + s // 2), 2)

    PANEL_ROWS = {
        "timer": [("+1 minute", "plus60"), ("-1 minute", "minus60"),
                  ("Cancel timer", "cancel"), ("Close", "close")],
        "alarm": [("+10 minutes", "plus600"), ("Cancel alarm", "cancel"),
                  ("Close", "close")],
        "stopwatch": [("Stop stopwatch", "stop_watch"), ("Close", "close")],
    }

    def _open_chip(self, payload):
        tm = self.timers
        if payload == "ringing":
            tm.dismiss()
            return
        if payload == "stopwatch":
            rows = self.PANEL_ROWS["stopwatch"]
            self.timer_panel = {"kind": "stopwatch", "item": None,
                                "rows": rows, "sel": 0, "rects": [],
                                "at": time.time()}
            return
        if payload == "list":
            rows = [(None, ("open", it)) for it in sorted(tm.items, key=lambda i: i["due"])]
            rows += [("Cancel all", "cancel_all"), ("Close", "close")]
            self.timer_panel = {"kind": "list", "item": None, "rows": rows,
                                "sel": 0, "rects": [], "at": time.time()}
            return
        self.timer_panel = {"kind": payload["kind"], "item": payload,
                            "rows": self.PANEL_ROWS[payload["kind"]],
                            "sel": 0, "rects": [], "at": time.time()}

    def _panel_act(self, action):
        tm = self.timers
        panel = self.timer_panel
        item = panel and panel.get("item")
        if isinstance(action, tuple) and action[0] == "open":
            self._open_chip(action[1])
            return
        if action == "plus60" and item:
            item["due"] += 60
        elif action == "minus60" and item:
            item["due"] = max(time.time() + 5, item["due"] - 60)
        elif action == "plus600" and item:
            item["due"] += 600
            d = datetime.datetime.fromtimestamp(item["due"])
            item["label"] = (d.strftime("%-I:%M %p")
                             .replace("AM", "A M").replace("PM", "P M"))
        elif action == "cancel":
            if item in tm.items:
                tm.items.remove(item)
            self.logger.log("info", f"cancelled via touch: {item['label']}")
            self.timer_panel = None
        elif action == "cancel_all":
            tm.items.clear()
            self.timer_panel = None
        elif action == "stop_watch":
            tm.stopwatch = None
            self.timer_panel = None
        elif action == "close":
            self.timer_panel = None
        if action != "close":
            tm.save()                      # touch edits must survive restarts
        if panel:
            panel["at"] = time.time()      # interaction keeps it alive

    def _draw_timer_panel(self):
        panel = self.timer_panel
        if not panel:
            return
        tm = self.timers
        item = panel.get("item")
        if time.time() - panel["at"] > 30 or (item and item not in tm.items):
            self.timer_panel = None        # timed out, fired, or cancelled
            return
        w, h = self.screen.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 130))
        self.screen.blit(shade, (0, 0))
        font = self.face._get_font(max(16, h // 20))
        if panel["kind"] == "timer":
            head_txt = f"Timer — {self._fmt_dur(item['due'] - time.time())} left"
        elif panel["kind"] == "alarm":
            rep = {"daily": " · every day", "weekdays": " · school days"}.get(
                item.get("repeat", "once"), "")
            head_txt = f"Alarm — {self._alarm_text(item)}{rep}"
        elif panel["kind"] == "stopwatch":
            head_txt = f"Stopwatch — {self._fmt_dur(time.time() - tm.stopwatch)}" \
                if tm.stopwatch else "Stopwatch"
        else:
            head_txt = "Timers and alarms"
        row_h = font.get_height() + max(8, h // 40)
        rows = panel["rows"]
        pw = int(w * 0.7)
        ph = row_h * (len(rows) + 1) + max(12, h // 30)
        px, py = (w - pw) // 2, (h - ph) // 2
        pygame.draw.rect(self.screen, (10, 60, 70),
                         pygame.Rect(px, py, pw, ph), border_radius=14)
        head = font.render(head_txt, True, (243, 247, 244))
        self.screen.blit(head, (px + (pw - head.get_width()) // 2, py + 6))
        panel["rects"] = []
        y = py + row_h
        now = time.time()
        for i, (label, action) in enumerate(rows):
            if label is None:              # live row for a list-panel item
                it = action[1]
                left = (self._fmt_dur(it["due"] - now) + " left"
                        if it["kind"] == "timer" else self._alarm_text(it))
                label = f"{it['kind'].title()} — {left}"
            r = pygame.Rect(px + 8, y, pw - 16, row_h)
            if i == panel["sel"]:
                pygame.draw.rect(self.screen, (85, 190, 178), r, border_radius=10)
            color = (10, 40, 50) if i == panel["sel"] else (235, 240, 240)
            t = font.render(label, True, color)
            self.screen.blit(t, (r.x + 10, r.y + (row_h - t.get_height()) // 2))
            panel["rects"].append(r)
            y += row_h

    # -------------------------------------------------------------- overlays
    MENU_VISIBLE = 8

    def _draw_game_menu(self):
        menu = self.game_menu
        if not menu:
            return
        if time.time() - menu["at"] > 45:      # forgotten menu melts away
            self.game_menu = None
            return
        w, h = self.screen.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 130))
        self.screen.blit(shade, (0, 0))
        font = self.face._get_font(max(16, h // 20))
        top = menu["top"]
        rows = menu["items"][top:top + self.MENU_VISIBLE]
        row_h = font.get_height() + max(8, h // 40)
        pw = int(w * 0.88)
        ph = row_h * (len(rows) + 1) + max(12, h // 30)
        px, py = (w - pw) // 2, (h - ph) // 2
        pygame.draw.rect(self.screen, (10, 60, 70),
                         pygame.Rect(px, py, pw, ph), border_radius=14)
        head = font.render("Which one? (D-pad + Start, or tap)", True,
                           (243, 247, 244))
        self.screen.blit(head, (px + (pw - head.get_width()) // 2, py + 6))
        # scroll arrows when the list continues past the window
        if top > 0:
            pygame.draw.polygon(self.screen, (243, 247, 244),
                                [(px + pw - 26, py + 18), (px + pw - 10, py + 18),
                                 (px + pw - 18, py + 8)])
        if top + self.MENU_VISIBLE < len(menu["items"]):
            pygame.draw.polygon(self.screen, (243, 247, 244),
                                [(px + pw - 26, py + ph - 18), (px + pw - 10, py + ph - 18),
                                 (px + pw - 18, py + ph - 8)])
        menu["rects"] = []
        y = py + row_h
        for vi, (label, *_rest) in enumerate(rows):
            i = top + vi
            r = pygame.Rect(px + 8, y, pw - 16, row_h)
            if i == menu["sel"]:
                pygame.draw.rect(self.screen, (85, 190, 178), r,
                                 border_radius=10)
            color = (10, 40, 50) if i == menu["sel"] else (235, 240, 240)
            avail = r.w - 20
            overflow = font.size(label)[0] - avail
            if i == menu["sel"] and overflow > 0:
                # selected row too wide: ping-pong side-scroll it (cosine so
                # it eases and pauses at each end); other rows get ellipsis
                sweep = (1 - math.cos(time.time() * 1.4)) / 2
                t = font.render(label, True, color)
                self.screen.set_clip(pygame.Rect(r.x + 10, r.y, avail, row_h))
                self.screen.blit(t, (r.x + 10 - int(overflow * sweep),
                                     r.y + (row_h - t.get_height()) // 2))
                self.screen.set_clip(None)
            else:
                text = label
                while text and font.size(text)[0] > avail:
                    text = text[:-2].rstrip() + "…"
                t = font.render(text, True, color)
                self.screen.blit(t, (r.x + 10,
                                     r.y + (row_h - t.get_height()) // 2))
            menu["rects"].append(r)
            y += row_h

    def _draw_corner_hold(self):
        """While the escape-hatch corner is held, wash the screen red from
        left to right — progress feedback a finger can't cover (a corner
        ring hides under the fingertip on a touchscreen)."""
        if self._corner_down_at is None:
            return
        frac = (time.monotonic() - self._corner_down_at) / HOLD_SECS
        if frac < 0.08:      # don't flash on ordinary taps
            return
        w, h = self.screen.get_size()
        band = pygame.Surface((int(w * min(frac, 1.0)), h), pygame.SRCALPHA)
        band.fill((190, 60, 60, 110))
        self.screen.blit(band, (0, 0))

    def _draw_mic_button(self):
        """Bottom-right touch toggle for the mic. Hidden when no mic is open."""
        if not self.ears.enabled:
            self._btn_mic = None
            return
        w, h = self.screen.get_size()
        r = max(14, h // 22)
        cx, cy = w - r - r // 2, h - r - r // 2
        self._btn_mic = pygame.Rect(cx - r, cy - r, r * 2, r * 2)
        muted = self.ears.user_muted.is_set()
        dark = (14, 46, 62)                      # face line color
        # Draw on an alpha surface and blit translucent, so subtitles that
        # run under the button stay readable. Muted is more opaque on purpose.
        surf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
        pygame.draw.circle(surf, (180, 70, 70) if muted else (66, 160, 150),
                           (r, r), r)
        pygame.draw.circle(surf, dark, (r, r), r, max(2, r // 12))
        # microphone glyph: capsule + cradle arc + stem + base
        cap = pygame.Rect(0, 0, int(r * 0.42), int(r * 0.72))
        cap.center = (r, r - int(r * 0.22))
        pygame.draw.rect(surf, dark, cap, border_radius=cap.w // 2)
        arc_box = pygame.Rect(0, 0, int(r * 0.95), int(r * 0.95))
        arc_box.center = (r, r - int(r * 0.12))
        pygame.draw.arc(surf, dark, arc_box, math.pi, 2 * math.pi,
                        max(2, r // 9))
        pygame.draw.line(surf, dark, (r, r + int(r * 0.36)),
                         (r, r + int(r * 0.52)), max(2, r // 10))
        pygame.draw.line(surf, dark, (r - int(r * 0.26), r + int(r * 0.52)),
                         (r + int(r * 0.26), r + int(r * 0.52)), max(2, r // 10))
        if muted:
            d = int(r * 0.62)
            pygame.draw.line(surf, dark, (r - d, r - d), (r + d, r + d),
                             max(3, r // 6))
        surf.set_alpha(175 if muted else 110)
        self.screen.blit(surf, self._btn_mic.topleft)

    def _draw_confirm(self):
        w, h = self.screen.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 140))
        self.screen.blit(shade, (0, 0))
        font = self.face._get_font(max(18, h // 16))
        msg = font.render("Leave BMO and go to the desktop?", True, (255, 255, 255))
        self.screen.blit(msg, ((w - msg.get_width()) // 2, int(h * 0.25)))
        self._btn_yes = pygame.Rect(int(w * 0.14), int(h * 0.5), int(w * 0.3), int(h * 0.2))
        self._btn_no = pygame.Rect(int(w * 0.56), int(h * 0.5), int(w * 0.3), int(h * 0.2))
        pygame.draw.rect(self.screen, (180, 70, 70), self._btn_yes, border_radius=18)
        pygame.draw.rect(self.screen, (70, 150, 90), self._btn_no, border_radius=18)
        for rect, label in ((self._btn_yes, "Exit"), (self._btn_no, "Stay")):
            t = font.render(label, True, (255, 255, 255))
            self.screen.blit(t, (rect.centerx - t.get_width() // 2,
                                 rect.centery - t.get_height() // 2))

    def _confirm_click(self, x, y):
        if getattr(self, "_btn_yes", None) and self._btn_yes.collidepoint(x, y):
            self.logger.log("info", "exit via escape hatch")
            self.running = False
        elif getattr(self, "_btn_no", None) and self._btn_no.collidepoint(x, y):
            self.confirm_exit = False


def acquire_lock():
    """Single instance. Returns the held file object or None if already running."""
    path = f"/tmp/bmo-{os.getuid()}.lock"
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="BMO — voice AI companion")
    ap.add_argument("--dev", action="store_true", help="windowed dev mode")
    ap.add_argument("--config", default=None, help="path to config.toml")
    args = ap.parse_args()

    lock = acquire_lock()
    if lock is None:
        print("BMO is already running.")
        sys.exit(3)

    cfg = Config(args.config, dev=args.dev)
    app = App(cfg)
    try:
        app.run()
    finally:
        if getattr(app, "music", None):
            app.music.stop()
    sys.exit(0)


if __name__ == "__main__":
    main()
