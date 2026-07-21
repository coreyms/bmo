"""Shared test helpers: a fake App with just enough surface for plugins.

Real BMO wires plugins to pygame, Vosk, and Piper. Tests don't want any of
that — they want to check the thinking, not the talking. So the fake app
records what BMO *would* have said or launched, and tests assert on that.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def fake_app(tmp_path):
    class Cfg:
        root = str(tmp_path)

        def path(self, p):
            full = os.path.join(self.root, p)
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            return full

        def get(self, section, key, default=None):
            return default

        def find_core(self, kind):
            return "/fake/core.so"

    class Logger:
        def __init__(self):
            self.entries = []

        def log(self, kind, text, **meta):
            self.entries.append((kind, text))

    class Voice:
        def __init__(self):
            self.said = []

        def say(self, s):
            self.said.append(s)

    class App:
        def __init__(self):
            self.cfg = Cfg()
            self.logger = Logger()
            self.voice = Voice()
            self.game_menu = None
            self.launched = []
            self.slept = 0

        def launch_game(self, core, rom, title):
            self.launched.append(title)
            return True

        def announce(self, text):
            self.voice.say(text)

        def request_sleep(self, say_bye=False):
            self.slept += 1
            if say_bye:
                self.voice.say("<generic goodbye>")

    return App()
