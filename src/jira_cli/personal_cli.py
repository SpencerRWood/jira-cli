"""Personal workflow arguments and explicit local context pickers."""

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from jira_cli import personal
from jira_cli.client import JiraClient
from jira_cli.config import Config, UserError, issue_key
from jira_cli.context import ContextStore


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
        else:
            mode.add_argument("--with-my-stories", action="store_true")
            mode.add_argument("--all", action="store_true")
    group = commands.add_parser("my", help="Personal work across accessible projects.")
    flags(group)
    actions = group.add_subparsers(dest="action", required=True)
    today = actions.add_parser("today")
    flags(today)
    today.add_argument(
        "--sort",
        choices=["due-date", "priority", "status", "staleness"],
        default="due-date",
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
    for number, row in enumerate(rows, start=1):
        data = personal.fields(row)
        label = json.dumps(
            {
                "key": row["key"],
                "summary": data.get("summary"),
                "completed_with_active_personal_stories": row.get(
                    "completed_with_active_personal_stories", False
                ),
            },
            ensure_ascii=True,
        ).replace(json.dumps(token, ensure_ascii=True)[1:-1], "[REDACTED]")
        sys.stderr.write(f"{number}: {label}\n")
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
        return personal.my_today(client, config, args.project, args.sort)
    if args.action == "view":
        return personal.epic_view(
            client, store.issue(args.issue or args.key), all_stories=args.all_stories
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
        else "relevant"
    )
    rows = personal.epic_list(client, config, project, mode)
    if args.action == "select":
        return select(rows, args, client, store)
    return {"scope": project, "filter": mode, "epics": rows}
