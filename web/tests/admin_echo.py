"""A test-only action: plans one Venue named after its text, once.

Its plan depends on the database (an existing venue means nothing to do),
so a replayed token re-plans differently and is refused like a real one.
"""

from __future__ import annotations

from dataclasses import dataclass

from duty_web.admin.actions.base import Action, Plan, ValidationError
from duty_web.models import Venue
from duty_web.names import normalize_key

ECHO_TEMPLATE = (
    '{% extends "admin/base_admin.html" %}{% block content %}'
    '<form method="post">{{ errors.get("text", "") }}<input name="text"></form>'
    "{% endblock %}"
)


@dataclass
class EchoInput:
    text: str
    count: int = 1


def _mail_fails():
    raise RuntimeError("smtp nere")


class EchoAction(Action):
    name = "tests.echo"
    title = "Eko"
    scope = "team"
    Input = EchoInput
    template = "admin/actions/_test_echo.html"

    def plan(self, session, ctx, inp):
        if not inp.text:
            raise ValidationError({"text": "Saknas"})
        if inp.text == "boom":
            raise RuntimeError("boom")
        plan = Plan()
        if session.query(Venue).filter_by(name_key=normalize_key(inp.text)).first():
            return plan
        for i in range(inp.count):
            plan.add("add", "venue", f"{inp.text} {i}")
        text_ = inp.text
        plan.writes.append(
            lambda s: s.add(Venue(name=text_, name_key=normalize_key(text_)))
        )
        if text_.startswith("mail"):
            plan.after_commit.append(_mail_fails)
        return plan
