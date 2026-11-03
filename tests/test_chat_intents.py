"""The chat's sentence parser: a table of what people say and what it must turn into. No network and no model: the parser is plain Python."""
import pytest

from simlab.chat import cases as C
from simlab.chat import intents as I

PARAMS = {"water": {"range_m": 150, "minutes": 60, "wave_hs": 1.0, "wind_ms": 8, "payload_kg": 0}, "ground": {"pipe_d": 0.3, "distance_m": 50, "wet": 1},
          "air": {"minutes": 10, "wind_ms": 8, "payload_kg": 0}}


def ctx(platform):
    if platform in (None, "none"):
        return I.ParseCtx()
    return I.ctx_from_book(C.CaseBook(platform, C.draft_plan(platform, PARAMS[platform], "x")))


def sig(platform, text):
    return I.parse(text, None, ctx(platform)).signature()


def E(*ops):
    return ("edit", tuple(ops))


# (platform with a plan or "none", what the person says, what the parser makes of it: Intent.signature())
TABLE = [
    # tests named with a number and a unit
    ("water", "waves of 1.2 m", E("set_ask")),
    ("water", "waves up to 0.9 m", E("set_levels")),
    ("water", "waves 90 cm", E("set_ask")),
    ("water", "add wind 12 m/s", E("add_sweep")),
    ("water", "add current 2 m/s", E("add_sweep")),
    ("water", "debris 20", E("set_ask")),
    ("water", "wind 20 mph", E("set_ask")),
    ("water", "waves 5 m", E("set_ask")),                       # not clamped here: the engine clamps and says so
    ("water", "waves 1.0 m and wind 12 m/s together", E("add_fixed")),
    ("ground", "add delay 0.5 s", E("add_sweep")),
    ("ground", "pipe width 25 cm", E("set_job_number")),
    ("ground", "distance 200 m", E("set_job_number")),
    ("ground", "wet floor", E("set_job_number")),
    ("ground", "dry floor", E("set_job_number")),
    ("air", "wind 15 m/s", E("set_ask")),
    ("air", "payload 0.5 kg", E("set_job_number")),
    ("air", "20 minutes", E("set_job_number")),
    ("water", "2 hours of endurance", E("set_job_number")),
    ("water", "distance 300 m", E("set_job_number")),
    ("water", "set the range to 400 metres", E("set_job_number")),
    # relative words
    ("water", "make the waves bigger", E("set_levels")),
    ("water", "make the waves calmer", E("set_levels")),
    ("water", "make it tougher", E("set_levels", "set_levels", "set_levels", "set_levels")),
    ("ground", "make it slipperier", E("set_levels")),
    ("ground", "make the mud worse", E("set_levels")),
    ("air", "more wind", E("set_levels")),
    # synonyms of tests, and faults
    ("ground", "add mud", E("add_sweep")),
    ("ground", "remove the lag test", E("remove")),
    ("water", "remove the debris test", E("remove")),
    ("water", "drop the wind test", E("remove")),
    ("water", "add a motor failure", E("add_fixed")),
    ("water", "what if the rudder is stuck", E("add_fixed")),
    ("air", "add a rotor failure", E("add_fixed")),
    ("ground", "add a dead motor", E("add_fixed")),
    # tries and the bar
    ("water", "10 tries", E("set_tries")),
    ("water", "set tries to 8", E("set_tries")),
    ("water", "bar 60%", E("set_bar")),
    ("water", "need 90% success", E("set_bar")),
    # what the simulator cannot do: recorded as a test it cannot run, never an error
    ("water", "add rain", E("add_untestable")),
    ("water", "go underwater", E("add_untestable")),
    ("water", "drop a parcel", E("add_untestable")),
    ("water", "add fog and gps loss", E("add_untestable", "add_untestable")),
    ("ground", "what about snow", E("add_untestable")),
    ("air", "test it in the dark", E("add_untestable")),
    # running and watching
    ("water", "test it", ("run_tests", "run_tests")),
    ("water", "run all", ("run_tests", "run_tests")),
    ("water", "run everything", ("run_tests", "run_tests")),
    ("water", "stress test it", ("run_tests", "run_tests")),
    ("water", "how tough is it", ("run_tests", "run_tests")),
    ("water", "/test waves", ("run_tests", "run_tests")),
    ("water", "watch it", ("watch", "watch")),
    ("water", "play the worst case", ("watch", "watch")),
    ("water", "watch the weakest", ("watch", "watch")),
    ("water", "simulate wind 15 m/s", ("watch", "watch")),
    ("water", "show me the boat in waves of 0.9 m", ("watch", "watch")),
    ("ground", "watch it on a slippery floor", ("watch", "watch")),
    ("water", "/watch", ("watch", "watch")),
    ("none", "watch it", ("watch", "watch")),                     # with no plan the engine answers 'no job yet'
    # parts
    ("water", "build sheet", ("sheet", "sheet")),
    ("water", "parts list", ("sheet", "sheet")),
    ("water", "what do i need to buy", ("sheet", "sheet")),
    ("water", "/sheet", ("sheet", "sheet")),
    # hardware and training only point somewhere
    ("water", "hardware", ("hardware", "answer")),
    ("water", "drive it", ("hardware", "answer")),
    ("water", "esp32 wiring", ("hardware", "answer")),
    ("water", "train a detector", ("train", "answer")),
    # questions
    ("water", "why did it fail", ("question", "answer")),
    ("water", "what does this mean", ("question", "answer")),
    ("water", "what is a try", ("question", "answer")),
    ("water", "what didn't you test", ("question", "answer")),
    ("water", "what can you not test", ("question", "answer")),
    ("water", "how long will it take", ("question", "answer")),
    ("water", "can it handle a storm", ("question", "answer")),
    ("water", "where are we", ("question", "answer")),
    # undo, redo, stop, commands
    ("water", "undo", ("undo", "undo")),
    ("water", "redo", ("redo", "redo")),
    ("water", "/undo", ("undo", "undo")),
    ("water", "stop", ("stop", "stop")),
    ("water", "/stop", ("stop", "stop")),
    ("water", "/new a drone for a field", ("new_chat", "new_chat")),
    ("water", "/help", ("help", "answer")),
    ("water", "hello", ("help", "answer")),
    # a new job: with no plan it starts one, with a plan of another kind it asks
    ("none", "A boat that finds a person in a flooded street", ("new_job", "new_job")),
    ("none", "a rover for a 30 cm pipe", ("new_job", "new_job")),
    ("none", "a drone over a field for 10 minutes", ("new_job", "new_job")),
    ("none", "something for a lake", ("new_job", "new_job")),
    ("none", "build a rocket", ("new_job", "new_job")),
    ("water", "a rover for a 30 cm pipe", ("confirm_new", "new_chat")),
    ("water", "a drone to watch a field for 20 minutes", ("confirm_new", "new_chat")),
    ("ground", "a boat to find a swimmer in the river", ("confirm_new", "new_chat")),
    ("water", "new job", ("confirm_new", "new_chat")),
    # an edit sentence with no job yet is not a job
    ("none", "make the waves bigger", ("blocked", "answer")),
    ("none", "undo", ("undo", "undo")),
    # ambiguous: no guessing, value chips
    ("water", "make it last longer", ("ambiguous", None)),
    ("air", "make it last longer", ("ambiguous", None)),
    # nonsense: did you mean
    ("water", "asdf", ("unclear", None)),
    ("water", "purple monkey dishwasher", ("unclear", None)),
    ("water", "thruster out", ("unclear", None)),                  # the test exists already: offers to run it
]


