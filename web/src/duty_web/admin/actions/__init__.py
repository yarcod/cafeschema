"""The admin actions. Importing this package registers every one of them.

Each action module registers itself with @register on import; list them
here so both doors see the same registry.
"""

from .base import REGISTRY, register  # noqa: F401
from . import places, roster_import, team_admins  # noqa: F401,E402
