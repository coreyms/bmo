"""YouTube plugin: search -> picker -> voice/number selection."""

import pytest

from bmo.plugins.youtube import YouTubePlugin
from bmo.router import normalize

FAKE_RESULTS = [(f"Video {i}", f"https://youtu.be/v{i}", "3:1" + str(i))
                for i in range(6)]


@pytest.fixture
def yt(fake_app, monkeypatch):
    fake_app.videos = []
    fake_app._video = False
    fake_app.picked = []
    fake_app.game_running = lambda: False
    fake_app.launch_video = lambda url, s: (fake_app.videos.append(url), True)[1]
    fake_app._menu_launch = lambda idx: fake_app.picked.append(idx)
    fake_app.quit_game = lambda: True
    p = YouTubePlugin(fake_app)
    monkeypatch.setattr(p, "_search", lambda q, n=6: list(FAKE_RESULTS))
    return p


def test_watch_opens_picker_with_results(yt, fake_app):
    r = yt.handle(normalize("watch minecraft parkour"))
    assert "6 videos" in r.speech
    assert fake_app.game_menu and len(fake_app.game_menu["items"]) == 6
    assert fake_app.game_menu["items"][0][3] == "youtube"
    assert fake_app.videos == []          # nothing launched yet


def test_watch_phrasings_reach_search(yt, fake_app, monkeypatch):
    seen = []
    monkeypatch.setattr(yt, "_search",
                        lambda q, n=6: (seen.append(q), list(FAKE_RESULTS))[1])
    for phrase, want in [
            ("watch minecraft parkour", "minecraft parkour"),
            ("play the mario theme on youtube", "the mario theme"),
            ("youtube funny cat compilation", "funny cat compilation"),
            ("play the volcano video", "volcano"),
            ("hey bmo watch adventure time intro", "adventure time intro")]:
        yt.handle(normalize(phrase))
        assert seen[-1] == want, phrase


def test_voice_pick_first_last_and_number(yt, fake_app):
    yt.handle(normalize("watch minecraft"))
    yt.handle(normalize("the first one"))
    assert fake_app.picked == [0]
    yt.handle(normalize("the last one"))
    assert fake_app.picked == [0, 5]
    yt.handle(normalize("number three"))
    assert fake_app.picked == [0, 5, 2]


def test_pick_without_menu_falls_through(yt, fake_app):
    assert yt.handle(normalize("the first one")) is None
    assert fake_app.picked == []


def test_pick_out_of_range_is_friendly(yt, fake_app):
    fake_app.game_menu = {"items": [("a", "a", "u", "youtube")] * 2}
    r = yt.handle(normalize("the fifth one"))
    assert "only 2" in r.speech and fake_app.picked == []


def test_empty_search_apologizes(yt, fake_app, monkeypatch):
    monkeypatch.setattr(yt, "_search", lambda q, n=6: [])
    r = yt.handle(normalize("watch something weird"))
    assert "couldn't find" in r.speech.lower()
    assert fake_app.game_menu is None


def test_busy_screen_refuses(yt, fake_app):
    fake_app.game_running = lambda: True
    r = yt.handle(normalize("watch minecraft"))
    assert "one screen" in r.speech.lower()


def test_stop_video_only_when_video(yt, fake_app):
    fake_app._video = True
    assert yt.handle(normalize("stop the video")) is not None
    fake_app._video = False
    assert yt.handle(normalize("stop the video")) is None