@pytest.mark.parametrize("platform,text,expected", TABLE, ids=[f"{i:03d}-{t[:28]}" for i, (_, t, _) in enumerate(TABLE)])
def test_sentence_table(platform, text, expected):
    assert sig(platform, text) == expected


def test_the_table_is_big_enough():
    assert len(TABLE) >= 60 and len({t for _, t, _ in TABLE}) >= 60


# ------------------------------------------------------------------ the details behind the table
def ops(platform, text):
    it = I.parse(text, None, ctx(platform))
    assert it.kind == "edit", (text, it.kind)
    return it.ops


def test_units_are_converted_to_the_tests_own_unit():
    assert ops("water", "waves 90 cm")[0]["ask"] == pytest.approx(0.9)
    assert ops("water", "wind 20 mph")[0]["ask"] == pytest.approx(8.9408)
    assert ops("water", "wind 36 km/h")[0]["ask"] == pytest.approx(10.0)
    assert ops("water", "2 hours of endurance")[0] == {"op": "set_job_number", "name": "minutes", "value": 120.0}
    assert ops("water", "3 minutes of endurance")[0]["value"] == 3.0
    assert ops("water", "bar 40%")[0] == {"op": "set_bar", "case": None, "value": 0.4}
    assert ops("ground", "pipe width 25 cm")[0] == {"op": "set_job_number", "name": "pipe_d", "value": 0.25}
    assert ops("air", "payload 500 g")[0]["value"] == pytest.approx(0.5)


