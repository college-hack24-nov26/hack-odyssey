"""OpenRouter settings and client, against a local fake server (no internet, no real key)."""
import threading

import pytest

from simlab.llm import LLMError, OpenRouter, client as llm_client, settings
from tests.fake_openrouter import FakeOpenRouter

KEY = "sk-or-v1-" + "a1b2c3d4" * 6


@pytest.fixture()
def fake():
    f = FakeOpenRouter()
    yield f
    f.stop()


@pytest.fixture(autouse=True)
def private_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMLAB_LLM", str(tmp_path / "llm.json"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.setattr(llm_client, "GATED", {})                         # what was refused earlier in the process must not leak between tests


def client(fake, key=KEY, sleeps=None):
    return OpenRouter(key=key, base_url=fake.url, sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


# ------------------------------------------------------------------ settings
def test_the_key_is_stored_privately_and_never_comes_back(tmp_path):
    assert settings.public()["configured"] is False
    settings.save(api_key=f"  {KEY}  ")
    pub = settings.public()
    assert pub["configured"] and pub["source"] == "file" and KEY not in str(pub)
    assert settings.get_key() == KEY and (tmp_path / "llm.json").is_file()
    assert settings.scrub(f"error for {KEY} here") == "error for *** here"
    settings.save(api_key="")                                            # removing it
    assert settings.get_key() is None


@pytest.mark.parametrize("bad", ["   ", "short", "has space inside the key xxxxxxxxxxxxxxxxxxxx"])
def test_a_pasted_key_that_cannot_be_right_is_refused_with_a_reason(bad):
    with pytest.raises(ValueError):
        settings.save(api_key=bad)


def test_environment_key_is_used_and_the_file_wins(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-" + KEY)
    assert settings.get_key() == "env-" + KEY and settings.public()["source"] == "environment"
    settings.save(api_key=KEY)
    assert settings.get_key() == KEY and settings.public()["source"] == "file"


def test_model_choice_is_validated_and_defaults():
    assert settings.get_model() == settings.DEFAULT_MODEL and settings.DEFAULT_MODEL.endswith(":free")
    settings.save(model="google/gemma-4-31b-it:free")
    assert settings.get_model() == "google/gemma-4-31b-it:free"
    with pytest.raises(ValueError):
        settings.save(model="two words")
    settings.save(model="")
    assert settings.get_model() == settings.DEFAULT_MODEL


# ------------------------------------------------------------------ information
def test_free_models_usage_and_the_fallback_chain(fake):
    c = client(fake)
    ids = [m["id"] for m in c.free_models()]
    assert "some/paid-model" not in ids and "thinkingmachines/inkling:free" in ids
    u = c.usage()
    assert u["free_requests_left"] == 47 and u["free_requests_limit"] == 50
    assert c.choose_models("thinkingmachines/inkling:free")[0] == "thinkingmachines/inkling:free"
    fake.models = [m for m in fake.models if m["id"] != "thinkingmachines/inkling:free"]
    c2 = client(fake)
    assert c2.choose_models("thinkingmachines/inkling:free")[0] == settings.FALLBACKS[0]   # the chosen one is gone: next best


def test_no_key_is_a_clear_error_not_a_crash(fake):
    with pytest.raises(LLMError) as e:
        OpenRouter(base_url=fake.url).models()
    assert e.value.kind == "no_key" and "settings" in str(e.value)


# ------------------------------------------------------------------ chat
def test_a_streamed_answer_arrives_in_pieces_and_carries_reasoning_apart(fake):
    fake.script.append({"reasoning": ["thinking ", "hard"], "chunks": ['{"a":', ' 1}'], "model": "thinkingmachines/inkling:free"})
    seen = []
    r = client(fake).chat([{"role": "user", "content": "hi"}], "thinkingmachines/inkling:free", on_delta=lambda k, p: seen.append((k, p)))
    assert r["text"] == '{"a": 1}' and r["reasoning"] == "thinking hard" and r["usage"]["completion_tokens"] == 50
    assert [k for k, _ in seen] == ["reasoning", "reasoning", "text", "text"]
    req = fake.chat_requests()[0]
    assert req["body"]["stream"] is True and req["auth"] == f"Bearer {KEY}" and req["body"]["reasoning"] == {"effort": "medium"}


def test_structured_output_is_asked_for_only_where_the_model_supports_it(fake):
    schema = {"type": "object", "properties": {"x": {"type": "number"}}}
    c = client(fake)
    c.chat([{"role": "user", "content": "q"}], "thinkingmachines/inkling-small:free", json_schema=schema)
    c.chat([{"role": "user", "content": "q"}], "thinkingmachines/inkling:free", json_schema=schema)
    c.chat([{"role": "user", "content": "q"}], "unknown/model:free", json_schema=schema)
    a, b, d = (r["body"] for r in fake.chat_requests())
    assert a["response_format"]["type"] == "json_schema" and a["provider"] == {"require_parameters": True}
    assert b["response_format"] == {"type": "json_object"} and "provider" not in b
    assert "response_format" not in d


def test_a_busy_model_is_retried_after_the_wait_it_asks_for(fake):
    fake.script += [{"status": 429, "error": "rate limited", "headers": {"Retry-After": "3"}}, {"chunks": ["fine"]}]
    sleeps = []
    r = client(fake, sleeps=sleeps).chat([{"role": "user", "content": "q"}], "thinkingmachines/inkling:free")
    assert r["text"] == "fine" and sleeps == [3.0] and len(fake.chat_requests()) == 2


def test_a_spent_daily_quota_stops_at_once_without_burning_more_requests(fake):
    fake.script += [{"status": 429, "error": "Rate limit exceeded: free-models-per-day"}, {"chunks": ["never"]}]
    with pytest.raises(LLMError) as e:
        client(fake).chat([{"role": "user", "content": "q"}], "thinkingmachines/inkling:free")
    assert e.value.kind == "daily_limit" and len(fake.chat_requests()) == 1


def test_a_rejected_key_is_reported_as_such_and_never_echoes_the_key(fake):
    fake.script.append({"status": 401, "error": f"invalid key {KEY}"})
    with pytest.raises(LLMError) as e:
        client(fake).chat([{"role": "user", "content": "q"}], "thinkingmachines/inkling:free")
    assert e.value.kind == "auth" and KEY not in str(e.value)


def test_a_model_limited_to_approved_apps_is_skipped_and_remembered_not_mistaken_for_a_bad_key(fake):
    gate = "thinkingmachines/inkling:free is only available on agentic harnesses. Try plugging it into a coding agent or productivity app"
    inkling, nxt = "thinkingmachines/inkling:free", settings.FALLBACKS[0]
    fake.script += [{"status": 403, "error": gate}, {"chunks": ["second"], "model": nxt}]
    r = client(fake).chat_with_fallback([{"role": "user", "content": "q"}], [inkling, nxt])
    assert r["text"] == "second" and [x["body"]["model"] for x in fake.chat_requests()] == [inkling, nxt]
    assert llm_client.gated_models() == [inkling]
    assert client(fake).choose_models(inkling)[0] == nxt                 # remembered: the next plan does not spend one of the 50 daily requests on it
    llm_client.GATED[inkling] -= llm_client.GATED_S + 1                  # hours later it is tried again, in case OpenRouter opened it up
    assert client(fake).choose_models(inkling)[0] == inkling
    fake.script.append({"status": 403, "error": gate})
    with pytest.raises(LLMError) as e:                                   # alone, the gate is reported as what it is
        client(fake).chat([{"role": "user", "content": "q"}], inkling)
    assert e.value.kind == "model_unavailable" and e.value.gated and "approved apps" in str(e.value)


def test_a_403_that_names_the_key_is_an_auth_error_and_a_moderation_403_lets_another_model_try(fake):
    fake.script.append({"status": 403, "error": "Invalid API key"})
    with pytest.raises(LLMError) as e:
        client(fake).chat([{"role": "user", "content": "q"}], "google/gemma-4-31b-it:free")
    assert e.value.kind == "auth" and not e.value.gated
    fake.script.append({"status": 403, "error": "Your input was flagged by moderation"})
    with pytest.raises(LLMError) as e2:
        client(fake).chat([{"role": "user", "content": "q"}], "google/gemma-4-31b-it:free")
    assert e2.value.kind == "model_unavailable" and not e2.value.gated and not llm_client.gated_models()


def test_error_text_from_the_server_is_scrubbed_of_the_saved_key(fake):
    settings.save(api_key=KEY)
    fake.script.append({"status": 400, "error": f"bad request for {KEY}"})
    with pytest.raises(LLMError) as e:
        OpenRouter(base_url=fake.url).chat([{"role": "user", "content": "q"}], "thinkingmachines/inkling:free")
    assert KEY not in str(e.value) and "***" in str(e.value)


def test_fallback_moves_to_the_next_model_only_for_errors_another_model_can_fix(fake):
    fake.script += [{"status": 404, "error": "No endpoints found for this model"}, {"chunks": ["second"], "model": "thinkingmachines/inkling-small:free"}]
    r = client(fake).chat_with_fallback([{"role": "user", "content": "q"}], ["thinkingmachines/inkling:free", "thinkingmachines/inkling-small:free"])
    assert r["text"] == "second" and [x["body"]["model"] for x in fake.chat_requests()] == ["thinkingmachines/inkling:free", "thinkingmachines/inkling-small:free"]
    fake.script += [{"status": 401, "error": "no"}, {"chunks": ["never"]}]
    with pytest.raises(LLMError) as e:
        client(fake).chat_with_fallback([{"role": "user", "content": "q"}], ["thinkingmachines/inkling:free", "thinkingmachines/inkling-small:free"])
    assert e.value.kind == "auth" and len(fake.chat_requests()) == 3


def test_an_error_in_the_middle_of_a_stream_and_an_empty_answer_are_reported(fake):
    fake.script.append({"chunks": ["partial"], "stream_error": "upstream crashed"})
    with pytest.raises(LLMError) as e:
        client(fake).chat([{"role": "user", "content": "q"}], "thinkingmachines/inkling:free", retries=0)
    assert e.value.kind == "unavailable"
    fake.script.append({"chunks": [], "reasoning": ["only thoughts"]})
    with pytest.raises(LLMError) as e2:
        client(fake).chat([{"role": "user", "content": "q"}], "thinkingmachines/inkling:free")
    assert e2.value.kind == "bad_response" and "no text" in str(e2.value)


def test_a_stream_can_be_cancelled_between_pieces(fake):
    fake.script.append({"chunks": [str(i) for i in range(50)], "delay": 0.02})
    stop = threading.Event()
    got = []

    def delta(kind, piece):
        got.append(piece)
        if len(got) == 3:
            stop.set()
    with pytest.raises(LLMError) as e:
        client(fake).chat([{"role": "user", "content": "q"}], "thinkingmachines/inkling:free", on_delta=delta, cancel=stop)
    assert e.value.kind == "cancelled" and len(got) < 50


def test_an_unreachable_server_is_a_network_error():
    c = OpenRouter(key=KEY, base_url="http://127.0.0.1:9", sleep=lambda s: None)
    with pytest.raises(LLMError) as e:
        c.chat([{"role": "user", "content": "q"}], "thinkingmachines/inkling:free", retries=0, timeout=(1, 1))
    assert e.value.kind == "network"
