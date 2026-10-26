"""Explicit local report exports with redacted data and safe spreadsheet cells."""

import argparse
import csv
import html
import io
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from jira_cli.config import UserError

COLUMNS = [
    "key",
    "summary",
    "status",
    "priority",
    "assignee",
    "created",
    "updated",
    "due_date",
    "currently_active",
    "completed_at",
    "carried_over",
    "overdue",
    "stale",
    "blockers",
    "inferred_risks",
    "url",
]


def add_parsers(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    flags: Callable[[argparse.ArgumentParser], None],
) -> None:
    group = commands.add_parser(
        "report", help="Read-only reports and explicit local exports."
    )
    flags(group)
    actions = group.add_subparsers(dest="action", required=True)
    for kind in ["daily", "weekly", "aging"]:
        leaf = actions.add_parser(kind)
        flags(leaf)
        leaf.add_argument("--mine", action="store_true")
        leaf.add_argument(
            "--since", help="YYYY-MM-DD, midnight UTC; see report definitions."
        )
        leaf.add_argument(
            "--output", type=Path, help="Create a new local file; never overwrite."
        )


def cell(value: object) -> str:
    """JSON escapes terminal controls, newlines and non-ASCII consistently."""
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True)
    return encoded[1:-1] if isinstance(value, str) else encoded


def spreadsheet(value: object) -> str:
    text = cell(value)
    # Check unescaped leading whitespace/control characters as well as visible
    # formula prefixes. Quoting a CSV field alone does not stop formula execution.
    original = str(value).lstrip() if isinstance(value, str) else text
    if original.startswith(("=", "+", "-", "@")) or (
        isinstance(value, str) and value.startswith(("\t", "\r", "\n"))
    ):
        return "'" + text
    return text


def markdown(value: object) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|\-])", r"\\\1", html.escape(cell(value)))


def render_csv(data: dict[str, Any]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["record_type", "metadata", *COLUMNS])
    writer.writeheader()
    metadata = {k: v for k, v in data.items() if k != "issues"}
    writer.writerow(
        {"record_type": "metadata", "metadata": json.dumps(metadata, sort_keys=True)}
    )
    for row in data["issues"]:
        writer.writerow(
            {"record_type": "issue", **{key: spreadsheet(row[key]) for key in COLUMNS}}
        )
    return stream.getvalue()


def render_markdown(data: dict[str, Any]) -> str:
    metadata = {k: v for k, v in data.items() if k != "issues"}
    lines = ["# " + markdown(data["report"]) + " report", ""]
    lines.extend(f"- **{markdown(k)}**: {markdown(v)}" for k, v in metadata.items())
    lines.extend(
        [
            "",
            "| " + " | ".join(COLUMNS) + " |",
            "| " + " | ".join("---" for _ in COLUMNS) + " |",
        ]
    )
    lines.extend(
        "| " + " | ".join(markdown(row[k]) for k in COLUMNS) + " |"
        for row in data["issues"]
    )
    return "\n".join(lines) + "\n"


def render_table(data: dict[str, Any]) -> str:
    stream = io.StringIO()
    console = Console(
        file=stream,
        force_terminal=False,
        color_system=None,
        width=120,
        markup=False,
        highlight=False,
    )
    for key, value in data.items():
        if key != "issues":
            console.print(key + ": " + cell(value))
    table = Table("Key", "Summary", "Status", "Priority", "Evidence / flags")
    for row in data["issues"]:
        evidence = {
            k: row[k]
            for k in [
                "currently_active",
                "completed_at",
                "carried_over",
                "overdue",
                "stale",
                "blockers",
                "inferred_risks",
            ]
        }
        table.add_row(
            *(cell(row[k]) for k in ["key", "summary", "status", "priority"]),
            cell(evidence),
        )
    console.print(table)
    return stream.getvalue()


def save(path: Path, content: str) -> None:
    """Publish a fully written file atomically, without replacing existing paths."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.link(temporary, path)
    except OSError as exc:
        raise UserError(
            "Cannot create report file; choose a new path "
            "in an existing writable directory."
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def emit(data: object, args: argparse.Namespace) -> None:
    if not isinstance(data, dict):
        raise UserError("Report data is invalid.")
    renderers = {"table": render_table, "csv": render_csv, "markdown": render_markdown}
    content = (
        json.dumps(data, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        if args.format == "json"
        else renderers[args.format](data)
    )
    if args.output is not None:
        save(args.output, content)
    elif not args.quiet:
        sys.stdout.write(content)
