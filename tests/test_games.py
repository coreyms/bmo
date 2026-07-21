"""Title cleaning, pretty display, and the launch/menu decision tree."""

import pytest

from bmo.plugins.games import GamesPlugin, clean_title, display_title
from bmo.router import normalize

LIBRARY = [
    ("the legend of zelda - a link to the past", "/r/snes/zelda-lttp.sfc", "snes"),
    ("the legend of zelda - oracle of ages", "/r/gbc/zelda-ooa.gbc", "gbc"),
    ("the legend of zelda - link's awakening", "/r/gb/zelda-la.gb", "gb"),
    ("super mario world", "/r/snes/smw.sfc", "snes"),
    ("mario paint", "/r/snes/mario-paint.sfc", "snes"),
    ("tetris", "/r/gb/tetris.gb", "gb"),
    ("tetris", "/r/nes/tetris.nes", "nes"),
]


@pytest.fixture
def games(fake_app, monkeypatch):
    p = GamesPlugin(fake_app)
    monkeypatch.setattr(p, "library", lambda: list(LIBRARY))
    return p


# ------------------------------------------------------------- clean_title
def test_clean_title_flips_trailing_article():
    assert (clean_title("Legend of Zelda, The - A Link to the Past (USA).sfc")
            == "the legend of zelda - a link to the past")


def test_clean_title_strips_junk_and_hyphens():
    assert clean_title("Super Mario World (USA) [!].sfc") == "super mario world"
    assert clean_title("Spider-Man (USA).gen") == "spider man"


def test_display_title_fixes_case():
    assert display_title("link's awakening dx") == "Link's Awakening Dx"
    assert display_title("final fantasy iii") == "Final Fantasy III"
    assert display_title("mega man x") == "Mega Man X"


# ---------------------------------------------------------- launch decisions
def _play(games, phrase):
    return games.handle(normalize(phrase))


def test_exact_title_launches_immediately(games, fake_app):
    _play(games, "play super mario world")
    assert fake_app.launched == ["Super Mario World"]
    assert fake_app.game_menu is None


def test_ambiguous_query_opens_menu_not_a_guess(games, fake_app):
    # the original sin: "play legend of zelda" silently launched Oracle
    # of Ages; it must ask instead
    r = _play(games, "play legend of zelda")
    assert fake_app.launched == []
    assert fake_app.game_menu and len(fake_app.game_menu["items"]) == 3
    assert "3 games" in r.speech


def test_system_suffix_narrows_to_one(games, fake_app):
    _play(games, "play zelda on super nintendo")
    assert fake_app.launched == ["The Legend Of Zelda - A Link To The Past"]


def test_same_title_two_systems_asks(games, fake_app):
    _play(games, "play tetris")
    assert fake_app.launched == []
    assert len(fake_app.game_menu["items"]) == 2


def test_fuzzy_match_catches_mishearing(games, fake_app):
    _play(games, "play super marco world")
    assert fake_app.launched == ["Super Mario World"]


def test_near_miss_asks_did_you_mean(games, fake_app):
    # ~0.46 similar to a Zelda title: worth a "did you mean", never a launch
    r = _play(games, "play the sound of silence")
    assert r is not None and "did you mean" in r.speech.lower()
    assert fake_app.launched == [] and fake_app.game_menu is None


def test_nonsense_falls_through_to_brain(games, fake_app):
    assert _play(games, "play purple monkey dishwasher blues") is None
    assert fake_app.launched == [] and fake_app.game_menu is None


def test_word_containment_is_whole_word(games):
    # "mario" must not match inside "marionette"-style titles
    hits = games._hits("mario", games.library())
    assert hits == ["mario paint", "super mario world"]
