"""Smalltalk: instant answers must match exactly, and only exactly."""

import pytest

from bmo.plugins.smalltalk import SmallTalkPlugin, pick, HOW_ARE_YOU
from bmo.router import normalize

SHOULD_MATCH = [
    "how are you", "how are you doing today", "how's it going", "hows it going",
    "are you okay", "what's up", "whats up bmo", "sup", "what's up with you",
    "what's happening", "what is happening",
    "what are you doing", "what are you doing today", "whatcha doing",
    "what are you doing tomorrow", "what are you going to do tomorrow",
    "where are you going", "are you a robot", "are you real",
    "how old are you", "who made you", "can you hear me", "are you there",
    "testing testing one two three", "i'm bored", "you're funny",
    "do you want to play a game", "wanna play", "i'm back", "welcome back",
    "sorry", "my bad", "what's your favorite color", "hello there",
    "good morning", "good afternoon", "thanks bmo", "i love you bmo",
    "hey bmo, how are you",
]

SHOULD_FALL_THROUGH = [
    "hi why is the sky blue", "how are you supposed to do this",
    "what's your favorite planet", "what is happening in the world",
    "where are you going with this", "what are you doing to my code",
    "i'm bored of this game", "see you at the game",
    "welcome back to the show", "tell me about dogs",
]


@pytest.fixture
def talk(fake_app):
    return SmallTalkPlugin(fake_app)


@pytest.mark.parametrize("phrase", SHOULD_MATCH)
def test_matches(talk, phrase):
    assert talk.handle(normalize(phrase)) is not None, phrase


@pytest.mark.parametrize("phrase", SHOULD_FALL_THROUGH)
def test_falls_through_to_brain(talk, phrase):
    assert talk.handle(normalize(phrase)) is None, phrase


def test_pick_never_repeats_back_to_back():
    prev = None
    for _ in range(200):
        cur = pick(HOW_ARE_YOU)
        assert cur != prev
        prev = cur


def test_farewells_are_tailored(talk, fake_app):
    talk.handle(normalize("see you after school"))
    assert "school" in fake_app.voice.said[-1].lower()
    talk.handle(normalize("see you tomorrow"))
    assert "tomorrow" in fake_app.voice.said[-1].lower()
    talk.handle(normalize("good night"))
    last = fake_app.voice.said[-1].lower()
    assert "night" in last or "dream" in last
    # "tonight" means back later today — not bedtime
    talk.handle(normalize("see you tonight"))
    assert fake_app.voice.said[-1] == "<generic goodbye>"


def test_farewell_sleeps_bmo(talk, fake_app):
    talk.handle(normalize("bye"))
    assert fake_app.slept == 1
