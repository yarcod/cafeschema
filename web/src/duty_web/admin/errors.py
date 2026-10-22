"""What an action raises when its input can't be planned.

Kept apart from actions/ so that modules the actions themselves import
(members_csv) can raise these without importing the actions package.
"""

from __future__ import annotations


class ValidationError(Exception):
    """Input that can't be planned. errors maps field name → Swedish message."""

    def __init__(self, errors: dict[str, str]):
        super().__init__(errors)
        self.errors = errors


class NearDuplicate(Exception):
    """A name that normalizes to one that already exists (spec D4)."""

    def __init__(self, field_name: str, existing_name: str, existing_id: int):
        super().__init__(existing_name)
        self.field = field_name
        self.existing_name = existing_name
        self.existing_id = existing_id
