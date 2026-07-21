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

        self.work_q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="router-brain").start()
        self.brain.warm_up()
        self.logger.log("info", f"BMO started (dev={cfg.dev}, model={self.brain.model})")

    # ----------------------------------------------------------------- state
    def set_state(self, state):
        if state == self.state:
            return
        self.state = state
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
                    for sentence in self.brain.stream_sentences(res.brain_text, res.style_hint):
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
            self._draw_mic_button()
            self._draw_corner_hold()
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
            # touch interrupts BMO mid-speech: "stop, you misheard me"
            self._gen += 1        # invalidate the in-flight LLM/worker output
            self.voice.stop()
            self.brain.interrupt.set()
            self.ears.mute(False)   # don't wait on a speak_end that may be gone
            self.set_state(LISTENING)
            self.window_deadline = time.time() + self.cfg.get("wake", "window_seconds", 120)

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

    # -------------------------------------------------------------- overlays
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
