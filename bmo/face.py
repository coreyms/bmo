"""BMO's face: a parametric model drawn procedurally every frame.

There are no image assets. The face is ~8 floats (FaceParams); every named
expression is a preset of those floats and animation is easing the current
values toward the target. Lip sync drives `mouth_drive` from the audio thread.

Coordinates are authored in an 800x480 design space and scaled to the window.
"""

import math
import random
import time

import pygame
import pygame.gfxdraw

DESIGN_W, DESIGN_H = 800, 480

# BMO palette
BG = (85, 190, 178)          # body teal
DARK = (14, 46, 62)          # line/feature color
MOUTH_INSIDE = (170, 62, 78)
TONGUE = (222, 108, 122)
TEETH = (243, 247, 244)
CAPTION = (10, 60, 70)

# Parameter presets. Keys: eye (openness/size scale), pdx/pdy (gaze -1..1),
# open (mouth 0..1), curve (smile -1..1), width (mouth width scale)
EXPRESSIONS = {
    "idle":      dict(eye=1.00, pdx=0.0,  pdy=0.0,  open=0.00, curve=0.35, width=1.00),
    "listening": dict(eye=1.30, pdx=0.0,  pdy=0.0,  open=0.06, curve=0.50, width=1.05),
    "thinking":  dict(eye=0.90, pdx=0.55, pdy=-0.7, open=0.00, curve=0.10, width=0.90),
    "speaking":  dict(eye=1.05, pdx=0.0,  pdy=0.0,  open=0.00, curve=0.40, width=1.00),
    "happy":     dict(eye=1.00, pdx=0.0,  pdy=0.0,  open=0.25, curve=0.80, width=1.10),
    "surprised": dict(eye=1.55, pdx=0.0,  pdy=0.0,  open=0.55, curve=0.05, width=0.75),
    "sad":       dict(eye=0.85, pdx=0.0,  pdy=0.35, open=0.00, curve=-0.45, width=0.90),
    "sleeping":  dict(eye=0.00, pdx=0.0,  pdy=0.0,  open=0.00, curve=0.18, width=0.90),
    "gaming":    dict(eye=1.00, pdx=0.0,  pdy=0.0,  open=0.15, curve=0.65, width=1.05),
}

EYE_Y = 185
EYE_DX = 118          # eye distance from center
EYE_R = 17
MOUTH_Y = 300
MOUTH_HALF_W = 92


def _bezier(p0, p1, p2, n=26):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    return pts


