"""CLI entry point.

`run()` receives its collaborators as arguments so the pipeline can be
tested without a filesystem, a network, or an SMTP server. `main()` is the
only place that reads argv, the environment, and the clock.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from datetime import date
from pathlib import Path

from .config import Config, ConfigError, load_config
from .email_sender import SendError, send
from .scheduling import due_on, previous_occurrence, today_in
from .sources import RosterError, ScheduleSource, build_source
from .templates import render


def _with_sheet_override(cfg: Config, path: str) -> Config:
    """Config with the roster forced to a local xlsx file (--sheet)."""
    return Config(
        schedule=dataclasses.replace(cfg.schedule, source="xlsx", path=path),
        email=cfg.email,
    )


def run(
    cfg: Config,
    *,
    today: date,
    dry_run: bool,
    password: str,
    source: ScheduleSource,
) -> int:
    """Fetch, decide, render and send. Returns a process exit code."""
    occurrences = source.fetch()
    pending = due_on(occurrences, today)

    if not pending:
        print(f"{today}: inga påminnelser att skicka.")
        return 0

    failures = 0
    for occ, role in pending:
        previous = previous_occurrence(
            occurrences, occ, max_gap_days=cfg.schedule.max_handoff_gap_days
        )
        message = render(
            occ,
            role,
            previous,
            chore=cfg.schedule.chore,
            schedule_link=cfg.schedule.schedule_link,
            documents_link=cfg.schedule.documents_link,
        )

        if dry_run:
            print(f"--- {role.value} -> {', '.join(message.to)}")
            print(f"Ämne: {message.subject}")
            print(message.body)
            print()
            continue

        try:
            send(message, cfg.email, password=password)
            print(f"Skickade {role.value} till {', '.join(message.to)}")
        except SendError as exc:
            # Keep going: with no retry state, skipping the rest would
            # silently drop reminders for unrelated groups.
            print(f"FEL: {exc}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="duty_mailer",
        description="Skickar påminnelser om kommande sysslor.",
    )
    parser.add_argument(
        "--config", default="config.yaml", help="Sökväg till config.yaml"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Visa vad som skulle skickas, skicka inget",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Kör som om det vore detta datum (ÅÅÅÅ-MM-DD)",
    )
    parser.add_argument(
        "--sheet", help="Åsidosätt schedule.path från konfigurationen"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cfg = load_config(Path(args.config))
        if args.sheet:
            cfg = _with_sheet_override(cfg, args.sheet)
        today = args.date or today_in(cfg.schedule.timezone)
        source = build_source(cfg.schedule)
        password = os.environ.get("SMTP_PASSWORD", "")
        return run(
            cfg,
            today=today,
            dry_run=args.dry_run,
            password=password,
            source=source,
        )
    except (ConfigError, RosterError, SendError) as exc:
        print(f"FEL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
