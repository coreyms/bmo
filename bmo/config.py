"""Load config.toml and resolve paths. One Config object is passed everywhere."""

import glob
import os
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    def __init__(self, path=None, dev=False):
        path = path or os.path.join(ROOT, "config.toml")
        with open(path, "rb") as f:
            self.raw = tomllib.load(f)
        self.dev = dev
        self.root = ROOT

    def __getitem__(self, section):
        return self.raw.get(section, {})

    def get(self, section, key, default=None):
        return self.raw.get(section, {}).get(key, default)

    def path(self, rel):
        """Resolve a config path relative to the project root."""
        if not rel:
            return rel
        return rel if os.path.isabs(rel) else os.path.join(self.root, rel)

    def find_core(self, kind):
        """Return the configured libretro core path, or auto-detect it."""
        configured = self.get("games", f"core_{kind}", "")
        if configured:
            return self.path(configured)
        names = {"nes": "nestopia_libretro.so", "snes": "snes9x_libretro.so",
                 "genesis": "genesis_plus_gx_libretro.so",
                 "sms": "genesis_plus_gx_libretro.so",
                 "gamegear": "genesis_plus_gx_libretro.so",
                 "gb": "gambatte_libretro.so",
                 "gbc": "gambatte_libretro.so"}
        hits = glob.glob(f"/usr/lib/*/libretro/{names[kind]}")
        return hits[0] if hits else None


def app_dirs(cfg):
    """Ensure writable runtime dirs exist (they may be fresh after a clone)."""
    for d in ("logs", "var"):
        os.makedirs(os.path.join(cfg.root, d), exist_ok=True)