def test_numbers_pair_with_the_test_they_belong_to():
    o = ops("water", "waves 1.0 m and wind 12 m/s together")[0]
    assert o == {"op": "add_fixed", "conditions": {"wave_hs": 1.0, "wind_ms": 12.0}}
    # a speed is not a wave height: the number goes to the wind
    assert ops("water", "add 12 m/s")[0] == {"op": "add_sweep", "hazard": "wind", "levels": [12.0]}


def test_relative_words_move_the_top_level_one_step():
    c = ctx("water")
    case = next(x for x in c.cases if x["id"] == "tc_waves")
    top = case["sweep"]["levels"][-1]
    up = ops("water", "make the waves bigger")[0]
    assert up["op"] == "set_levels" and up["levels"][-1] > top
    down = ops("water", "make the waves calmer")[0]
    assert down["levels"][-1] < top and len(down["levels"]) == len(case["sweep"]["levels"]) - 1
    # descending tests (grip: lower is worse): slipperier means a lower top level
    g = next(x for x in ctx("ground").cases if x["id"] == "tc_grip")
    worse = ops("ground", "make it slipperier")[0]["levels"]
    assert worse[-1] < g["sweep"]["levels"][-1]


def test_a_clamp_is_the_engines_job_and_is_said_in_words():
    book = C.CaseBook("water", C.draft_plan("water", PARAMS["water"], "x"))
    it = I.parse("waves 5 m", None, I.ctx_from_book(book))
    out = book.apply(it.ops)
    assert out.clamps and "1.5 m" in out.clamps[0] and "maximum" in out.clamps[0]
    assert book.for_hazard("waves")["sweep"]["ask"] == 1.5


def test_unsupported_words_say_why():
    it = I.parse("add rain", None, ctx("water"))
    assert it.ops[0]["op"] == "add_untestable" and "not simulated" in it.ops[0]["reason"]
    assert it.meta["unsupported"] == ["rain"]
    # "no rain" is the person saying they do not want it
    assert I.parse("no rain please", None, ctx("water")).kind != "edit"


def test_a_first_message_states_the_jobs_numbers():
    assert I.extract_job("A boat that finds a person 150 m away in 1 m waves", "water") == {"range_m": 150.0, "wave_hs": 1.0}
    assert I.extract_job("a rover for a 30 cm sewer pipe, 80 m long, wet", "ground") == {"pipe_d": 0.3, "distance_m": 80.0, "wet": 1.0}
    assert I.extract_job("a drone over a field for 10 minutes", "air") == {"minutes": 10.0}


def test_actions_win_over_text_and_hardware_actions_are_refused():
    it = I.parse("hello", {"type": "run_tests", "args": {"cases": ["tc_waves"]}}, ctx("water"))
    assert it.kind == "run_tests" and it.source == "action" and it.action["args"]["cases"] == ["tc_waves"]
    for bad in ("arm", "drive", "checklist", "hw", "estop", "flash", "connect"):
        r = I.parse(None, {"type": bad, "args": {}}, ctx("water"))
        assert r.kind == "blocked" and r.topic == "hardware"
    assert I.parse(None, {"type": "launch_missiles"}, ctx("water")).kind == "blocked"
    assert I.parse(None, {"nope": 1}, ctx("water")).kind == "unclear"


def test_structured_edit_actions_become_case_operations():
    it = I.parse(None, {"type": "edit_test", "args": {"case": "tc_waves", "levels": [0, 0.5, 1], "tries": 6}}, ctx("water"))
    assert [o["op"] for o in it.ops] == ["set_levels", "set_tries"]
    it = I.parse(None, {"type": "add_test", "args": {"conditions": {"wave_hs": 0.5, "wind_ms": 10}}}, ctx("water"))
    assert it.ops[0]["op"] == "add_fixed"
    assert I.parse(None, {"type": "set_job_number", "args": {"name": "range_m", "value": 200}}, ctx("water")).ops[0]["value"] == 200


def test_did_you_mean_offers_real_actions_and_ambiguous_asks_offer_values():
    it = I.parse("purple monkey dishwasher", None, ctx("water"))
    assert 1 <= len(it.chips) <= 3 and all(c["action"]["type"] in I.ACTION_TYPES for c in it.chips)
    amb = I.parse("make it last longer", None, ctx("water"))
    assert amb.kind == "ambiguous" and len(amb.chips) >= 2
    assert all(c["action"]["type"] == "set_job_number" and c["action"]["args"]["name"] == "minutes" for c in amb.chips)
    values = [c["action"]["args"]["value"] for c in amb.chips]
    assert values == sorted(values) and values[0] > 60


def test_a_long_description_is_a_job_even_when_it_contains_a_verb():
    it = I.parse("a drone to watch a field for 20 minutes", None, I.ParseCtx())
    assert it.kind == "new_job" and it.meta["goal"] == "air_search"
