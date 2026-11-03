"""How many free AI requests are left today: attempts counted (failed ones too), the key's own figure read at most once a minute, zero switches the AI off."""
import calendar
import json

import pytest

from simlab.chat import budget as budget_mod
from simlab.chat import words
from simlab.chat.budget import Budget, DAILY_LIMIT, LOW, ai_setting, utc_day
from simlab.llm import OpenRouter
from simlab.llm import client as llm_client
from tests.chat_helpers import KEY, isolate, save_key
from tests.fake_openrouter import FakeOpenRouter


class Clock:
    def __init__(self, t=1_793_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Usage:
    """A client factory that counts how often the key's usage is read."""

    def __init__(self, left=47, limit=50, error=None):
        self.calls, self.left, self.limit, self.error = 0, left, limit, error

    def __call__(self):
        outer = self

        class C:
            def usage(self):
                outer.calls += 1
                if outer.error:
                    raise RuntimeError(outer.error)
                return {"free_requests_left": outer.left, "free_requests_limit": outer.limit}
        return C()


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    save_key(tmp_path)
    return tmp_path


def make(tmp_path, usage=None, clock=None, mono=None):
    return Budget(tmp_path / "chats", client_factory=usage, clock=clock or Clock(), mono=mono or Clock(1000.0))


# ------------------------------------------------------------------ attempts
def test_attempts_are_counted_per_utc_day_and_roll_over(rig):
    clock = Clock()
    b = make(rig, clock=clock)
    assert b.used_today() == 0
    for _ in range(3):
        b.record_attempt("m")
    assert b.used_today() == 3
    clock.t += 3600 * 30                                           # a day and a bit later
    assert utc_day(clock()) != json.loads(b.path.read_text())["day"] and b.used_today() == 0
    b.record_attempt()
    assert b.used_today() == 1 and json.loads(b.path.read_text())["day"] == utc_day(clock())


def test_the_day_changes_at_midnight_utc():
    midnight = float(calendar.timegm((2026, 11, 1, 0, 0, 0)))
    assert utc_day(midnight - 1) == "2026-10-31" and utc_day(midnight) == "2026-11-01" and utc_day(midnight + 86399) == "2026-11-01"


def test_every_request_the_client_sends_counts_even_when_it_fails(rig):
    fake = FakeOpenRouter()
    b = make(rig)
    b.install()
    try:
        fake.script += [{"status": 500, "error": "boom"}] * 8
        c = OpenRouter(key=KEY, base_url=fake.url, sleep=lambda s: None)
        with pytest.raises(Exception):
            c.chat_with_fallback([{"role": "user", "content": "hi"}], c.choose_models("nvidia/nemotron-3-ultra-550b-a55b:free"), retries=1)
        assert len(fake.chat_requests()) >= 2 and b.used_today() == len(fake.chat_requests())
        b.uninstall()
        n = b.used_today()
        fake.script.append({"chunks": ["ok"]})
        c.chat_with_fallback([{"role": "user", "content": "hi"}], c.choose_models("nvidia/nemotron-3-ultra-550b-a55b:free"))
        assert b.used_today() == n                                  # uninstalled: no longer counted
        assert b._hook not in llm_client.ATTEMPT_HOOKS
    finally:
        b.uninstall()
        fake.stop()


def test_a_broken_counter_never_stops_a_request(rig):
    fake = FakeOpenRouter()
    llm_client.ATTEMPT_HOOKS.append(lambda model: 1 / 0)
    try:
        fake.script.append({"chunks": ["fine"]})
        c = OpenRouter(key=KEY, base_url=fake.url, sleep=lambda s: None)
        assert c.chat_with_fallback([{"role": "user", "content": "hi"}], c.choose_models("nvidia/nemotron-3-ultra-550b-a55b:free"))["text"] == "fine"
    finally:
        llm_client.ATTEMPT_HOOKS.pop()
        fake.stop()


# ------------------------------------------------------------------ the key's own figure, cached
def test_the_key_is_read_at_most_once_a_minute(rig):
    usage, mono = Usage(left=40), Clock(1000.0)
    b = make(rig, usage, mono=mono)
    for _ in range(5):
        b.snapshot()
    assert usage.calls == 1
    mono.t += 59
    b.snapshot()
    assert usage.calls == 1
    mono.t += 2
    b.snapshot()
    assert usage.calls == 2
    b.snapshot(force=True)
    assert usage.calls == 3


def test_the_figure_is_adjusted_by_the_attempts_made_since_it_was_read(rig):
    usage = Usage(left=20)
    b = make(rig, usage)
    s = b.snapshot()
    assert s["left"] == 20 and s["configured"] and s["enabled"] and s["approx"] is True and s["low"] is False and s["limit"] == 50
    for _ in range(4):
        b.record_attempt()
    assert b.snapshot()["left"] == 16 and usage.calls == 1
    for _ in range(8):
        b.record_attempt()
    s = b.snapshot()
    assert s["left"] == 8 and s["low"] is True and LOW == 10 and s["enabled"] is True      # under ten: still on, the page asks first


def test_zero_left_switches_the_ai_off_with_the_reset_time(rig):
    b = make(rig, Usage(left=0))
    s = b.snapshot()
    assert s["left"] == 0 and s["exhausted"] is True and s["enabled"] is False and s["reason"] == words.RESETS == "Resets at 00:00 UTC"
    b2 = make(rig, Usage(left=3))
    for _ in range(5):
        b2.record_attempt()
    s2 = b2.snapshot(force=True)                                    # the key's figure was read after those five attempts, so it already includes them
    assert s2["left"] == 3 and s2["low"] is True and s2["enabled"] is True
    b2.record_attempt()
    assert b2.snapshot()["left"] == 2


def test_the_api_saying_the_day_is_spent_is_trusted_until_the_day_changes(rig):
    b = make(rig, Usage(left=30))
    assert b.snapshot()["enabled"]
    b.mark_exhausted()
    assert b.snapshot()["enabled"] is False and b.snapshot()["exhausted"] is True


def test_without_the_key_figure_the_local_count_is_used(rig):
    b = make(rig, Usage(error="no network"))
    for _ in range(7):
        b.record_attempt()
    s = b.snapshot()
    assert s["left"] == DAILY_LIMIT - 7 and s["error"] and s["enabled"] is True
    no_factory = make(rig / "other", None)                          # a different chats folder: its own count, and no way to ask the key
    assert no_factory.snapshot()["left"] == DAILY_LIMIT


def test_no_key_means_the_ai_is_off_and_nothing_is_asked(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    usage = Usage()
    s = make(tmp_path, usage).snapshot()
    assert s["configured"] is False and s["enabled"] is False and s["left"] is None and s["reason"] == "no key saved" and usage.calls == 0 and s["model"] is None


def test_an_error_text_never_contains_the_key(rig):
    b = make(rig, Usage(error=f"401 for {KEY}"))
    s = b.snapshot()
    assert s["error"] and KEY not in json.dumps(s) and "***" in s["error"]


# ------------------------------------------------------------------ the setting
def test_the_chat_setting_can_switch_the_planner_off(rig, monkeypatch):
    b = make(rig, Usage())
    assert ai_setting() == "auto" and b.snapshot()["enabled"]
    cfg = budget_mod.setting_path()
    cfg.write_text(json.dumps({"ai": "never"}))
    s = b.snapshot()
    assert ai_setting() == "never" and s["enabled"] is False and s["setting"] == "never" and "switched off" in s["reason"]
    cfg.write_text(json.dumps({"ai": "ask"}))
    assert ai_setting() == "auto"                                   # an unknown value falls back: the person chose automatic in this version
    cfg.write_text("not json")
    assert ai_setting() == "auto"
    cfg.unlink()
    assert ai_setting() == "auto"
