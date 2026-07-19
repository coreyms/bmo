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
from bmo.router import Router

SLEEPING, LISTENING, THINKING, SPEAKING, GAMING = (
    "sleeping", "listening", "thinking", "speaking", "gaming")

STATE_FACE = {SLEEPING: "sleeping", LISTENING: "listening",
              THINKING: "thinking", SPEAKING: "speaking", GAMING: "gaming"}

GREETINGS = ["Yes? I'm listening!", "Hi! What's up?", "BMO is here!",
             "Ooh hi! What are we doing?", "Ready for adventure!"]

CORNER = 90          # px hot corner (top-right) for the parent escape hatch
HOLD_SECS = 5.0


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
            self.voice.say("Okay! See you later!")
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
        # A new request preempts whatever BMO is saying or thinking about.
        if self.voice.busy() or self._worker_busy:
            self.brain.interrupt.set()
            self.voice.stop()
        try:
            while True:
                self.work_q.get_nowait()
        except queue.Empty:
            pass
        self.logger.log("user", text)
        self.face.caption_top = f"You: {text}"
        self.set_state(THINKING)
        self.work_q.put(text)

    _worker_busy = False

    def _worker(self):
        while True:
            text = self.work_q.get()
            self._worker_busy = True
            try:
                try:
                    res = self.router.route(text)
                except Exception as e:
                    self.logger.log("error", f"router: {e}")
                    continue
                if res.speech:
                    self.logger.log("bmo", res.speech)
                    self.voice.say(res.speech)
                elif res.brain_text:
                    reply = []
                    for sentence in self.brain.stream_sentences(res.brain_text, res.style_hint):
                        self.voice.say(sentence)
                        reply.append(sentence)
                    if reply:
                        self.logger.log("bmo", " ".join(reply), model=self.brain.model)
                else:
                    # handled silently (stop/sleep) — return to listening if awake
                    self.events.put(("idle", None))
            finally:
                self._worker_busy = False
                self.events.put(("done", None))

    def announce(self, text):
        """Out-of-band speech (timer/alarm firing) — even during games."""
        self.logger.log("bmo", text, kind2="announce")
        self.voice.say(text)

    # ----------------------------------------------------------------- games
    def launch_game(self, core, rom, title):
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
        if x > w - CORNER and y < CORNER:
            self._corner_down_at = now
            return
        if self.state == SLEEPING:
            self.wake(greet=True)
        elif self.voice.busy():
            # touch interrupts BMO mid-speech
            self.voice.stop()
            self.brain.interrupt.set()
            self.set_state(LISTENING)
            self.window_deadline = time.time() + self.cfg.get("wake", "window_seconds", 120)

    def _check_corner_hold(self):
        if self._corner_down_at is None:
            return
        held = pygame.mouse.get_pressed()[0] or self._finger_down()
        if not held:
            self._corner_down_at = None
            return
        if time.monotonic() - self._corner_down_at >= HOLD_SECS:
            self._corner_down_at = None
            self.confirm_exit = True

    def _finger_down(self):
        return bool(pygame.mouse.get_pressed()[0])

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
                self.face.caption_top = payload
            elif kind == "speak_start":
                if self.state not in (GAMING, SLEEPING):
                    self.set_state(SPEAKING)
                self.ears.mute(True)
                self.face.caption_bottom = f"BMO: {payload}"
            elif kind == "speak_end":
                self.ears.mute(False)
                # Only leave SPEAKING when the whole reply is finished —
                # between-sentence gaps must not flap the state/expression.
                if (self.state == SPEAKING and not self._worker_busy
                        and not self.voice.busy()):
                    self.set_state(LISTENING)
                    self.window_deadline = time.time() + self.cfg.get(
                        "wake", "window_seconds", 120)
            elif kind == "done":
                if self.state in (SPEAKING, THINKING) and not self.voice.busy():
                    self.set_state(LISTENING)
                    self.window_deadline = time.time() + self.cfg.get(
                        "wake", "window_seconds", 120)
            elif kind == "idle":
                if self.state == THINKING:
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
