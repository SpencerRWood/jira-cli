"""Compact terminal lists; callers retain the full normalized JSON schema."""

import json
from typing import Any

from rich.console import Console
from rich.table import Table

from jira_cli.config import UserError


def cell(value: object) -> str:
    if value is None or value == "":
        return "—"
    encoded = json.dumps(value, ensure_ascii=False)
    # Preserve readable Unicode while escaping controls and embedded newlines.
    return encoded[1:-1] if isinstance(value, str) else encoded


def issue_list(data: object, command: str) -> None:
    console = Console(markup=False, highlight=False)
    epics = command == "epic"
    rows = data.get("epics") if epics and isinstance(data, dict) else data
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise UserError("Cannot present invalid issue list data.")
    if epics and isinstance(data, dict):
        console.print(
            "Project: "
            + cell(data.get("scope"))
            + "  Filter: "
            + cell(data.get("filter"))
        )
    if not rows:
        console.print("No matching issues.")
        return
    title = {"issue": "Issues", "story": "Stories", "epic": "Epics"}[command]
    table = Table(title=f"{title} ({len(rows)})", show_lines=True)
    table.add_column("Key", no_wrap=True)
    table.add_column("Summary", ratio=3, overflow="fold")
    table.add_column("Status", overflow="fold")
    table.add_column("Priority", overflow="fold")
    table.add_column("Assignee", overflow="fold")
    if epics:
        table.add_column("My active\nStories", justify="right")
        table.add_column("Attention", overflow="fold")
    else:
        table.add_column("Epic", no_wrap=True)
    for row in rows:
        table.add_row(*row_cells(row, epics=epics))
    console.print(table)


def row_cells(row: dict[str, Any], *, epics: bool) -> list[str]:
    assignee = row.get("assignee")
    name = assignee.get("name") if isinstance(assignee, dict) else None
    values = [cell(row.get(key)) for key in ["key", "summary", "status", "priority"]]
    values.append(cell(name))
    if epics:
        values.extend(
            [
                cell(row.get("my_active_story_count")),
                "Completed; active Stories"
                if row.get("completed_with_active_personal_stories")
                else "—",
            ]
        )
    else:
        values.append(cell(row.get("epic")))
    return values
