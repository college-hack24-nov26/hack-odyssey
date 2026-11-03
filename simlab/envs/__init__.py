from .core import Sim


def __getattr__(name):                 # gymnasium is an optional dependency: import the RL wrapper only when asked for
    if name in ("SimEnv", "as_vehicle", "as_task"):
        from . import gym_env
        return getattr(gym_env, name)
    raise AttributeError(name)


__all__ = ["Sim", "SimEnv"]
