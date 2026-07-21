"""The safety tiers and the caption mask."""

from bmo.plugins.safety import check, mask
from bmo.router import normalize


def test_tiers():
    assert check("i want to hurt myself") == "crisis"
    assert check("what the fuck") == "profanity"
    assert check("tell me about beer") == "grownup"
    assert check("tell me a joke") is None


def test_whole_word_only_no_scunthorpe():
    # innocent words containing bad substrings must pass
    for ok in ("classic assassin", "peacock", "pussycat", "drugstore",
               "sextuplets", "cocktail sausages"):
        assert check(ok) is None, ok


def test_typed_uppercase_is_caught():
    # normalize lowercases, so SHIFT-key swearing can't bypass the filter
    assert check(normalize("SHIT")) == "profanity"


def test_mask_blanks_swears_only():
    assert mask("shit go to sleep") == "**** go to sleep"
    assert mask("SHIT go to sleep") == "**** go to sleep"
    assert mask("what the fuck is that") == "what the **** is that"
    assert mask("go to sleep") == "go to sleep"
    assert mask("classic assassin peacock") == "classic assassin peacock"
