"""Compact terminal lists; callers retain the full normalized JSON schema."""

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit

from rich.console import Console
from rich.style import Style
from rich.table import Table
from rich.text import Text

from jira_cli.config import UserError
from jira_cli.descriptions import description

KEY_PATTERN = re.compile(r"(?<![\w-])[A-Z][A-Z0-9_]*-[1-9][0-9]*(?![\w-])")


def linked(value: str, jira_url: str | None = None) -> Text:
    """Link visible keys to the configured Jira without interpreting markup."""
    value = text(value)
    result = Text(value)
    if not jira_url or any(
        ord(char) < 33 or 127 <= ord(char) <= 159 for char in jira_url
    ):
        return result
    try:
        parsed = urlsplit(jira_url)
        _ = parsed.port
    except ValueError:
        return result
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return result
    base = jira_url.rstrip("/")
    for match in KEY_PATTERN.finditer(value):
        target = base + "/browse/" + quote(match[0], safe="")
        result.stylize(Style(link=target, underline=True), match.start(), match.end())
    return result


def cell(value: object) -> str:
    if value is None or value == "":
        return "—"
    encoded = json.dumps(value, ensure_ascii=False)
    # Preserve readable Unicode while escaping controls and embedded newlines.
    return encoded[1:-1] if isinstance(value, str) else encoded


def issue_list(data: object, command: str, *, jira_url: str | None = None) -> None:
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
    if epics:
        table.add_column("Epic Name", overflow="fold")
    table.add_column("Summary", ratio=3, overflow="fold")
    if command == "story":
        table.add_column("Epic Key", no_wrap=True)
        table.add_column("Epic Name", overflow="fold")
    table.add_column("Status", overflow="fold")
    table.add_column("Priority", overflow="fold")
    table.add_column("Assignee", overflow="fold")
    if epics:
        table.add_column("My Active Stories", justify="right")
    elif command != "story":
        table.add_column("Epic", no_wrap=True)
    for row in rows:
        table.add_row(
            *(
                linked(value, jira_url)
                for value in row_cells(row, epics=epics, stories=command == "story")
            )
        )
    console.print(table)


def row_cells(row: dict[str, Any], *, epics: bool, stories: bool = False) -> list[str]:
    assignee = row.get("assignee")
    name = assignee.get("name") if isinstance(assignee, dict) else None
    keys = ["key", "epic_name", "summary"] if epics else ["key", "summary"]
    if stories:
        keys.extend(["epic_key", "epic_name"])
    keys.extend(["status", "priority"])
    values = [cell(row.get(key)) for key in keys]
    values.append(cell(name))
    if epics:
        values.append(cell(row.get("my_active_story_count")))
    elif not stories:
        values.append(cell(row.get("epic")))
    return values


def my_today(data: dict[str, Any], *, jira_url: str | None = None) -> None:
    console = Console(markup=False, highlight=False)
    console.print(
        "Scope: " + cell(data.get("scope")) + "  Sort: " + cell(data.get("sort"))
    )
    rows = data["issues"]
    if not rows:
        console.print("No matching issues.")
    limit = data.get("limit")
    title = (
        f"MY TODAY — TOP {limit} PRIORITIES" if limit else "MY TODAY — ALL PRIORITIES"
    )
    table = Table(title=title, show_lines=True)
    table.add_column("Key", no_wrap=True)
    table.add_column("Summary", ratio=3, overflow="fold")
    for label in ["Status", "Priority", "Due Date", "Updated"]:
        table.add_column(label, overflow="fold")
    table.add_column("Latest Comment", ratio=2, overflow="fold")
    table.add_column("Attention", overflow="fold")
    for row in rows:
        notes = []
        if blockers := row.get("blockers"):
            notes.append("Blocked by: " + ", ".join(blockers))
        notes.extend("Inferred: " + risk for risk in row.get("inferred_risks", []))
        values = [
            cell(row.get("key")),
            text(row.get("summary")),
            cell(row.get("status")),
            cell(row.get("priority")),
            cell(row.get("due_date")),
            timestamp(row.get("updated")),
            comment_text(row.get("latest_comment")),
            text("\n".join(notes)),
        ]
        table.add_row(*(linked(value, jira_url) for value in values))
    console.print(table)
    console.print(
        f"Showing {len(rows)} of {data.get('actionable_total', len(rows))} "
        "actionable stories"
    )
    if data.get("show_blocked", True):
        blocked = data.get("blocked", [])
        blocked_table = Table(
            "Key",
            "Summary",
            "Priority",
            "Blocked Since",
            title="BLOCKED STORIES",
            show_lines=True,
        )
        for row in blocked:
            blocked_table.add_row(
                *(
                    linked(text(row.get(key)), jira_url)
                    for key in ["key", "summary", "priority", "blocked_since"]
                )
            )
        console.print(blocked_table if blocked else "No blocked stories")


