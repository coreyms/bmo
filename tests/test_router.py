"""normalize() is the front door: every utterance passes through it."""

from bmo.router import normalize


def test_strips_wake_prefix():
    assert normalize("BMO, set a timer") == "set a timer"
    assert normalize("hey beemo what's up") == "what's up"
    assert normalize("b m o play mario") == "play mario"


def test_lowercases_everything():
    # safety filters and intent words compare lowercase — uppercase
    # typed input must not sneak past them
    assert normalize("PLAY Super MARIO") == "play super mario"


def test_keeps_apostrophes_drops_punctuation():
    assert normalize("what's it like outside?!") == "what's it like outside"
    assert normalize("stop...") == "stop"


def test_collapses_whitespace():
    assert normalize("  set   a   timer  ") == "set a timer"


def test_bare_wake_word_becomes_empty():
    # router.route() turns "" into a greeting; normalize just reports it
    assert normalize("BMO!") == ""
