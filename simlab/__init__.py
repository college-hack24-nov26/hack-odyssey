"""simlab: describe any machine as data, simulate it on a pluggable backend, train policies and detectors for it."""
from . import tasks, vehicles  # noqa: F401  (register the built-in vehicle kinds and tasks)
from .catalog import get_vehicle, save_vehicle, vehicle_names
from .registry import BACKENDS, KINDS, POLICIES, TASKS, load_plugins
from .spec import TaskSpec, VehicleSpec
from .tasks import get_task_spec, task_names

BACKENDS.add_lazy("mujoco", "simlab.backends.mujoco.backend:MujocoBackend")   # imports mujoco only when first used
BACKENDS.add_lazy("webots", "simlab.backends.webots:WebotsBackend")            # launches Webots when a run is created
# Plug-ins (SIMLAB_PLUGINS, config/plugins.txt) are NOT imported here: they may need MuJoCo/OpenGL, which must never be
# pulled into a Webots controller process. They load the first time a name is not found (registries) or the catalog is listed.

__version__ = "0.1.0"
__all__ = ["BACKENDS", "KINDS", "POLICIES", "TASKS", "TaskSpec", "VehicleSpec", "get_task_spec", "get_vehicle",
           "load_plugins", "save_vehicle", "task_names", "vehicle_names"]
