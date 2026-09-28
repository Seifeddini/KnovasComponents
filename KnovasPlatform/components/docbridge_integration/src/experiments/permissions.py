"""Who may do what in the experiments module.

The module is invisible to anyone without a viewing role: its navigation item
is not drawn and its routes answer 404, exactly as if it were switched off.
That is what keeps the standard search view uncluttered for everyone else.
"""

from __future__ import annotations

from typing import Any, FrozenSet

EXPERIMENTER = "experimenter"
MANAGER = "experiments_manager"
ADMIN = "admin"

#: May see the module, create experiments and record everything about them.
VIEW_ROLES: FrozenSet[str] = frozenset({EXPERIMENTER, MANAGER, ADMIN})

#: May maintain domains, types, metrics and evaluators, delete experiments,
#: and operate the index.
MANAGE_ROLES: FrozenSet[str] = frozenset({MANAGER, ADMIN})


def _roles(user: Any) -> FrozenSet[str]:
    if user is None:
        return frozenset()
    return frozenset(getattr(user, "roles", None) or ())


def can_view(user: Any) -> bool:
    return bool(_roles(user) & VIEW_ROLES)


def can_manage(user: Any) -> bool:
    return bool(_roles(user) & MANAGE_ROLES)
