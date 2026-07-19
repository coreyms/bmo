"""Intent router: deterministic fast-path commands before the LLM.

Plugins register (pattern, handler) pairs. The first plugin that returns a
Result wins; unmatched text streams to the brain. Handlers get the App so
they can launch games, set timers, change state, etc.
"""

import importlib
import pkgutil
import re
from dataclasses import dataclass


@dataclass
class Result:
    speech: str = None        # say this directly
    brain_text: str = None    # send this to the LLM instead
    style_hint: str = None    # steering hint passed to the brain


class Plugin:
    """Base class. Subclasses set `name`, `priority` (lower = first) and
    register intents in __init__ via self.add(pattern, handler)."""
    name = "plugin"
    priority = 50

    def __init__(self, app):
        self.app = app
        self.intents = []

    def add(self, pattern, handler):
        self.intents.append((re.compile(pattern, re.I), handler))

    def handle(self, text):
        for rx, handler in self.intents:
            m = rx.search(text)
            if m:
                return handler(m, text)
        return None


WAKE_PREFIX = re.compile(r"^(hey\s+)?(beemo|bee mo|be mo|b m o|bmo)[,!.\s]*", re.I)


def normalize(text):
    t = text.strip()
    t = WAKE_PREFIX.sub("", t)              # "BMO, set a timer" -> "set a timer"
    t = re.sub(r"[^\w\s':]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class Router:
    def __init__(self, app):
        self.app = app
        self.plugins = []
        self._discover()

    def _discover(self):
        from bmo import plugins as pkg
        for info in pkgutil.iter_modules(pkg.__path__):
            mod = importlib.import_module(f"bmo.plugins.{info.name}")
            for attr in vars(mod).values():
                if (isinstance(attr, type) and issubclass(attr, Plugin)
                        and attr is not Plugin):
                    self.plugins.append(attr(self.app))
        self.plugins.sort(key=lambda p: p.priority)

    def route(self, raw_text):
        """Returns a Result. Falls through to the brain when nothing matches."""
        text = normalize(raw_text)
        if not text:
            return Result()
        for plugin in self.plugins:
            try:
                res = plugin.handle(text)
            except Exception as e:
                if self.app.logger:
                    self.app.logger.log("error", f"plugin {plugin.name}: {e}")
                continue
            if res is not None:
                return res
        return Result(brain_text=text)
