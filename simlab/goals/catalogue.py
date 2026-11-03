"""The goal catalogue: what a user can ask for ("inspect pipes", "rescue on the water", "watch from the air"), in plain words.

Each goal is one YAML file in simlab/goals/catalogue/ (yours go in config/goals/ and override by id). A goal names a platform, the
keywords that match a typed sentence, up to three plain questions whose answers become numbers, and the task its machine is
tested on. What the answers mean for the machine is code (design.py), not data, so a goal cannot ask for something the
software cannot check.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BUILTIN = Path(__file__).parent / "catalogue"


def user_dir() -> Path:
    return Path(os.environ.get("SIMLAB_GOALS", Path(__file__).resolve().parents[2] / "config" / "goals"))


@dataclass
class Choice:
    label: str
    params: dict


@dataclass
class Question:
    id: str
    text: str
    choices: list[Choice]
    default: int = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "default": self.default, "choices": [{"label": c.label} for c in self.choices]}


@dataclass
class Goal:
    id: str
    title: str
    platform: str
    task: str
    card: dict
    keywords: list[str]
    questions: list[Question]
    hazards: str = ""
    extra: dict = field(default_factory=dict)

    def params(self, answers: dict | None) -> dict:
        """Answers {question id: choice index} -> one flat dict of numbers; missing or bad answers fall back to the default."""
        out: dict = {}
        for q in self.questions:
            i = (answers or {}).get(q.id, q.default)
            if not isinstance(i, int) or not 0 <= i < len(q.choices):
                i = q.default
            out.update(q.choices[i].params)
        return out

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "platform": self.platform, "task": self.task, "card": self.card,
                "questions": [q.to_dict() for q in self.questions]}


def _parse(d: dict) -> Goal:
    qs = [Question(q["id"], q["text"], [Choice(c["label"], dict(c.get("params", {}))) for c in q["choices"]], int(q.get("default", 0)))
          for q in d.get("questions", [])]
    if len(qs) > 4 or not all(q.choices for q in qs):
        raise ValueError(f"goal {d.get('id')!r}: at most 4 questions, each with choices")
    return Goal(str(d["id"]), str(d["title"]), str(d["platform"]), str(d["task"]), dict(d.get("card", {})),
                [str(k).lower() for k in d.get("keywords", [])], qs, str(d.get("hazards", "")))


def all_goals() -> dict[str, Goal]:
    out: dict[str, Goal] = {}
    for folder in (BUILTIN, user_dir()):
        if folder.is_dir():
            for f in sorted(folder.glob("*.yaml")):
                try:
                    g = _parse(yaml.safe_load(f.read_text(encoding="utf-8-sig")))
                except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
                    continue                                    # a broken goal file never takes the others down
                out[g.id] = g
    return out


def get_goal(goal_id: str) -> Goal:
    goals = all_goals()
    if goal_id not in goals:
        raise KeyError(f"unknown goal {goal_id!r}; known: {', '.join(goals)}")
    return goals[goal_id]
