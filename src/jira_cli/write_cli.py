"""Mutation command arguments, explicit input files, and safe interactive choices."""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from jira_cli import epic_context
from jira_cli.client import JiraClient
from jira_cli.config import Config, UserError
from jira_cli.context import ContextStore
from jira_cli.mutations import MISSING, Creation, Mutations

COMMANDS = {"create", "comment", "attach", "set", "assign", "watcher"}


def add_parsers(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    flags: Callable[[argparse.ArgumentParser], None],
) -> None:
    for name, actions in {
        "create": ["story", "epic"],
        "comment": ["add"],
        "watcher": ["add"],
        "set": ["status", "field", "epic"],
    }.items():
        group = commands.add_parser(name)
        flags(group)
        sub = group.add_subparsers(dest="action", required=True)
        for action in actions:
            leaf = sub.add_parser(action)
            flags(leaf)
            if name == "create":
                for option in ["summary", "priority", "assignee"]:
                    leaf.add_argument("--" + option)
                if action == "story":
                    epic_context.flags(leaf)
                else:
                    leaf.add_argument("--epic")
                    leaf.add_argument("--epic-name")
                description = leaf.add_mutually_exclusive_group()
                description.add_argument("--description")
                description.add_argument("--description-file", type=Path)
                leaf.add_argument("--from", dest="input_file", type=Path)
                leaf.add_argument("--field", action="append", default=[])
                leaf.add_argument("--no-context", action="store_true")
                leaf.add_argument(
                    "--watchers",
                    type=watcher_names,
                    default=(),
                    help="Comma-separated Jira usernames to add as watchers.",
                )
            elif name == "watcher":
                leaf.add_argument("--watchers", type=watcher_names, required=True)
            elif name == "comment":
                leaf.add_argument("text", nargs="?")
                leaf.add_argument("--file", type=Path)
                leaf.add_argument(
                    "--mention",
                    action="append",
                    default=[],
                    metavar="USERNAME",
                    help="Resolve a Jira username; repeat for multiple mentions.",
                )
            elif action == "field":
                leaf.add_argument("field", nargs="?")
                leaf.add_argument("value", nargs="?", default=MISSING)
                leaf.add_argument("--from-file", type=Path)
                leaf.add_argument("--clear", action="store_true")
            else:
                leaf.add_argument("value", nargs="?")
                if action == "epic":
                    leaf.add_argument("--clear", action="store_true")
                else:
                    leaf.add_argument("--field", action="append", default=[])
    attach = commands.add_parser("attach")
    flags(attach)
    attach.add_argument("files", nargs="+", type=Path)
    assign = commands.add_parser("assign", help="Assign an issue to yourself.")
    flags(assign)


def watcher_names(value: str) -> tuple[str, ...]:
    names = [name.strip() for name in value.split(",")]
    if any(not name for name in names) or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise argparse.ArgumentTypeError(
            "Use non-empty, comma-separated usernames without control characters."
        )
    # Jira usernames are case-insensitive; preserve the first supplied spelling.
    unique: dict[str, str] = {}
    for name in names:
        unique.setdefault(name.casefold(), name)
    return tuple(unique.values())


def assignments(items: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        name, separator, value = item.partition("=")
        if not separator or not name or name in result:
            raise UserError("Use unique --field NAME=VALUE assignments.")
        result[name] = value
    return result


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise UserError(
            "Cannot read the explicitly selected UTF-8 input file."
        ) from exc


def creation_values(args: argparse.Namespace) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if args.input_file:
        try:
            loaded = yaml.safe_load(read_text(args.input_file))
        except yaml.YAMLError as exc:
            raise UserError("The selected creation file is not valid YAML.") from exc
        if not isinstance(loaded, dict) or any(not isinstance(k, str) for k in loaded):
            raise UserError(
                "Creation input must be one mapping of field names/IDs to values."
            )
        values.update(loaded)
    values.update(assignments(args.field))
    if getattr(args, "epic_name", None) is not None:
        values["epic_name"] = args.epic_name
    for name in ["summary", "priority", "assignee"]:
        if value := getattr(args, name):
            values[name] = value
    if args.description_file:
        values["description"] = read_text(args.description_file)
    elif args.description is not None:
        values["description"] = args.description
    return values


class Prompt:
    def __init__(self, args: argparse.Namespace, client: JiraClient) -> None:
        self.interactive = sys.stdin.isatty() and not getattr(
            args, "non_interactive", False
        )
        self.token = client.token.get_secret_value()

    def ask(self, label: str, choices: list[str]) -> str:
        if not self.interactive:
            raise UserError(
                "A required value is missing for "
                + json.dumps(label).replace(json.dumps(self.token)[1:-1], "[REDACTED]")
                + "; provide arguments or --field NAME=VALUE. No prompt was opened."
            )
        # JSON escaping keeps server labels from injecting terminal control sequences.
        prompt = json.dumps({"field": label, "choices": choices}, ensure_ascii=True)
        prompt = prompt.replace(json.dumps(self.token)[1:-1], "[REDACTED]")
        sys.stderr.write(prompt + "\nValue: ")
        try:
            value = input()
        except (EOFError, KeyboardInterrupt) as exc:
            raise UserError(
                "Command cancelled; no further Jira writes were attempted."
            ) from exc
        if not value.strip():
            raise UserError("A required value was empty; no Jira write was attempted.")
        return value


def execute(
    args: argparse.Namespace,
    client: JiraClient,
    store: ContextStore,
    config: Config,
    project: str,
) -> dict[str, Any]:
    service = Mutations(client, store, config, Prompt(args, client).ask)
    if args.command == "create":
        if args.issue:
            raise UserError("Creation cannot accept an issue key; Jira assigns it.")
        epic = (
            epic_context.resolve(args, client, store, config)
            if args.action == "story"
            else args.epic
        )
        if args.action == "story":
            epic_context.notice(
                epic,
                args,
                jira_url=client.base_url.replace(
                    client.token.get_secret_value(), "[REDACTED]"
                ),
            )
        return service.create(
            Creation(
                args.action.title(),
                epic.rsplit("-", 1)[0]
                if epic and not args.epic and not args.project
                else project,
                creation_values(args),
                epic,
                not args.no_context,
                args.watchers,
            )
        )
    issue = store.issue(args.issue)
    if args.command == "comment":
        if args.text is not None and args.file:
            raise UserError("Choose inline comment text or --file, not both.")
        body = read_text(args.file) if args.file else args.text
        return service.comment(
            issue,
            body if body is not None else service.ask("Comment text", []),
            tuple(args.mention),
        )
    if args.command == "assign":
        return service.assign_self(issue)
    if args.command == "watcher":
        return service.watch_existing(issue, args.watchers)
    if args.command == "attach":
        return service.attach(issue, args.files)
    return set_action(args, service, issue)


def set_action(
    args: argparse.Namespace, service: Mutations, issue: str
) -> dict[str, Any]:
    if args.action == "status":
        return service.transition(issue, args.value, assignments(args.field))
    if args.action == "epic":
        if args.clear and args.value:
            raise UserError("Choose an epic key or --clear, not both.")
        return service.set_epic(issue, args.value, clear=args.clear)
    if sum([args.value is not MISSING, bool(args.from_file), args.clear]) > 1:
        raise UserError("Choose one field value, --from-file, or --clear.")
    value = (
        None
        if args.clear
        else read_text(args.from_file)
        if args.from_file
        else args.value
    )
    return service.set_field(issue, args.field, value)
