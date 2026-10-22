"""One definition per admin action, run by both doors (spec D1, D2).

An action's plan() is pure: it reads the database, validates, and returns a
Plan — the changes a person reviews in Granska or an agent gets from
dry_run — plus the writes that will make exactly those changes. Nothing is
written until the executor applies a plan whose digest still matches the
one that was reviewed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import types
from collections.abc import Callable, Mapping
from dataclasses import MISSING, asdict, dataclass, field
from typing import Any, ClassVar, Union, get_args, get_origin, get_type_hints

from sqlalchemy.orm import Session

from ..errors import NearDuplicate, ValidationError  # noqa: F401 (re-exported)

OPS = ("add", "update", "delete")


@dataclass(frozen=True)
class Change:
    op: str
    kind: str
    # Names only, never addresses: this is shown in Granska and returned to
    # agents verbatim.
    summary: str
    detail: str = ""


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    writes: list[Callable[[Session], None]] = field(
        default_factory=list, repr=False, compare=False
    )
    # Run after commit (mail, mostly). A failure is logged, never rolled back.
    after_commit: list[Callable[[], None]] = field(
        default_factory=list, repr=False, compare=False
    )

    def add(self, op: str, kind: str, summary: str, detail: str = "") -> None:
        if op not in OPS:
            raise ValueError(f"okänd op {op!r}")
        self.changes.append(Change(op, kind, summary, detail))

    def public(self) -> dict[str, Any]:
        counts = {op: 0 for op in OPS}
        for change in self.changes:
            counts[change.op] += 1
        return {
            "changes": [asdict(c) for c in self.changes],
            "warnings": list(self.warnings),
            "counts": counts,
        }

    def digest(self) -> str:
        body = json.dumps(
            {"changes": [asdict(c) for c in self.changes], "warnings": self.warnings},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class Action:
    name: ClassVar[str]
    title: ClassVar[str]
    scope: ClassVar[str] = "team"
    Input: ClassVar[type]
    template: ClassVar[str]
    uploads: ClassVar[tuple[str, ...]] = ()
    done_endpoint: ClassVar[str] = "admin.lag"

    def parse(self, fields: Mapping[str, Any], files: Mapping[str, Any]) -> Any:
        return parse_input(self.Input, fields)

    def form_context(self, session: Session, ctx) -> dict[str, Any]:
        return {}

    def plan(self, session: Session, ctx, inp) -> Plan:
        raise NotImplementedError


REGISTRY: dict[str, Action] = {}


def register(cls: type[Action]) -> type[Action]:
    if cls.name in REGISTRY:
        raise RuntimeError(f"Åtgärden {cls.name} är redan registrerad")
    REGISTRY[cls.name] = cls()
    return cls


_TYPE_NAMES = {str: "string", int: "integer", bool: "boolean", dict: "object"}


def _unwrap_optional(kind: Any) -> tuple[Any, bool]:
    if get_origin(kind) in (Union, types.UnionType):
        args = [a for a in get_args(kind) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return kind, False


def _has_default(f: dataclasses.Field) -> bool:
    return f.default is not MISSING or f.default_factory is not MISSING


def _coerce(kind: Any, raw: Any) -> Any:
    if get_origin(kind) is list:
        (item,) = get_args(kind)
        return [_coerce(item, value) for value in (raw if isinstance(raw, list) else [raw])]
    if kind is bool:
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "on", "ja", "yes")
    if kind is int:
        if isinstance(raw, bool):
            raise TypeError("bool är inget heltal")
        return int(str(raw).strip())
    if kind is str:
        if isinstance(raw, (dict, list)):
            raise TypeError("förväntade text")
        return str(raw).strip()
    if kind is dict:
        if not isinstance(raw, dict):
            raise TypeError("förväntade ett objekt")
        return raw
    raise TypeError(f"fälttypen {kind} stöds inte")


def parse_input(cls: type, fields: Mapping[str, Any]) -> Any:
    """Build an action's Input from a form (MultiDict) or a JSON object."""
    hints = get_type_hints(cls)
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for f in dataclasses.fields(cls):
        kind, optional = _unwrap_optional(hints[f.name])
        if get_origin(kind) is list and hasattr(fields, "getlist"):
            raw = fields.getlist(f.name) or None
        else:
            raw = fields.get(f.name)
        if raw is None or raw == "" or raw == []:
            if _has_default(f):
                continue
            if optional:
                values[f.name] = None
                continue
            errors[f.name] = "Saknas"
            continue
        try:
            values[f.name] = _coerce(kind, raw)
        except (TypeError, ValueError):
            errors[f.name] = "Måste vara ett heltal" if kind is int else "Ogiltigt värde"
    if errors:
        raise ValidationError(errors)
    return cls(**values)


def fields_schema(cls: type) -> dict[str, str]:
    """Field name → type for GET /admin/api; a trailing ? marks optional."""
    hints = get_type_hints(cls)
    schema = {}
    for f in dataclasses.fields(cls):
        kind, optional = _unwrap_optional(hints[f.name])
        if get_origin(kind) is list:
            name = _TYPE_NAMES[get_args(kind)[0]] + "[]"
        else:
            name = _TYPE_NAMES[kind]
        schema[f.name] = name + ("?" if optional or _has_default(f) else "")
    return schema