def text(value: object) -> str:
    """Keep line breaks while escaping other terminal control characters."""
    if not isinstance(value, str) or not value:
        return cell(value)
    return "".join(
        char
        if char == "\n" or (ord(char) >= 32 and not 127 <= ord(char) <= 159)
        else json.dumps(char)[1:-1]
        for char in value
    )


def timestamp(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M %z")
    except ValueError:
        return cell(value)


def person(value: object) -> object:
    return value.get("name") if isinstance(value, dict) else None


def comment_text(value: object) -> str:
    if not isinstance(value, dict):
        return "—"
    return (
        cell(person(value.get("author")))
        + " · "
        + timestamp(value.get("created"))
        + "\n"
        + text(value.get("body"))
    )


def field_table(
    rows: list[tuple[str, object]], *, jira_url: str | None = None
) -> Table:
    table = Table("Field", "Value", show_lines=True)
    table.columns[0].no_wrap = True
    table.columns[1].ratio = 1
    table.columns[1].overflow = "fold"
    for label, value in rows:
        table.add_row(label, linked(text(value), jira_url))
    return table


def context_view(data: dict[str, Any], *, jira_url: str | None = None) -> None:
    keys = [
        ("Project", "project"),
        ("Issue", "issue"),
        ("Summary", "summary"),
        ("Status", "status"),
        ("Epic Key", "epic_key"),
        ("Epic Name", "epic_name"),
    ]
    rows = [(label, data.get(key)) for label, key in keys]
    if data.get("warning"):
        rows.append(("Warning", data["warning"]))
    Console(markup=False, highlight=False).print(field_table(rows, jira_url=jira_url))


def issue_view(
    data: dict[str, Any], *, detail: bool, jira_url: str | None = None
) -> None:
    rows = [
        (label, data.get(key))
        for label, key in [("Key", "key"), ("URL", "url"), ("Summary", "summary")]
    ]
    if not detail:
        rows.append(("Description", description(data.get("description"))))
    if data.get("type") == "Epic":
        rows.append(("Epic Name", data.get("epic_name")))
    else:
        rows.extend(
            [
                ("Parent Epic Key", data.get("epic_key")),
                ("Parent Epic Name", data.get("epic_name")),
            ]
        )
    rows.extend(
        [
            ("Priority", data.get("priority")),
            ("Status", data.get("status")),
            ("Assignee", person(data.get("assignee"))),
        ]
    )
    console = Console(markup=False, highlight=False)
    rows.append(("Latest Comment", comment_text(data.get("latest_comment"))))
    if detail:
        rows.extend(detail_rows(data))
    console.print(field_table(rows, jira_url=jira_url))
    if detail:
        comment_table = Table("Author", "Timestamp", "Comment", show_lines=True)
        comment_table.columns[2].ratio = 2
        comment_table.columns[2].overflow = "fold"
        for comment in data.get("comments", []):
            comment_table.add_row(
                cell(person(comment.get("author"))),
                timestamp(comment.get("created")),
                linked(text(comment.get("body")), jira_url),
            )
        console.print(comment_table if comment_table.row_count else "Comments: —")
        console.print(
            "Comments shown newest first; limit: " + cell(data.get("comment_limit"))
        )
        if data.get("type") == "Epic":
            children = [
                row
                for group in data.get("groups", [])
                for row in group.get("stories", [])
            ]
            console.print("Child Stories (" + cell(data.get("story_scope")) + ")")
            issue_list(children, "story", jira_url=jira_url)


def detail_rows(data: dict[str, Any]) -> list[tuple[str, object]]:
    dates = data.get("dates") or {}
    attachments = ", ".join(
        str(row["filename"])
        for row in data.get("attachments", [])
        if row.get("filename")
    )
    related = ", ".join(
        str(row["key"]) for row in data.get("related_issues", []) if row.get("key")
    )
    return [
        ("Description", description(data.get("description"))),
        ("Attachments", attachments),
        ("Related Issues", related),
        ("Created", timestamp(dates.get("created"))),
        ("Updated", timestamp(dates.get("updated"))),
    ]
