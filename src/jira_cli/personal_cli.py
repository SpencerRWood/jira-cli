"""Personal workflow arguments and explicit local context pickers."""

import argparse
import sys
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.table import Table

from jira_cli import personal
from jira_cli.client import JiraClient
from jira_cli.config import Config, UserError, issue_key
from jira_cli.context import ContextStore
from jira_cli.presentation import cell
from jira_cli.views import IssueViews


def add_parsers(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    flags: Callable[[argparse.ArgumentParser], None],
) -> None:
    group = commands.add_parser(
        "epic", help="Discover relevant epics and personal Stories."
    )
    flags(group)
    actions = group.add_subparsers(dest="action", required=True)
    for action in ["list", "view", "select"]:
        leaf = actions.add_parser(action)
        flags(leaf)
        mode = leaf.add_mutually_exclusive_group()
        mode.add_argument("--mine", action="store_true")
        if action == "view":
            leaf.add_argument("key", nargs="?")
            mode.add_argument("--all-stories", action="store_true")
            leaf.add_argument("--detail", action="store_true")
        else:
            mode.add_argument("--with-my-stories", action="store_true")
            mode.add_argument("--all", action="store_true")
            leaf.add_argument("--include-done", action="store_true")
    group = commands.add_parser("my", help="Personal work across accessible projects.")
    flags(group)
    actions = group.add_subparsers(dest="action", required=True)
    today = actions.add_parser("today")
    flags(today)
    today.add_argument(
        "--sort",
        choices=["due-date", "priority", "status", "staleness"],
        default="staleness",
        help="Sort by staleness (oldest update first) by default.",
    )


def require_picker(args: argparse.Namespace) -> None:
    if (
        getattr(args, "non_interactive", False)
        or args.quiet
        or args.format == "json"
        or not sys.stdin.isatty()
    ):
        raise UserError(
            "Selection requires an interactive terminal with table output; "
            "use an explicit key or jira context set KEY for scripts."
        )


def select(
    rows: list[dict[str, Any]],
    args: argparse.Namespace,
    client: JiraClient,
    store: ContextStore,
) -> dict[str, Any]:
    require_picker(args)
    if not store.config.persist:
        raise UserError("Context persistence is disabled in config.yaml.")
    if not rows:
        raise UserError("No accessible matching issues are available for selection.")
    token = client.token.get_secret_value()
    table = Table(title="Select an issue", show_lines=True)
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Key", no_wrap=True)
    table.add_column("Summary", ratio=3, overflow="fold")
    table.add_column("Status", overflow="fold")
    for number, row in enumerate(rows, start=1):
        data = personal.fields(row)
        status = data.get("status")
        values = [
            str(number),
            row["key"],
            data.get("summary"),
            status.get("name") if isinstance(status, dict) else None,
        ]
        table.add_row(
            *(
                cell(
                    value.replace(token, "[REDACTED]")
                    if isinstance(value, str) and token
                    else value
                )
                for value in values
            )
        )
    Console(stderr=True, markup=False, highlight=False).print(table)
    sys.stderr.write("Select a number (blank cancels): ")
    try:
        answer = input().strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise UserError("Selection cancelled; context unchanged.") from exc
    if not answer:
        raise UserError("Selection cancelled; context unchanged.")
    if (
        not answer.isascii()
        or not answer.isdecimal()
        or len(answer) > len(str(len(rows)))
        or not 1 <= int(answer) <= len(rows)
    ):
        raise UserError("Invalid selection; context unchanged.")
    key = issue_key(rows[int(answer) - 1]["key"])
    current = store.selection()
    current.issue = key
    store.update(current)
    return {"selected_issue": key, "url": client.url(key), "jira_writes": False}


def execute(
    args: argparse.Namespace,
    client: JiraClient,
    store: ContextStore,
    config: Config,
    project: str,
) -> object:
    if args.command == "my":
        # My Today deliberately ignores default/env/saved project selection.
        result = personal.my_today(client, config, args.project, args.sort)
        presenter = IssueViews(client, config)
        result["issues"] = [
            {
                **presenter.normalize(row, include_latest_comment=True),
                "due_date": personal.fields(row).get("duedate"),
                "updated": personal.fields(row).get("updated"),
                "blockers": row["blockers"],
                "inferred_risks": row["inferred_risks"],
            }
            for row in result["issues"]
        ]
        return result
    if args.action == "view":
        return personal.epic_view(
            client,
            store.issue(args.issue or args.key),
            config,
            all_stories=args.all_stories,
            detail=args.detail,
        )
    if args.action == "select":
        require_picker(args)
    mode = (
        "mine"
        if args.mine
        else "with-my-stories"
        if args.with_my_stories
        else "all"
        if args.all
        else config.epics.default_filter
    )
    rows = personal.epic_list(
        client, config, project, mode, include_done=args.include_done
    )
    if args.action == "select":
        return select(rows, args, client, store)
    presenter = IssueViews(client, config)
    return {
        "scope": project,
        "filter": mode,
        "epics": [
            {
                **presenter.normalize(row),
                "my_active_story_count": row["my_active_story_count"],
                "completed_with_active_personal_stories": row[
                    "completed_with_active_personal_stories"
                ],
            }
            for row in rows
        ],
    }
