"""From what the person typed or tapped to something the engine can do. No language model, ever: the same words give the same answer, offline.

Layers, in order (the first that understands wins; each says how sure it is):
  0. structured actions {type, args}: every chip and button. No parsing.
  1. /commands: /watch /test /sheet /undo /redo /new /stop /help, the same payloads as the actions.
  2. the edit grammar, once a plan exists: names of tests come from `menu.load(platform)` (ids, titles, a synonym table), numbers from one number-and-unit
     regex, relative words (bigger, calmer) move a level step or scale by 1.5 / 0.67. Values are NOT clamped here: the operations carry what was asked and
     `cases.CaseBook` clamps them to the simulator's ranges and says so in words.
  3. questions with a known topic (why did it fail, what does this mean, how long, what did you not test). These come before the verbs because
     "what does the test mean" contains a verb.
  4. verbs: watch, test, sheet, undo, hardware, training. Hardware only ever points to the Workbench link.
  5. a new job (no plan yet, or the user says so), or, when a plan exists and the sentence is clearly about another kind of machine, a question
     "start a new chat for this?" instead of silently replacing the job.
  6. otherwise "did you mean ..." chips (token overlap), and value chips for ambiguous asks like "make it last longer".
Unsupported words (rain, fog, GPS, underwater, dropping a parcel ...) become a test the simulator cannot run, with the reason: an answer, not an error.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from ..goals.match import match
from ..llm import menu
from . import cases as C
from . import words

ACTION_TYPES = ("new_job", "edit", "add_test", "edit_test", "remove_test", "set_job_number", "set_task_param", "set_tries", "set_bar", "watch", "run_tests",
                "sheet", "undo", "redo", "pick_design", "stop", "use_ai", "keep_mine", "use_ai_keep_edits", "answer", "open_details", "new_chat", "use_kit",
                "cancel_queued", "keep_here")
# never accepted, whatever the client sends: the chat has no way to touch hardware
FORBIDDEN_ACTIONS = ("arm", "disarm", "drive", "checklist", "hw", "connect", "disconnect", "flash", "estop", "setup", "calibrate")


@dataclass
class ParseCtx:
    """What the parser may know about the chat. `platform` is None until a job exists."""
    platform: str | None = None
    cases: list[dict] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    has_results: bool = False
    pending_ai: bool = False

    @property
    def has_plan(self) -> bool:
        return self.platform is not None


@dataclass
class Intent:
    kind: str                         # edit | watch | run_tests | sheet | undo | redo | stop | question | hardware | train | help | new_job | confirm_new | new_chat ...
    #                                   plus 'ambiguous' / 'unclear' (chips) and 'blocked' (a refusal)
    source: str                       # action | command | grammar | question | verb | new_job | fallback
    confidence: float = 1.0
    action: dict | None = None        # {type, args}: what the engine executes
    ops: list[dict] = field(default_factory=list)
    read_as: str = ""
    chips: list[dict] = field(default_factory=list)
    topic: str | None = None
    issues: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "source": self.source, "confidence": round(self.confidence, 2), "read_as": self.read_as,
                "action": self.action, "ops": self.ops, "topic": self.topic, "issues": self.issues}

    def signature(self) -> tuple:
        """What the sentence table compares: kind plus the operation names (or the action type)."""
        if self.kind == "edit":
            return ("edit", tuple(op["op"] for op in self.ops))
        return (self.kind, (self.action or {}).get("type"))


# ---------------------------------------------------------------- text and numbers
WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15,
             "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100}
UNIT_RE = (r"%|percent|per cent|m/s|mps|km/h|kph|kmh|mph|knots?|kts?|kn|metres?|meters?|m|centimet(?:re|er)s?|cm|millimet(?:re|er)s?|mm|kilomet(?:re|er)s?|km|"
           r"minutes?|mins?|hours?|hrs?|hr|h|seconds?|secs?|sec|s|milliseconds?|ms|kilograms?|kilos?|kg|grams?|g|x")
NUM_RE = re.compile(rf"(?<![\w.])(?P<n>\d+(?:\.\d+)?|\.\d+)\s*(?P<u>{UNIT_RE})?(?![a-z0-9])", re.I)


def normalise(text: str) -> str:
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"\bhalf an hour\b", "30 minutes", t)
    t = re.sub(r"\b(?:an?|one)\s+(hour|minute)\b", r"1 \1", t)
    t = re.sub(r"\b(" + "|".join(WORD_NUMS) + r")\b(?=\s*(?:tries|times|runs|minutes?|mins?|hours?|metres?|meters?|m\b|cm\b|kg\b|percent|%))",
               lambda m: str(WORD_NUMS[m.group(1)]), t)
    return re.sub(r"\s+", " ", t).strip()


def unit_family(u: str | None) -> tuple[str, float]:
    """(family, factor to the family's base: m, m/s, s, kg, share)."""
    if not u:
        return "plain", 1.0
    u = u.lower()
    if u in ("%", "percent", "per cent"):
        return "share", 0.01
    if u in ("m/s", "mps"):
        return "speed", 1.0
    if u in ("km/h", "kph", "kmh"):
        return "speed", 1 / 3.6
    if u == "mph":
        return "speed", 0.44704
    if u.startswith("knot") or u in ("kt", "kts", "kn"):
        return "speed", 0.514444
    if u.startswith("kilomet") or u == "km":
        return "length", 1000.0
    if u.startswith("centimet") or u == "cm":
        return "length", 0.01
    if u.startswith("millimet") or u == "mm":
        return "length", 0.001
    if u.startswith("met") or u == "m":
        return "length", 1.0
    if u.startswith("milli") or u == "ms":
        return "time", 0.001
    if u.startswith("min"):
        return "time", 60.0
    if u.startswith("hour") or u in ("hr", "hrs", "h"):
        return "time", 3600.0
    if u.startswith("sec") or u == "s":
        return "time", 1.0
    if u.startswith("kilo") or u == "kg":
        return "mass", 1.0
    if u.startswith("gram") or u == "g":
        return "mass", 0.001
    if u == "x":
        return "mult", 1.0
    return "plain", 1.0


@dataclass
class Num:
    start: int
    end: int
    n: float
    family: str
    factor: float
    unit: str | None
    used: bool = False

    @property
    def si(self) -> float:
        return self.n * self.factor


def find_numbers(text: str) -> list[Num]:
    out = []
    for m in NUM_RE.finditer(text):
        fam, f = unit_family(m.group("u"))
        out.append(Num(m.start(), m.end(), float(m.group("n")), fam, f, m.group("u")))
    return out


def blank(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


# ---------------------------------------------------------------- vocabulary
# phrase -> candidate ids in order; the first one the platform's menu has wins. Ids and titles from menu.load(platform) are added on top.
HAZARD_WORDS = [
    (r"waves?|swell|surf|chop|choppy|choppier|wave height|rough water|rougher water", ["waves"]),
    (r"winds?|gusts?|gusty|breeze|windy|windier|headwind", ["wind"]),
    (r"currents?|tides?|tidal|stream|river flow|flow", ["current"]),
    (r"debris|obstacles?|rocks?|pilings?|wreckage|logs?|branches", ["debris", "resistance"]),
    (r"mud|muddy|muddier|sludge|sticky|clogged|clog|resistance|drag", ["resistance"]),
    (r"slippery|slipperier|slippy|slimy|wet|wetness|ice|icy|grip|traction|slime|greasy|oily", ["grip"]),
    (r"lag|laggy|laggier|delay|delays|latency|slow commands|slow link|slow wi-?fi|command delay", ["lag"]),
]
FAULT_WORDS = [
    (r"motor (?:out|fail\w*|dead|dies|stops?|failure)|(?:a |one )?motor fail\w*|engine (?:out|fail\w*)|(?:dead|broken|failed|failing) (?:left |right )?motors?", ["thruster_out", "rotor_out", "left_motor_dead"]),
    (r"thrusters?(?: out| fail\w*| dead| failure)?", ["thruster_out"]),
    (r"rotors? (?:out|fail\w*|dead|stops?)|rotor", ["rotor_out"]),
    (r"weak motors?|worn motors?|weak battery|tired motors?", ["weak_motors"]),
    (r"rudder(?: stuck| jam\w*)?|steering (?:jam\w*|stuck)|jammed rudder", ["rudder_stuck"]),
    (r"storm|stormy|everything at once|all at once", ["wind_and_waves"]),
    (r"lost commands?|dropped commands?|command dropouts?|dropouts?|lost packets?|link drops?|signal loss|radio loss|commands? (?:lost|dropped)", ["lost_commands"]),
]
# expected unit family per hazard, to pair numbers with the right test and to refuse a speed for a wave height
EXPECT = {"waves": ("length", "plain"), "wind": ("speed", "plain"), "current": ("speed", "plain"), "grip": ("plain", "share"), "resistance": ("share", "plain"),
          "lag": ("time", "plain"), "debris": ("plain",)}
UNIT_DEFAULT = {"speed": "wind", "length": "waves", "time": "lag"}                # a number whose unit alone points to one test, when nothing else names it
# job numbers that go with a hazard word, for "design for 12 m/s wind"
HAZARD_JOB = {"waves": "wave_hs", "wind": "wind_ms"}

RE_REMOVE = re.compile(r"\b(remove|drop|delete|skip|without|get rid of|take out|cancel|forget|no more|stop testing|don'?t test|do not test|leave out|ignore|no)\b")
RE_ADD = re.compile(r"\b(add|also|include|plus|as well|throw in|try|what if|what about|test with|test for|extra|another|additionally|insert)\b")
RE_SET = re.compile(r"\b(set|make|change|use|put|switch|adjust|update|should be|to be)\b")
RE_UPTO = re.compile(r"\b(up to|upto|max(?:imum)?|top(?: level)?|no more than|at most|as high as|until|worst case of)\b")
RE_LEVELS = re.compile(r"\b(levels?|steps?)\b")
RE_COMBO = re.compile(r"\b(together|combined?|combination|at the same time|same time|simultaneously|both|all at once)\b")
RE_DESIGN = re.compile(r"\b(design for|built? for|must handle|needs? to handle|has to handle|have to handle|should handle|worst day|the job)\b")
REL_UP = re.compile(r"\b(bigger|larger|longer|further|farther|more|worse|stronger|rougher|tougher|harder|heavier|increase|raise|greater|stormier|windier|choppier|slipperier|"
                    r"muddier|nastier|extreme|crank up|turn up|ramp up|steeper)\b")
REL_DOWN = re.compile(r"\b(calmer|smaller|less|weaker|milder|gentler|easier|decrease|reduce|lighter|softer|drier|tone down|turn down|back off|dial down|slower)\b")
REL_NUM_UP = re.compile(r"\b(higher|up)\b")
REL_NUM_DOWN = re.compile(r"\b(lower|down)\b")
RE_TRIES = [re.compile(r"(?<![\w.])(\d+)\s*(?:tries|try|times|runs|repeats|repetitions|attempts|episodes)\b(?:\s*(?:per level|each|per condition))?"),
            re.compile(r"\b(?:tries|runs|repeats|repetitions|attempts)\s*(?:per level|each|per condition)?\s*(?:to|=|of|:|at)?\s*(\d+)\b"),
            re.compile(r"\beach (?:level |condition |test )?(\d+) times\b")]
RE_BAR = [re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|percent)?\s*(?:success rate|pass rate|success|bar|of (?:the )?tries|passing|pass)\b"),
          re.compile(r"\b(?:bar|threshold|success rate|pass rate|required|require|need|needs)\s*(?:to be|to|of|at|is|=|:|should be)?\s*(\d+(?:\.\d+)?)\s*(%|percent)?")]

UNSUPPORTED = [
    (r"(?:heavy |light )?(?:rain(?:y|ing)?|downpour|drizzle|monsoon)", "rain"),
    (r"fog|foggy|mist|haze|smoke", "fog"),
    (r"snow|snowy|snowfall|blizzard", "snow"),
    (r"hail", "hail"),
    (r"underwater|submerge\w*|submarine|deep sea|diving", "underwater"),
    (r"lightning|thunder|short circuit|electrical fault", "lightning"),
    (r"gps|gnss|satellites?|compass", "gps"),
    (r"temperatures?|freezing|frost|cold|hot weather|heat wave|overheat\w*", "temperature"),
    (r"darkness|nighttime|night|no light|in the dark", "dark"),
    (r"dust|dusty|sand|sandstorm", "dust"),
    (r"salt ?water|salt|corrosion|rust", "salt"),
    (r"drop(?:ping)? (?:a |the |some )?(?:parcel|package|payload|load|supplies|bomb|cargo)|deliver\w*|parcel|package delivery|airdrop|release (?:the )?payload", "payload_drop"),
    (r"follow(?:ing)? (?:a |the )?(?:person|swimmer|moving)|moving (?:person|target|swimmer)|chase|track(?:ing)? (?:a |the )?(?:person|target)", "follow"),
]
NEGATION = re.compile(r"(?:\bwithout|\bno|\bnot|\bignore|\bskip|\bforget|\bexcept|\bavoid)\s+(?:\w+\s+){0,2}$")

JOB_CUES = {
    "pipe_d": re.compile(r"pipe|pipes|tunnel|culvert|drain|diameter|wide|width|across|bore|narrow"),
    "distance_m": re.compile(r"distance|far|long|length|travel|\bgo\b|goes|crawl|run|inspect|deep|reach|stretch|block|metres? in|meters? in"),
    "range_m": re.compile(r"away|\bout\b|offshore|range|reach|distance|far|from (?:the )?(?:shore|bank|coast)|metres out|meters out"),
    "wave_hs": re.compile(r"waves?|swell|surf|chop"),
}
# "make it last longer" and the like: a job number named by a word but no value: answered with value chips, never a guess
JOB_REL = [(r"last|stay|endurance|run time|runtime|flight time|battery|time out|time up|duration", "minutes"),
           (r"further|farther|longer distance|more distance|go far|reach further|reach farther", None),
           (r"carry|payload|heavier load|more load", "payload_kg"),
           (r"pipe|diameter|tunnel", "pipe_d")]

RE_SHEET = re.compile(r"\b(build sheet|parts list|shopping list|bill of materials|bom|what to buy|what do i (?:need to )?buy|buy|wiring|wire it|how (?:do i|to) wire|pin table|schematic|sheet|parts)\b")
RE_HARDWARE = re.compile(r"\b(hardware|esp32|arduino|firmware|flash it|real rover|real machine|real robot|drive it|drive the rover|arm it|connect (?:to )?(?:the )?(?:rover|robot)|serial port|upload (?:it|the firmware))\b")
RE_TRAIN = re.compile(r"\b(train|training|learn|detector|reinforcement|neural|fine-?tune)\b")
RE_TEST = re.compile(r"\b(test|tests|testing|stress(?:[- ]?test)?|break it|how tough|how strong|how robust|run (?:all|everything|them|the tests?|it)|try everything|benchmark|check everything|run)\b")
RE_WATCH = re.compile(r"\b(watch|show|play|simulate|animate|demo|demonstrate|replay|see it|look at|run it live|run a demo)\b")
STRONG_TEST = re.compile(r"\b(how tough|how strong|how robust|stress|break it|run all|run everything|check everything)\b")
RE_WATCH_FIRST = re.compile(r"^(?:please |can you |could you |let'?s |i want to |i'?d like to |go ahead and )?(?:watch|show|play|simulate|demo|demonstrate|replay|animate)\b")
RE_WORST = re.compile(r"\b(worst|roughest|toughest|hardest|extreme|most severe|harshest|severe)\b")
RE_WEAKEST = re.compile(r"\b(weakest|weak spot|where it fails|failing one|the one that failed)\b")
RE_UNDO = re.compile(r"^(?:please )?(?:undo|take that back|revert|go back|put it back|oops)(?: (?:that|it|the last (?:one|edit|change)))?[.!]*$")
RE_REDO = re.compile(r"^(?:please )?(?:redo|do it again|bring it back)(?: that)?[.!]*$")
RE_STOP = re.compile(r"^(?:please )?(?:stop|cancel|abort|halt)(?: (?:it|everything|all|the tests?|the run|the simulation|the ai|the plan))?[.!]*$")
RE_NEW = re.compile(r"\b(new job|new chat|start over|start again|different job|another job|reset)\b")
RE_CHITCHAT = re.compile(r"^(?:hi|hello|hey|thanks|thank you|ok|okay|cool|great|nice|yes|no)[.!? ]*$")
RE_HELP = re.compile(r"\b(help|what can you do|how do i use|commands)\b")

QUESTIONS = [
    ("limits", re.compile(r"what (?:can|do|did|will) (?:you|it|we|the simulator) not (?:test|simulate|cover)|what (?:didn'?t|did not|can'?t|cannot|don'?t|do not) (?:you |it |we |the simulator )?(?:test|simulate|do|cover)|what is(?:n'?t| not) tested|not tested|limitations?|what are the limits|what can'?t you|what are you not testing")),
    ("why_fail", re.compile(r"\bwhy\b.*\b(fail\w*|break\w*|crash\w*|sink|sank|capsiz\w*|flip\w*|tip\w*|stall\w*|stuck|stop\w*|struggl\w*|lose|lost|miss\w*|below)\b|what (?:went wrong|failed|broke|happened)|what'?s (?:wrong|the weakest)|where does it (?:fail|break)|which (?:test |one )?(?:failed|fails|is weakest)")),
    ("what_means", re.compile(r"what does .* mean|\bexplain\b|how do i read|what is (?:a |the )?(?:try|tries|bar|level|autopilot|result|results)\b|what are (?:the )?tries|meaning of|what do (?:these|the) (?:numbers|results|cells) mean")),
    ("cost", re.compile(r"how long (?:will|does|would)|how long is|how much time|how many (?:tries|tests|conditions|runs)|what will (?:it|this) cost|how long.*\b(?:take|run)\b")),
    ("overview", re.compile(r"where are we|\bstatus\b|\bsummary\b|what do i have|what have we got|what now|what next|what can i do|\boverview\b|\brecap\b")),
    ("can_it", re.compile(r"^(?:can|will|would|could|does|is|are|do) (?:it|this|that|the (?:boat|rover|drone|machine|design|robot))\b")),
]


# ---------------------------------------------------------------- vocabulary for one platform
def _vocab(platform: str) -> list[tuple[re.Pattern, str, str]]:
    """[(compiled phrase, 'hazard' | 'fault', id)] for the platform: the synonym tables resolved against its menu, plus every id and title."""
    m = menu.load(platform)
    out = []
    for table, kind, have in ((HAZARD_WORDS, "hazard", m["hazards"]), (FAULT_WORDS, "fault", m["faults"])):
        for phrase, cands in table:
            pick = next((c for c in cands if c in have), None)
            if pick:
                out.append((phrase, kind, pick))
    for kind, have in (("hazard", m["hazards"]), ("fault", m["faults"])):
        for iid, d in have.items():
            title = re.sub(r"^(?:a|an|the)\s+", "", re.sub(r"[^a-z0-9 ]+", " ", d["title"].lower())).strip()
            for p in {iid.replace("_", " "), title}:
                if p:
                    out.append((re.escape(p).replace(r"\ ", r"\s+"), kind, iid))
    return [(re.compile(rf"\b(?:{p})\b"), k, i) for p, k, i in out]


@dataclass
class Target:
    kind: str
    id: str
    start: int
    end: int
    values: list[float] = field(default_factory=list)


def find_targets(text: str, platform: str) -> list[Target]:
    found = []
    for rx, kind, iid in _vocab(platform):
        for mt in rx.finditer(text):
            found.append(Target(kind, iid, mt.start(), mt.end()))
    found.sort(key=lambda t: (t.start, -(t.end - t.start)))
    kept: list[Target] = []
    for t in found:
        if kept and t.start < kept[-1].end:
            continue                                                     # inside a longer match
        kept.append(t)
    return kept


def convert(hid: str, n: Num) -> float | None:
    """The number in the hazard's own unit, or None when its unit does not fit (a speed for a wave height)."""
    fams = EXPECT.get(hid, ("plain",))
    if n.family not in fams:
        return None
    if hid == "resistance":
        return n.n * n.factor if n.family == "share" else (n.n if n.n <= 1 else n.n / 100)
    return n.si


# ---------------------------------------------------------------- job numbers
def _window(text: str, n: Num) -> str:
    return text[max(0, n.start - 30):n.end + 24]


def job_numbers(text: str, platform: str, mode: str = "edit") -> list[tuple[str, float, Num]]:
    """[(job number name, value in its unit, the number found)]. mode 'new' (a job description: waves and wind are the job's own numbers, a bare length
    is the distance) or 'edit' (needs a cue word, so a number meant for a test is left alone)."""
    allowed = menu.PARAMS_OF[platform]
    out = []
    for n in find_numbers(text):
        win = _window(text, n)
        near_waves = bool(re.search(r"waves?|swell|surf|chop", text[n.end:n.end + 14] + " " + text[max(0, n.start - 14):n.start]))
        key = None
        if n.family == "time" and n.factor >= 60 and "minutes" in allowed:
            key = ("minutes", n.si / 60.0)
        elif n.family == "mass" and "payload_kg" in allowed:
            key = ("payload_kg", n.si)
        elif n.family == "length":
            if platform == "ground":
                cue_pipe, cue_dist = JOB_CUES["pipe_d"].search(win), JOB_CUES["distance_m"].search(win)
                if (n.unit and n.unit.lower() in ("cm", "mm") and not (cue_dist and not cue_pipe)) or (cue_pipe and n.si <= 3.0):
                    key = ("pipe_d", n.si)
                elif cue_dist or mode == "new":
                    key = ("distance_m", n.si)
            elif platform == "water":
                if mode == "new" and near_waves:
                    key = ("wave_hs", n.si)
                elif JOB_CUES["range_m"].search(win) and not near_waves:
                    key = ("range_m", n.si)
                elif mode == "new" and not near_waves:
                    key = ("range_m", n.si)
        elif n.family == "speed" and "wind_ms" in allowed and mode == "new":
            key = ("wind_ms", n.si)
        if key and key[0] in allowed:
            out.append((key[0], key[1], n))
    return out


def wet_words(text: str, platform: str, mode: str) -> float | None:
    """The ground job's wet/dry switch: any wet-ish word when describing a new job, only explicit phrasings when editing."""
    if platform != "ground":
        return None
    if mode == "new":
        if re.search(r"\b(wet|slippery|slimy|sludge|sludgy|muddy|damp|greasy)\b", text):
            return 1.0
        if re.search(r"\bdry\b", text):
            return 0.0
        return None
    if re.search(r"\b(?:floor|pipe|inside|ground)\s+(?:is\s+)?wet\b|\b(?:make it|set it to|it'?s|it is)\s+wet\b|\bwet (?:pipe|inside|conditions|floor|ground)\b", text):
        return 1.0
    if re.search(r"\b(?:floor|pipe|inside|ground)\s+(?:is\s+)?dry\b|\b(?:make it|set it to|it'?s|it is)\s+dry\b|\bdry (?:pipe|inside|conditions|floor|ground)\b", text):
        return 0.0
    return None


def extract_job(text: str, platform: str) -> dict:
    """The job numbers a first message states ("150 m away", "30 cm pipe", "1 m waves", "10 minutes", "wet"), for the quick draft."""
    t = normalise(text)
    out: dict = {}
    for key, val, _ in job_numbers(t, platform, "new"):
        out.setdefault(key, val)
    w = wet_words(t, platform, "new")
    if w is not None:
        out["wet"] = w
    return out


# ---------------------------------------------------------------- the edit grammar
@dataclass
class EditParse:
    ops: list[dict] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    chips: list[dict] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    guessed: bool = False
    flags: dict = field(default_factory=dict)


def _case_for(ctx: ParseCtx, kind: str, iid: str) -> dict | None:
    for c in ctx.cases:
        if kind == "hazard" and c.get("kind") == "sweep" and c["sweep"]["hazard"] == iid:
            return c
        if kind == "fault" and c.get("kind") == "fixed" and c["fixed"]["fault"] == iid:
            return c
    return None


def _pair(numbers: list[Num], targets: list[Target]) -> list[Num]:
    """Attach each number to the hazard it belongs to: the one whose unit fits, that has no number yet, and that is nearest. Returns the unattached."""
    hz = [t for t in targets if t.kind == "hazard"]
    loose = []
    for n in numbers:
        prev = max((t for t in hz if t.end <= n.start), key=lambda t: t.end, default=None)
        nxt = min((t for t in hz if t.start >= n.end), key=lambda t: t.start, default=None)
        cands = []
        for t, gap in ((prev, n.start - prev.end if prev else 0), (nxt, nxt.start - n.end if nxt else 0)):
            if t is not None and convert(t.id, n) is not None:
                cands.append((bool(t.values), gap, t))
        if not cands:
            loose.append(n)
            continue
        cands.sort(key=lambda c: (c[0], c[1]))
        cands[0][2].values.append(convert(cands[0][2].id, n))
        n.used = True
    return loose


def _top_move(case: dict, sign: int, platform: str) -> list[float]:
    """The levels after moving the most severe one a step: sign +1 tougher, -1 calmer. Tougher: the catalogue's next rung above, or x1.5 (x0.67 where a
    lower number is tougher). Calmer: drop the top level when there are more than three, else x0.67 (x1.5)."""
    s = case["sweep"]
    levels, desc = list(s["levels"]), s["descending"]
    top = levels[-1]
    if sign > 0:
        ladder = sorted((v for v in menu.load(platform)["hazards"][s["hazard"]]["levels"] if (v < top if desc else v > top)), reverse=desc)
        new = ladder[0] if ladder else (top * 0.67 if desc else top * 1.5)
        return levels[:-1] + [round(new, 4)]
    if len(levels) > 3:
        return levels[:-1]
    return levels[:-1] + [round(top * (1.5 if desc else 0.67), 4)]


def parse_edit(text: str, ctx: ParseCtx) -> EditParse:
    """Operations for a sentence that edits the plan. `text` is normalised. Nothing here clamps; see the module docstring."""
    ep = EditParse()
    platform = ctx.platform
    assert platform
    m = menu.load(platform)
    work = text

    # 1. what the simulator cannot do: a test that is recorded, never run
    for phrase, key in UNSUPPORTED:
        for mt in re.finditer(rf"\b(?:{phrase})\b", work):
            if NEGATION.search(work[:mt.start()]):
                continue
            title = mt.group(0).strip().capitalize()
            ep.unsupported.append(key)
            ep.ops.append({"op": "add_untestable", "title": title, "reason": words.UNTESTABLE_REASON.get(key, words.UNTESTABLE_REASON["other"]), "key": key})
            work = blank(work, mt.start(), mt.end())
            break

    # 2. tries and the bar
    for rx in RE_TRIES:
        mt = rx.search(work)
        if mt:
            ep.ops.append({"op": "set_tries", "case": None, "n": int(mt.group(1))})
            work = blank(work, mt.start(), mt.end())
            break
    for rx in RE_BAR:
        mt = rx.search(work)
        if mt:
            v = float(mt.group(1))
            pct = bool(mt.group(2)) or v > 1.0
            ep.ops.append({"op": "set_bar", "case": None, "value": v / 100.0 if pct else v})
            work = blank(work, mt.start(), mt.end())
            break

    # 3. job numbers (distance, pipe width, minutes, payload ...) and the wet/dry switch
    flags = {"remove": bool(RE_REMOVE.search(work)), "add": bool(RE_ADD.search(work)), "set": bool(RE_SET.search(work)), "upto": bool(RE_UPTO.search(work)),
             "levels": bool(RE_LEVELS.search(work)), "combo": bool(RE_COMBO.search(work)), "design": bool(RE_DESIGN.search(work))}
    work_flags = RE_REMOVE.sub(" ", work)                                      # "no more" must not read as "more"
    flags["up"] = bool(REL_UP.search(work_flags))
    flags["down"] = bool(REL_DOWN.search(work_flags))
    flags["num_up"] = bool(REL_NUM_UP.search(work_flags))
    flags["num_down"] = bool(REL_NUM_DOWN.search(work_flags))
    ep.flags = flags
    for key, val, n in job_numbers(work, platform, "edit"):
        ep.ops.append({"op": "set_job_number", "name": key, "value": val})
        n.used = True
        work = blank(work, n.start, n.end)
    w = wet_words(work, platform, "edit")
    if w is not None and "wet" in menu.PARAMS_OF[platform]:
        ep.ops.append({"op": "set_job_number", "name": "wet", "value": w})
        work = re.sub(r"\b(?:wet|dry)\b", " ", work)

    # 4. tests named in the sentence, with their numbers
    targets = find_targets(work, platform)
    numbers = [n for n in find_numbers(work) if not n.used]
    loose = _pair(numbers, targets)
    if loose and not any(t.kind == "hazard" for t in targets):                # a number whose unit alone points at one test ("add 12 m/s")
        for n in list(loose):
            cand = UNIT_DEFAULT.get(n.family)
            if cand and cand in m["hazards"] and convert(cand, n) is not None:
                targets.append(Target("hazard", cand, n.start, n.end, [convert(cand, n)]))
                loose.remove(n)
                ep.guessed = True
    for n in loose:
        ep.issues.append(f"I could not tell what {n.n:g}{(' ' + n.unit) if n.unit else ''} is for.")
    seen: dict[tuple, Target] = {}
    for t in targets:                                                          # one entry per test, values merged
        k = (t.kind, t.id)
        if k in seen:
            seen[k].values += t.values
        else:
            seen[k] = t
    uniq = list(seen.values())
    ep.targets = uniq

    # a combination: several tough conditions, each with a value, said to happen together
    valued = [t for t in uniq if t.kind == "hazard" and t.values]
    if flags["combo"] and len(valued) >= 2 and not flags["remove"]:
        knobs = C.allowed_knobs(platform)
        cond = {m["hazards"][t.id].get("knob"): t.values[-1] for t in valued if m["hazards"][t.id].get("knob") in knobs}
        if len(cond) >= 2:
            ep.ops.append({"op": "add_fixed", "conditions": cond})
            uniq = [t for t in uniq if not (t.kind == "hazard" and t.values)]

    rel_sign = (+1 if flags["up"] else -1 if flags["down"] else 0)
    for t in uniq:
        if t.kind == "hazard":
            case = _case_for(ctx, "hazard", t.id)
            vals = t.values
            desc = m["hazards"][t.id]["levels"][0] > m["hazards"][t.id]["levels"][-1]
            param = HAZARD_JOB.get(t.id)
            if flags["design"] and vals and param in menu.PARAMS_OF[platform]:
                ep.ops.append({"op": "set_job_number", "name": param, "value": vals[-1]})
            elif flags["remove"]:
                if case is not None:
                    ep.ops.append({"op": "remove", "case": case["id"]})
                else:
                    ep.issues.append(words.no_such_test(m["hazards"][t.id]["title"].lower()))
            elif vals:
                if case is None:
                    ep.ops.append({"op": "add_sweep", "hazard": t.id, **({"ask": vals[-1]} if not flags["add"] and len(vals) == 1 else {"levels": vals})})
                elif flags["upto"]:
                    keep = [v for v in case["sweep"]["levels"] if (v > vals[-1] if desc else v < vals[-1])]
                    ep.ops.append({"op": "set_levels", "case": case["id"], "levels": keep + [vals[-1]]})
                elif flags["levels"] or (len(vals) >= 2 and not flags["add"]):
                    ep.ops.append({"op": "set_levels", "case": case["id"], "levels": vals})
                elif flags["add"]:
                    ep.ops.append({"op": "add_sweep", "hazard": t.id, "levels": vals})
                else:
                    ep.ops.append({"op": "set_ask", "case": case["id"], "ask": vals[-1]})
            elif rel_sign or flags["num_up"] or flags["num_down"]:
                sign = rel_sign or ((1 if flags["num_up"] else -1) * (-1 if desc else 1))
                if case is None:
                    ep.ops.append({"op": "add_sweep", "hazard": t.id})
                else:
                    ep.ops.append({"op": "set_levels", "case": case["id"], "levels": _top_move(case, sign, platform)})
            elif flags["add"]:
                ep.ops.append({"op": "add_sweep", "hazard": t.id})
        else:
            case = _case_for(ctx, "fault", t.id)
            if flags["remove"]:
                if case is not None:
                    ep.ops.append({"op": "remove", "case": case["id"]})
                else:
                    ep.issues.append(words.no_such_test(m["faults"][t.id]["title"].lower()))
            elif flags["add"] or (case is None and not (RE_TEST.search(work) or RE_WATCH.search(work))):      # "add"/"what if" on an existing test: the engine says it is already there
                ep.ops.append({"op": "add_fixed", "fault": t.id})

    # a relative word about a job number, with no value: ask for one (value chips), never guess
    if not ep.ops and (rel_sign or flags["num_up"]) and not uniq:
        for rx, key in JOB_REL:
            if re.search(rf"\b(?:{rx})\b", work_flags) and (key is None or key in menu.PARAMS_OF[platform]):
                key = key or ("range_m" if platform == "water" else "distance_m")
                ep.chips = value_chips(key, platform, ctx.params, up=rel_sign >= 0)
                break

    # "make it tougher" with nothing named: every tough-condition test moves one step
    if not uniq and not ep.ops and not ep.chips and rel_sign and re.search(r"\b(it|everything|all|tests?|conditions?|things|water|sea|weather)\b", work_flags):
        for c in ctx.cases:
            if c.get("kind") == "sweep":
                ep.ops.append({"op": "set_levels", "case": c["id"], "levels": _top_move(c, rel_sign, platform)})
        ep.flags["all_tests"] = True
    return ep


def value_chips(key: str, platform: str, params: dict, up: bool = True) -> list[dict]:
    """Three concrete values for a job number the person only described ("longer"): the choice is theirs."""
    lo, hi, unit, _ = menu.PARAMS[key]
    cur = float(params.get(key) or {"minutes": 30, "payload_kg": 0.5, "pipe_d": 0.5, "distance_m": 50, "range_m": 200}.get(key, 1))
    label = {"minutes": "Runtime", "payload_kg": "Payload", "pipe_d": "Pipe width", "distance_m": "Distance", "range_m": "Range"}.get(key, key.replace("_", " ").capitalize())
    out = []
    for k in ((1.5, 2.0, 4.0) if up else (0.75, 0.5, 0.25)):
        v = round(max(lo, min(hi, cur * k)), 2 if cur < 10 else 0)
        if v not in [c["action"]["args"]["value"] for c in out]:
            out.append({"id": f"val:{key}:{v}", "label": words.value_chip(label, v, unit), "kind": "value", "action": {"type": "set_job_number", "args": {"name": key, "value": v}}})
    return out


# ---------------------------------------------------------------- actions and commands
def _ops_for_action(a: dict) -> list[dict] | None:
    """The structured edit actions, as case operations. None when the action is not an edit."""
    t, g = a["type"], a.get("args") or {}
    if t == "edit":
        ops = g.get("ops")
        return [dict(o) for o in ops if isinstance(o, dict)] if isinstance(ops, list) else []
    if t == "add_test":
        if g.get("fault"):
            return [{"op": "add_fixed", "fault": g["fault"]}]
        if g.get("conditions"):
            return [{"op": "add_fixed", "conditions": g["conditions"], **({"title": g["title"]} if g.get("title") else {})}]
        if g.get("untestable"):
            return [{"op": "add_untestable", "title": g["untestable"], "reason": g.get("reason") or words.UNTESTABLE_REASON["other"]}]
        return [{"op": "add_sweep", "hazard": g.get("hazard"), **{k: g[k] for k in ("levels", "ask") if g.get(k) is not None}}]
    if t == "edit_test":
        case, ops = g.get("case"), []
        if g.get("levels") is not None:
            ops.append({"op": "set_levels", "case": case, "levels": g["levels"]})
        if g.get("ask") is not None:
            ops.append({"op": "set_ask", "case": case, "ask": g["ask"]})
        if g.get("tries") is not None:
            ops.append({"op": "set_tries", "case": case, "n": g["tries"]})
        if g.get("bar") is not None:
            ops.append({"op": "set_bar", "case": case, "value": g["bar"]})
        return ops
    if t == "remove_test":
        return [{"op": "remove", "case": g.get("case")}]
    if t == "set_job_number":
        return [{"op": "set_job_number", "name": g.get("name"), "value": g.get("value")}]
    if t == "set_task_param":
        return [{"op": "set_task_param", "name": g.get("name"), "value": g.get("value")}]
    if t == "set_tries":
        return [{"op": "set_tries", "case": g.get("case"), "n": g.get("n")}]
    if t == "set_bar":
        return [{"op": "set_bar", "case": g.get("case"), "value": g.get("value")}]
    return None


def parse_action(a: dict | None) -> Intent:
    if not isinstance(a, dict) or not isinstance(a.get("type"), str):
        return Intent("unclear", "action", 0.0, issues=["an action needs a type"])
    t = a["type"]
    if t in FORBIDDEN_ACTIONS:
        return Intent("blocked", "action", 1.0, action={"type": t, "args": {}}, topic="hardware", issues=[words.blocked_hardware()])
    if t not in ACTION_TYPES:
        return Intent("blocked", "action", 1.0, action={"type": t, "args": {}}, topic="unknown", issues=[words.blocked_unknown_action(t[:40])])
    args = a.get("args") if isinstance(a.get("args"), dict) else {}
    ops = _ops_for_action({"type": t, "args": args})
    if ops is not None:
        return Intent("edit", "action", 1.0, action={"type": t, "args": args}, ops=ops)
    kind = {"answer": "question"}.get(t, t)
    return Intent(kind, "action", 1.0, action={"type": t, "args": args}, topic=args.get("topic") if t == "answer" else None)


def _watch_args(text: str, ctx: ParseCtx) -> tuple[dict, str]:
    """Arguments for a watch request from the words around 'watch'."""
    args: dict = {"case": None, "level": None, "conditions": None, "worst": False, "weakest": False}
    if not ctx.has_plan:
        return args, "watch it"
    if RE_WEAKEST.search(text):
        args["weakest"] = True
        return args, "watch the weakest test"
    ep = parse_edit(RE_WATCH.sub(" ", text), ParseCtx(ctx.platform, ctx.cases, ctx.params))
    hz = [t for t in ep.targets if t.kind == "hazard"]
    if hz and any(t.values for t in hz):
        m = menu.load(ctx.platform)
        cond = {m["hazards"][t.id]["knob"]: t.values[-1] for t in hz if t.values and m["hazards"][t.id].get("knob")}
        args["conditions"] = cond or None
        return args, "watch at " + ", ".join(f"{k} {v:g}" for k, v in cond.items())
    named = [t for t in ep.targets if _case_for(ctx, t.kind, t.id)]
    if named:
        args["case"] = _case_for(ctx, named[0].kind, named[0].id)["id"]
        args["worst"] = True
        return args, f"watch the worst {named[0].id.replace('_', ' ')} case"
    if RE_WORST.search(text):
        args["worst"] = True
        return args, "watch it in the worst conditions"
    if re.search(r"\b(rough|tough|hard|stormy|bad)\b", text):
        args["worst"] = True
        return args, "watch it in rough conditions"
    return args, "watch it in calm conditions"


def _test_args(text: str, ctx: ParseCtx) -> tuple[dict, str]:
    if ctx.has_plan and ctx.cases and not re.fullmatch(r"(?:please )?(?:run|test|stress|check)\s+(?:all|everything|them|it)(?:\s+(?:tests?|now|please))*[.!]*", text):
        ids = []
        for t in find_targets(RE_TEST.sub(" ", text), ctx.platform):
            c = _case_for(ctx, t.kind, t.id)
            if c and c["id"] not in ids:
                ids.append(c["id"])
        if ids:
            return {"cases": ids}, "run the test" + ("s" if len(ids) > 1 else "") + ": " + ", ".join(i.replace("tc_f_", "").replace("tc_", "") for i in ids)
    return {"cases": "all"}, "run all the tests"


def parse_command(text: str, ctx: ParseCtx) -> Intent:
    cmd, _, rest = text.strip().partition(" ")
    cmd, rest = cmd.lower(), normalise(rest)
    if cmd == "/watch":
        a, ra = _watch_args(rest, ctx)
        return Intent("watch", "command", 1.0, {"type": "watch", "args": a}, read_as=ra)
    if cmd in ("/test", "/run"):
        a, ra = _test_args(rest, ctx)
        return Intent("run_tests", "command", 1.0, {"type": "run_tests", "args": a}, read_as=ra)
    if cmd == "/sheet":
        return Intent("sheet", "command", 1.0, {"type": "sheet", "args": {"format": "json"}}, read_as="show the parts and build sheet")
    if cmd == "/undo":
        return Intent("undo", "command", 1.0, {"type": "undo", "args": {}}, read_as="undo the last edit")
    if cmd == "/redo":
        return Intent("redo", "command", 1.0, {"type": "redo", "args": {}}, read_as="redo")
    if cmd == "/new":
        return Intent("new_chat", "command", 1.0, {"type": "new_chat", "args": {"text": rest}}, read_as="start a new chat")
    if cmd == "/stop":
        return Intent("stop", "command", 1.0, {"type": "stop", "args": {"job": "all"}}, read_as="stop everything")
    if cmd == "/help":
        return Intent("help", "command", 1.0, {"type": "answer", "args": {"topic": "help"}}, topic="help")
    return Intent("help", "command", 0.5, {"type": "answer", "args": {"topic": "help"}}, topic="help", issues=[f"I do not know the command {cmd}."])


# ---------------------------------------------------------------- the parser
def suggestions(ctx: ParseCtx) -> list[tuple[str, dict]]:
    """Phrases the 'did you mean' chips can offer, each with the action it runs."""
    out: list[tuple[str, dict]] = [("run all tests", {"type": "run_tests", "args": {"cases": "all"}}), ("watch it", {"type": "watch", "args": {}}),
                                   ("parts list and build sheet", {"type": "sheet", "args": {"format": "json"}}), ("undo", {"type": "undo", "args": {}}),
                                   ("what can you not test", {"type": "answer", "args": {"topic": "limits"}})]
    if ctx.has_plan:
        for c in ctx.cases:
            if c.get("status") != "untestable":                           # a bare mention ("motor out") gets "run the ... test" chips
                name = re.sub(r"^(?:a|an|the)\s+", "", (c["sweep"]["title"] if c.get("kind") == "sweep" else c["title"]).lower())
                out.append((f"run the {name} test", {"type": "run_tests", "args": {"cases": [c["id"]]}}))
            if c.get("kind") == "sweep":
                title = c["sweep"]["title"].lower()
                out.append((f"make the {title} bigger", {"type": "edit", "args": {"ops": [{"op": "set_levels", "case": c["id"], "levels": _top_move(c, +1, ctx.platform)}]}}))
                out.append((f"watch the worst {title}", {"type": "watch", "args": {"worst": True, "case": c["id"]}}))
    return out


def did_you_mean(text: str, ctx: ParseCtx) -> list[dict]:
    toks = set(re.findall(r"[a-z]+", text))
    scored = []
    for phrase, action in suggestions(ctx):
        ptoks = set(re.findall(r"[a-z]+", phrase))
        overlap = len(toks & ptoks) / max(len(ptoks), 1)
        fuzzy = max((difflib.SequenceMatcher(None, a, b).ratio() for a in toks for b in ptoks), default=0.0)
        scored.append((overlap + 0.5 * fuzzy, phrase, action))
    scored.sort(key=lambda x: -x[0])
    return [{"id": f"dym:{i}", "label": phrase.capitalize(), "kind": "suggest", "action": action} for i, (_, phrase, action) in enumerate(scored[:3])]


def _verb(t: str) -> str | None:
    """'test' or 'watch': a strong phrase decides, otherwise whichever verb comes first (the longer match wins a tie: 'run it live' is a watch)."""
    if STRONG_TEST.search(t):
        return "test"
    cands = [(mt.start(), -(mt.end() - mt.start()), kind) for kind, rx in (("test", RE_TEST), ("watch", RE_WATCH)) for mt in rx.finditer(t)]
    return min(cands)[2] if cands else None


def ctx_from_book(book, has_results: bool = False, pending_ai: bool = False) -> ParseCtx:
    return ParseCtx(platform=book.platform, cases=book.cases, params=book.params, has_results=has_results, pending_ai=pending_ai)


def parse(text: str | None = None, action: dict | None = None, ctx: ParseCtx | None = None) -> Intent:
    """The whole parser. `action` (a chip or button) wins over `text`."""
    ctx = ctx or ParseCtx()
    if action is not None:
        return parse_action(action)
    raw = (text or "").strip()
    if not raw:
        return Intent("unclear", "fallback", 0.0, issues=["empty message"])
    if raw.startswith("/"):
        return parse_command(raw, ctx)
    t = normalise(raw)
    n_words = len(re.findall(r"[a-z0-9]+", t))
    hits = match(t)

    if not ctx.has_plan and n_words > 4 and hits:                       # a long sentence about a ready-made job is a job, whatever verbs it uses ("a drone to watch a field")
        return Intent("new_job", "new_job", 0.9, {"type": "new_job", "args": {"text": raw}}, read_as="start a new job", meta={"goal": hits[0]["goal"]})
    if RE_UNDO.match(t):
        return Intent("undo", "verb", 0.95, {"type": "undo", "args": {}}, read_as="undo the last edit")
    if RE_REDO.match(t):
        return Intent("redo", "verb", 0.95, {"type": "redo", "args": {}}, read_as="redo")
    if RE_STOP.match(t):
        return Intent("stop", "verb", 0.95, {"type": "stop", "args": {"job": "all"}}, read_as="stop everything that is running")
    if RE_CHITCHAT.match(t):
        return Intent("help", "fallback", 0.5, {"type": "answer", "args": {"topic": "help"}}, topic="help")

    if ctx.has_plan and hits and n_words >= 4 and hits[0]["score"] >= 2:          # clearly another kind of job: ask, never edit this one into it
        from ..goals.catalogue import all_goals
        platform = all_goals()[hits[0]["goal"]].platform
        if platform != ctx.platform:
            return Intent("confirm_new", "new_job", 0.7, {"type": "new_chat", "args": {"text": raw}}, read_as="a different job", meta={"goal": hits[0]["goal"], "platform_differs": True})
    if ctx.has_plan and RE_WATCH_FIRST.match(t):                         # "simulate wind 15 m/s", "show me waves of 0.9 m": watching at that condition, not editing the test
        a, ra = _watch_args(t, ctx)
        if a["conditions"] or a["case"]:
            return Intent("watch", "verb", 0.85, {"type": "watch", "args": a}, read_as=ra)
    if not ctx.has_plan and not hits and n_words <= 6 and (any(find_targets(t, p) for p in menu.PLATFORMS) or REL_UP.search(t) or REL_DOWN.search(t) or RE_REMOVE.search(t)):
        return Intent("blocked", "fallback", 0.6, {"type": "answer", "args": {"topic": "no_plan"}}, topic="no_plan", issues=[words.blocked_no_plan()])

    ep = EditParse()
    if ctx.has_plan:
        ep = parse_edit(t, ctx)
        if ep.ops:
            conf = 0.9 if not ep.issues and not ep.guessed else 0.65
            return Intent("edit", "grammar", conf, {"type": "edit", "args": {"ops": ep.ops}}, ops=ep.ops, issues=ep.issues, meta={"unsupported": ep.unsupported})
        if ep.chips:
            return Intent("ambiguous", "grammar", 0.5, chips=ep.chips, issues=[words.needs_value("that")])

    for topic, rx in QUESTIONS:
        if rx.search(t):
            if topic == "can_it" and not ctx.has_plan:
                continue
            return Intent("question", "question", 0.85, {"type": "answer", "args": {"topic": topic}}, topic=topic, read_as="answer a question from what is stored")

    if RE_HARDWARE.search(t):
        return Intent("hardware", "verb", 0.9, {"type": "answer", "args": {"topic": "hardware"}}, topic="hardware", read_as="point to the Hardware screen")
    if RE_TRAIN.search(t) and not re.search(r"\b(tests?|waves?|wind)\b", t):
        return Intent("train", "verb", 0.9, {"type": "answer", "args": {"topic": "train"}}, topic="train", read_as="training is not in the chat yet")
    if ctx.has_plan or n_words <= 4:                                   # with no job yet only a short, command-like sentence counts as a verb
        if re.search(r"\b(build sheet|parts list|shopping list|bill of materials)\b", t) or (RE_SHEET.search(t) and _verb(t) is None):
            return Intent("sheet", "verb", 0.9, {"type": "sheet", "args": {"format": "json"}}, read_as="show the parts list and build sheet")
        verb = _verb(t)
        if verb == "test":
            a, ra = _test_args(t, ctx)
            return Intent("run_tests", "verb", 0.85, {"type": "run_tests", "args": a}, read_as=ra)
        if verb == "watch":
            a, ra = _watch_args(t, ctx)
            return Intent("watch", "verb", 0.85, {"type": "watch", "args": a}, read_as=ra)
        if RE_SHEET.search(t):
            return Intent("sheet", "verb", 0.8, {"type": "sheet", "args": {"format": "json"}}, read_as="show the parts list and build sheet")
        if RE_HELP.search(t):
            return Intent("help", "verb", 0.7, {"type": "answer", "args": {"topic": "help"}}, topic="help")
    if ctx.has_plan and RE_NEW.search(t):
        return Intent("confirm_new", "new_job", 0.8, {"type": "new_chat", "args": {"text": ""}}, read_as="start a new job", meta={"platform_differs": False})

    if not ctx.has_plan:                                               # anything else describes a job: the quick draft, the AI, or the three ready-made jobs
        return Intent("new_job", "new_job", 0.9 if hits else 0.5, {"type": "new_job", "args": {"text": raw}}, read_as="start a new job",
                      meta={"goal": hits[0]["goal"] if hits else None})
    if hits:
        from ..goals.catalogue import all_goals
        platform = all_goals()[hits[0]["goal"]].platform
        if platform != ctx.platform or (hits[0]["score"] >= 2 and n_words >= 5):
            return Intent("confirm_new", "new_job", 0.7, {"type": "new_chat", "args": {"text": raw}}, read_as="a different job",
                          meta={"goal": hits[0]["goal"], "platform_differs": platform != ctx.platform})
    return Intent("unclear", "fallback", 0.2, chips=did_you_mean(t, ctx), issues=ep.issues)
