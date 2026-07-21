"""Clock parsing, recurrence, persistence — the school-alarm trust suite."""

import datetime
import os
import re
import time

from bmo.plugins.timers import (TimersPlugin, parse_clock, parse_number,
                                REPEAT_RX, _next_due_ts)


# ------------------------------------------------------------ parse_number
def test_parse_number_digits_and_words():
    assert parse_number("5") == 5
    assert parse_number("five") == 5
    assert parse_number("twenty five") == 25
    assert parse_number("an hour's worth") is None
    assert parse_number("a") == 1


# ------------------------------------------------------------- parse_clock
def test_vosk_homophones():
    # Vosk hears "2:45" as "to forty five" — the original bug
    assert parse_clock("to forty five") == (2, 45, False)
    assert parse_clock("to forty five p m") == (14, 45, True)
    assert parse_clock("to thirty eight") == (2, 38, False)
    assert parse_clock("for thirty") == (4, 30, False)
    assert parse_clock("won fifteen") == (1, 15, False)


def test_word_minutes_combine():
    assert parse_clock("seven fifteen") == (7, 15, False)
    assert parse_clock("seven oh five a m") == (7, 5, True)
    assert parse_clock("eleven forty five p m") == (23, 45, True)


def test_digits_and_separators():
    assert parse_clock("2 45 pm") == (14, 45, True)
    assert parse_clock("2:45") == (2, 45, False)
    assert parse_clock("7.30 a m") == (7, 30, True)


def test_twelve_oclock_rules():
    assert parse_clock("12 30 a m") == (0, 30, True)     # half past midnight
    assert parse_clock("12 p m") == (12, 0, True)
    assert parse_clock("noon") == (12, 0, True)
    assert parse_clock("midnight") == (0, 0, True)


def test_garbage_is_rejected():
    assert parse_clock("banana o'clock") is None
    assert parse_clock("99 99") is None


# --------------------------------------------------------------- recurrence
def test_repeat_phrases():
    cases = [("7 30 a m every school day", "weekdays"),
             ("7 every weekday", "weekdays"),
             ("8 daily", "daily"),
             ("seven thirty every day", "daily"),
             ("seven thirty every morning", "daily"),
             ("7 30", None)]
    for spec, want in cases:
        m = REPEAT_RX.search(spec)
        got = None if not m else ("daily" if m.group("daily") else "weekdays")
        assert got == want, spec


def test_weekday_alarm_skips_weekend():
    fri = datetime.datetime(2026, 7, 24, 7, 0)           # a Friday
    nxt = datetime.datetime.fromtimestamp(
        _next_due_ts(fri.timestamp(), "weekdays", fri.timestamp() + 60))
    assert nxt.weekday() == 0 and nxt.hour == 7          # Monday 7:00
    daily = datetime.datetime.fromtimestamp(
        _next_due_ts(fri.timestamp(), "daily", fri.timestamp() + 60))
    assert daily.weekday() == 5                          # Saturday


# ------------------------------------------------- plugin behavior (fake app)
def _set(plugin, phrase):
    return plugin.handle(phrase)


def test_alarm_via_grammar_and_persistence(fake_app):
    p = TimersPlugin(fake_app)
    r = _set(p, "set an alarm for to forty five p m every school day")
    assert "2:45 P M every school day" in r.speech
    assert os.path.exists(fake_app.cfg.path("var/alarms.json"))
    # a fresh plugin (= BMO restart) reloads the same alarm
    p2 = TimersPlugin(fake_app)
    assert len(p2.items) == 1 and p2.items[0]["repeat"] == "weekdays"


def test_missed_oneshot_is_announced_not_dropped(fake_app):
    p = TimersPlugin(fake_app)
    p.items.append({"due": time.time() - 9000, "label": "7:00 A M",
                    "kind": "alarm", "repeat": "once"})
    p.save()
    p2 = TimersPlugin(fake_app)
    assert p2.items == [] and p2._missed == ["7:00 A M"]
    p2.tick()
    assert any("missed" in s for s in fake_app.voice.said)


def test_late_alarm_still_fires(fake_app):
    p = TimersPlugin(fake_app)
    p.items.append({"due": time.time() - 600, "label": "7:00 A M",
                    "kind": "alarm", "repeat": "once"})   # 10 min late
    p.save()
    p2 = TimersPlugin(fake_app)
    assert len(p2.items) == 1        # kept — first tick will ring it


def test_alarm_cap_is_three(fake_app):
    p = TimersPlugin(fake_app)
    for i in range(3):
        p.items.append({"due": time.time() + 9999 + i, "label": "x",
                        "kind": "alarm", "repeat": "once"})
    r = _set(p, "set an alarm for 9 p m")
    assert "3 alarms" in r.speech


def test_cancel_is_kind_targeted(fake_app):
    p = TimersPlugin(fake_app)
    p.items.append({"due": time.time() + 60, "label": "1 minute",
                    "kind": "timer", "repeat": "once"})
    p.items.append({"due": time.time() + 9999, "label": "7:00 A M",
                    "kind": "alarm", "repeat": "once"})
    r = _set(p, "cancel my timer")
    assert "1 timer" in r.speech
    kinds = {i["kind"] for i in p.items}
    assert kinds == {"alarm"}        # the school alarm survived


def test_recurring_alarm_reschedules_on_fire(fake_app):
    p = TimersPlugin(fake_app)
    p.items.append({"due": time.time() - 1, "label": "7:00 A M",
                    "kind": "alarm", "repeat": "daily"})
    p.tick()
    assert p.ringing and p.ringing["kind"] == "alarm"
    assert len(p.items) == 1 and p.items[0]["due"] > time.time()


def test_alarm_verb_is_optional(fake_app):
    # Vosk mishears "set an" -> "certain"; the time is the anchor
    p = TimersPlugin(fake_app)
    r = _set(p, "certain alarm for to thirty p m")
    assert r is not None and "2:30 P M" in r.speech


def test_wake_me_up_at(fake_app):
    p = TimersPlugin(fake_app)
    r = _set(p, "wake me up at seven a m")
    assert r is not None and "7:00 A M" in r.speech


def test_alarm_questions_fall_through(fake_app):
    p = TimersPlugin(fake_app)
    assert _set(p, "do we have an alarm for tomorrow") is None
    assert _set(p, "what's the alarm for") is None
    assert len([i for i in p.items if i["kind"] == "alarm"]) == 0
