"""Shared, stdlib-only core for the experimental menu prototype.

The public surface is intentionally small: contracts (types), catalog
(rows), profile (JSON wire format), controller (state machine), and
simulated (deterministic host).  Renderers must consume ``MenuView`` only and
must never call ``HostAdapter.launch`` directly.
"""

from .catalog import ACTIONS, ACTIONS_BY_ID, build_rows
from .contracts import (
    ActionSpec,
    Availability,
    ContextLevel,
    ContextSnapshot,
    ControllerSummary,
    HostAdapter,
    LaunchResult,
    MenuEvent,
    MenuRow,
    MenuView,
    ParameterSpec,
)
from .controller import MenuController
from .parameters import (
    ParameterError,
    parameter_schema,
    requires_parameters,
    validate_parameters,
)
from .profile import (
    MenuProfile,
    ProfileEntry,
    ProfileError,
    default_profile,
    dump_profile,
    load_profile,
)
from .simulated import SCENARIO_NAMES, SimulatedHost

__all__ = [
    "ACTIONS",
    "ACTIONS_BY_ID",
    "ActionSpec",
    "Availability",
    "ContextLevel",
    "ContextSnapshot",
    "ControllerSummary",
    "HostAdapter",
    "LaunchResult",
    "MenuController",
    "MenuEvent",
    "MenuProfile",
    "MenuRow",
    "MenuView",
    "ParameterError",
    "ParameterSpec",
    "ProfileEntry",
    "ProfileError",
    "SCENARIO_NAMES",
    "SimulatedHost",
    "build_rows",
    "default_profile",
    "dump_profile",
    "load_profile",
    "parameter_schema",
    "requires_parameters",
    "validate_parameters",
]
