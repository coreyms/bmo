"""Pure DSP and text helpers from the voice and brain layers."""

import numpy as np

from bmo.brain import clean_for_speech, SENTENCE_END
from bmo.voice import compute_envelope
from bmo.main import App


def test_envelope_silence_is_closed_mouth():
    silence = np.zeros(16000, dtype=np.int16)
    env = compute_envelope(silence, 16000)
    assert float(env.max()) == 0.0


def test_envelope_loud_opens_mouth_and_normalizes():
    t = np.linspace(0, 1, 16000)
    loud = (np.sin(2 * np.pi * 220 * t) * 20000).astype(np.int16)
    env = compute_envelope(loud, 16000)
    assert 0.9 <= float(env.max()) <= 1.0
    assert len(env) == 16000 // (16000 * 20 // 1000)   # one value per 20ms


def test_envelope_attack_is_faster_than_release():
    burst = np.concatenate([np.zeros(8000), np.ones(4000) * 20000,
                            np.zeros(8000)]).astype(np.int16)
    env = compute_envelope(burst, 16000)
    onset = int(np.flatnonzero(env > 0.05)[0])
    opened = int(np.flatnonzero(env > 0.6)[0])
    last_high = int(np.flatnonzero(env > 0.6)[-1])
    after = env[last_high:]
    closed = int(np.flatnonzero(after < 0.2)[0])
    # frames to open (40ms attack) < frames to close (110ms release)
    assert (opened - onset) < closed


def test_clean_for_speech_strips_markup_and_emoji():
    assert clean_for_speech("**hello** `world` 🎮") == "hello world"
    assert clean_for_speech("a  b\n\nc") == "a b c"


def test_sentence_end_finds_boundaries():
    m = SENTENCE_END.search("Hi there! And more")
    assert m and m.end() == 10


def test_fmt_dur():
    assert App._fmt_dur(0) == "0:00"
    assert App._fmt_dur(272) == "4:32"
    assert App._fmt_dur(3725) == "1:02:05"
    assert App._fmt_dur(-3) == "0:00"


def test_alarm_text_display():
    assert App._alarm_text({"label": "7:30 P M"}) == "7:30 PM"


# ------------------------------------------------------------- mood tags
def test_mood_tag_extraction():
    from bmo.brain import take_mood_tag
    assert take_mood_tag("[happy] Hi there!") == ("happy", "Hi there!", True)
    assert take_mood_tag("[SAD] oh no.") == ("sad", "oh no.", True)
    assert take_mood_tag("[plain] Okay.") == (None, "Okay.", True)   # plain = no override
    # tag still streaming in: hold, don't emit
    assert take_mood_tag("[surpr") == (None, "[surpr", False)
    assert take_mood_tag("") == (None, "", False)
    # no tag at all: resolve and move on
    assert take_mood_tag("Hello friend, how are") == (None, "Hello friend, how are", True)


def test_stray_mood_tags_never_spoken():
    assert clean_for_speech("[happy] Hi!") == "Hi!"
    assert clean_for_speech("So [sad] anyway") == "So anyway"


def test_result_expression_field():
    from bmo.router import Result
    assert Result(speech="x").expression is None
    assert Result(speech="x", expression="happy").expression == "happy"


def test_guess_mood_fallback():
    from bmo.brain import guess_mood
    assert guess_mood("Oh sweetie, I'm so sorry about your dog.") == "sad"
    assert guess_mood("Whoa, no way!") == "surprised"
    assert guess_mood("Congratulations, that's awesome!") == "happy"
    assert guess_mood("Two plus two is four.") is None