class Face:
    def __init__(self, cfg):
        self.cfg = cfg
        self.params = dict(EXPRESSIONS["sleeping"])
        self.target = dict(EXPRESSIONS["sleeping"])
        self.expression = "sleeping"
        self.mouth_drive = 0.0        # set by the voice thread (0..1)
        self.speaking = False
        self._blink = 0.0             # 0 = open, 1 = fully blinked
        self._blink_at = time.monotonic() + random.uniform(2, 5)
        self._blinking = False
        self._t = 0.0                 # animation clock
        self._font = None
        self.caption_top = ""         # what BMO heard / user typed
        self.caption_bottom = ""      # what BMO is saying

    # ------------------------------------------------------------------ state
    def set_expression(self, name):
        if name in EXPRESSIONS and name != self.expression:
            self.expression = name
            self.target = dict(EXPRESSIONS[name])

    def set_mouth_drive(self, level):
        self.mouth_drive = max(0.0, min(1.0, level))

    # ----------------------------------------------------------------- update
    def update(self, dt):
        self._t += dt
        # Ease params toward target (frame-rate independent exponential).
        k = 1 - math.exp(-dt / 0.09)
        for key, tgt in self.target.items():
            self.params[key] += (tgt - self.params[key]) * k

        # Blinking (skip while sleeping — lids already closed).
        now = time.monotonic()
        if self.expression != "sleeping":
            if not self._blinking and now >= self._blink_at:
                self._blinking = True
                self._blink_t0 = now
            if self._blinking:
                # 60ms down, 90ms up
                e = now - self._blink_t0
                self._blink = min(e / 0.06, 1.0) if e < 0.06 else max(1 - (e - 0.06) / 0.09, 0.0)
                if e > 0.15:
                    self._blinking = False
                    self._blink = 0.0
                    self._blink_at = now + random.uniform(2.0, 6.0)
                    if random.random() < 0.15:   # occasional double blink
                        self._blink_at = now + 0.25
        else:
            self._blink = 0.0

    # ------------------------------------------------------------------- draw
    def draw(self, screen):
        w, h = screen.get_size()
        scale = min(w / DESIGN_W, h / DESIGN_H)
        ox = (w - DESIGN_W * scale) / 2
        oy = (h - DESIGN_H * scale) / 2

        def S(x, y):
            return (int(ox + x * scale), int(oy + y * scale))

        def SL(v):
            return max(1, int(v * scale))

        screen.fill(BG)
        p = self.params

        idle_drift = math.sin(self._t * 0.6) * 3 + math.sin(self._t * 1.7) * 1.5
        eye_open = max(0.0, p["eye"] * (1.0 - self._blink))
        pdx = p["pdx"] * 12 + (idle_drift if self.expression == "idle" else 0)
        pdy = p["pdy"] * 10

        # Eyes: solid dark dots that squash shut for blinks/sleep.
        for side in (-1, 1):
            ex, ey = 400 + side * EYE_DX + pdx, EYE_Y + pdy
            rx = EYE_R * max(min(p["eye"], 1.15), 0.9)   # closed eyes keep their width
            ry = EYE_R * eye_open
            if ry < 2.5:  # closed: a gentle arc-line
                x0, y0 = S(ex - rx, ey)
                x1, y1 = S(ex + rx, ey)
                pygame.draw.line(screen, DARK, (x0, y0), (x1, y1), SL(5))
            else:
                cx, cy = S(ex, ey)
                pygame.gfxdraw.filled_ellipse(screen, cx, cy, SL(rx), SL(ry), DARK)
                pygame.gfxdraw.aaellipse(screen, cx, cy, SL(rx), SL(ry), DARK)

        # Mouth. Openness is expression + live audio drive.
        openness = min(1.0, max(p["open"], self.mouth_drive if self.speaking else p["open"]))
        curve = p["curve"]
        half_w = MOUTH_HALF_W * p["width"] * (1.0 + openness * 0.12)
        left = (400 - half_w, MOUTH_Y)
        right = (400 + half_w, MOUTH_Y)

        if openness < 0.06:
            # Closed: a single smile/frown curve.
            ctrl = (400, MOUTH_Y + 62 * curve)
            pts = [S(*pt) for pt in _bezier(left, ctrl, right)]
            pygame.draw.lines(screen, DARK, False, pts, SL(7))
        else:
            # Open: region between an upper and lower lip curve.
            up_ctrl = (400, MOUTH_Y + 18 * curve - 46 * openness)
            lo_ctrl = (400, MOUTH_Y + 62 * curve + 108 * openness)
            upper = _bezier(left, up_ctrl, right)
            lower = _bezier(right, lo_ctrl, left)
            poly = [S(*pt) for pt in upper + lower]
            pygame.gfxdraw.filled_polygon(screen, poly, MOUTH_INSIDE if openness > 0.3 else DARK)
            if openness > 0.3:
                # Teeth: a strip hanging from the *actual* upper-lip curve
                # (sampled from it, so it can never poke outside the mouth).
                n = len(upper)
                seg = upper[n // 5: n - n // 5]
                depth = 8 + 16 * openness
                teeth = ([(x, y + 2) for x, y in seg]
                         + [(x, y + 2 + depth) for x, y in reversed(seg)])
                pygame.gfxdraw.filled_polygon(screen, [S(*p) for p in teeth], TEETH)
            if openness > 0.5:
                # Tongue: a dome rising from the lower lip — tallest in the
                # middle, tapering to nothing at the edges.
                n = len(lower)
                seg = lower[n // 3: n - n // 3]
                h = 16 + 26 * openness
                m = len(seg) - 1
                dome = [(x, y - 2 - h * math.sin(math.pi * i / m))
                        for i, (x, y) in enumerate(seg)]
                tongue = [(x, y - 2) for x, y in seg] + list(reversed(dome))
                pygame.gfxdraw.filled_polygon(screen, [S(*p) for p in tongue], TONGUE)
            pygame.gfxdraw.aapolygon(screen, poly, DARK)
            pygame.draw.lines(screen, DARK, True, poly, SL(4))

        # State decorations
        if self.expression == "sleeping":
            self._draw_zzz(screen, S, SL)
        elif self.expression == "thinking":
            self._draw_dots(screen, S, SL)
        elif self.expression == "listening":
            self._draw_ears_pulse(screen, S, SL)

        self._draw_captions(screen, w, h, scale)

    def _draw_zzz(self, screen, S, SL):
        font = self._get_font(SL(34))
        for i in range(3):
            phase = (self._t * 0.5 + i * 0.33) % 1.0
            x = 560 + i * 34 + phase * 14
            y = 150 - i * 30 - phase * 22
            surf = font.render("z", True, DARK)
            surf.set_alpha(int(230 * (1 - phase * 0.7)))
            screen.blit(surf, S(x, y))

    def _draw_dots(self, screen, S, SL):
        for i in range(3):
            pulse = (math.sin(self._t * 4 - i * 0.9) + 1) / 2
            cx, cy = S(640 + i * 42, 55)
            pygame.gfxdraw.filled_circle(screen, cx, cy, SL(6 + 5 * pulse), DARK)

    def _draw_ears_pulse(self, screen, S, SL):
        # Soft expanding rings: "I'm listening!"
        for i in range(2):
            phase = (self._t * 0.9 + i * 0.5) % 1.0
            r = SL(18 + phase * 30)
            alpha = int(120 * (1 - phase))
            ring = pygame.Surface((r * 2 + 4, r * 2 + 4), pygame.SRCALPHA)
            pygame.draw.circle(ring, (*DARK, alpha), (r + 2, r + 2), r, SL(3))
            x, y = S(736, 74)
            screen.blit(ring, (x - r - 2, y - r - 2))

    def _draw_captions(self, screen, w, h, scale):
        if not self.cfg.get("display", "show_captions", True):
            return
        font = self._get_font(max(14, int(20 * scale)))
        y = h - 8
        for text, alpha in ((self.caption_bottom, 255), (self.caption_top, 170)):
            if not text:
                continue
            surf = font.render(text[-90:], True, CAPTION)
            surf.set_alpha(alpha)
            y -= surf.get_height() + 4
            screen.blit(surf, ((w - surf.get_width()) // 2, y))

    def _get_font(self, size):
        key = ("f", size)
        if not hasattr(self, "_fonts"):
            self._fonts = {}
        if key not in self._fonts:
            try:
                self._fonts[key] = pygame.font.SysFont("dejavusans", size, bold=True)
            except Exception:
                self._fonts[key] = pygame.font.Font(None, size)
        return self._fonts[key]
