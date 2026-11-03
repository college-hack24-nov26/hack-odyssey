"""Training and evaluation. Imports are lazy: Stable-Baselines3 is only needed to actually train."""


def __getattr__(name):
    if name in ("train", "RUNS", "DEFAULTS", "merge"):
        from . import rl
        return getattr(rl, name)
    if name in ("evaluate", "compare", "wilson"):
        from . import evaluate as ev
        return getattr(ev, name)
    raise AttributeError(name)


__all__ = ["train", "evaluate", "compare", "wilson", "RUNS", "DEFAULTS", "merge"]
