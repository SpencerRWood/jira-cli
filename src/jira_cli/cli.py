"""PowerShell-friendly command entry point with bounded, secret-safe failures."""

import argparse
import json
import os
import sys
import webbrowser
from collections.abc import Sequence

from rich.console import Console
from rich.table import Table

from jira_cli import (
    epic_context,
    listing,
    personal_cli,
    presentation,
    report_cli,
    reporting,
    write_cli,
)
from jira_cli.client import JiraClient
from jira_cli.completion import powershell
from jira_cli.config import (
    Config,
    UserError,
    application_directory,
    load_config,
    project_key,
    read_pat,
    set_config,
)
from jira_cli.context import ContextStore, Selection
from jira_cli.views import IssueViews


def common(parser: argparse.ArgumentParser) -> None:
    for name in ("issue", "project"):
        parser.add_argument("--" + name, default=argparse.SUPPRESS)
    parser.add_argument(
        "--format",
        choices=["table", "json", "csv", "markdown"],
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Fail on missing values instead of opening interactive prompts.",
    )


def issue_arguments(leaf: argparse.ArgumentParser, action: str) -> None:
    if action in {"view", "open"}:
        leaf.add_argument("key", nargs="?")
    if action == "view":
        leaf.add_argument("--detail", action="store_true")
    if action in {"list", "select"}:
        listing.flags(leaf)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="jira",
        description=(
            "Local-first Jira Data Center CLI. "
            "Live checks require the corporate network."
        ),
    )
    common(root)
    commands = root.add_subparsers(dest="command", required=True)
    for name, subcommands in {
        "config": ["show", "set"],
        "context": ["show", "set", "clear", "use-project"],
        "auth": ["test"],
        "metadata": ["project", "fields", "transitions"],
        "issue": ["view", "list", "open", "select"],
        "story": ["view", "list", "select"],
    }.items():
        group = commands.add_parser(name)
        common(group)
        actions = group.add_subparsers(dest="action", required=True)
        for action in subcommands:
            leaf = actions.add_parser(action)
            common(leaf)
            if name == "config" and action == "set":
                leaf.add_argument("key")
                leaf.add_argument("value")
            if name == "context":
                leaf.add_argument("--global", dest="global_scope", action="store_true")
                if action in {"set", "use-project"}:
                    leaf.add_argument("key", nargs="?")
            if name == "metadata" and action in {"project", "transitions"}:
                leaf.add_argument("key", nargs="?")
            if name == "metadata" and action == "fields":
                leaf.add_argument("--type", dest="issue_type", default="Story")
            if name in {"issue", "story"}:
                issue_arguments(leaf, action)
                if name == "story" and action in {"list", "select"}:
                    epic_context.flags(leaf)
    for name in ["search", "doctor", "completion"]:
        leaf = commands.add_parser(name)
        common(leaf)
        if name == "search":
            leaf.add_argument("--jql", required=True)
        if name == "completion":
            leaf.add_argument("shell", choices=["powershell"])
    write_cli.add_parsers(commands, common)
    personal_cli.add_parsers(commands, common)
    report_cli.add_parsers(commands, common)
    return root


def completion() -> str:
    """Shell completion needs neither credentials nor network access."""
    return powershell(parser())


def redact(data: object, token: str) -> object:
    if isinstance(data, str):
        return data.replace(token, "[REDACTED]") if token else data
    if isinstance(data, dict):
        return {str(redact(k, token)): redact(v, token) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v, token) for v in data]
    return data


def emit(data: object, args: argparse.Namespace, token: str | None = None) -> None:
    if args.quiet:
        return
    if token is None:
        try:
            token = read_pat(application_directory()).get_secret_value()
        except UserError:
            token = ""
    encoded = json.dumps(redact(data, token), ensure_ascii=True, indent=2)
    if args.format == "json":
        sys.stdout.write(encoded + "\n")
        return
    safe = json.loads(encoded)
    if args.command in {"issue", "story", "epic"} and args.action == "list":
        presentation.issue_list(safe, args.command)
        return
    table = Table("Field", "Value")
    for key, value in safe.items() if isinstance(safe, dict) else [("result", safe)]:
        table.add_row(str(key), json.dumps(value, ensure_ascii=False))
    Console(markup=False, highlight=False).print(table)


def local(args: argparse.Namespace, store: ContextStore) -> object:
    if args.action == "show":
        return store.selection(global_scope=args.global_scope).model_dump()
    current = store.selection(global_scope=args.global_scope)
    if args.action == "set":
        current.issue = store.issue(args.issue or args.key)
    elif args.action == "use-project":
        if not (key := args.project or args.key):
            raise UserError("Provide a project key.")
        current.project = project_key(key)
    else:
        current = Selection()
    store.update(current, global_scope=args.global_scope)
    return {
        "context": current.model_dump(),
        "scope": "global" if args.global_scope else store.config.scope,
    }


def remote(
    args: argparse.Namespace,
    client: JiraClient,
    store: ContextStore,
    config: Config,
    project: str,
) -> object:
    if args.command == "auth":
        return {"user": client.object("myself"), "project": client.project(project)}
    if args.command == "doctor":
        server = client.object("serverInfo")
        if not server.get("version"):
            raise UserError("Jira server version is unavailable; verify the endpoint.")
        if server.get("deploymentType") not in {None, "Server", "Data Center"}:
            raise UserError("Reported deployment is not Jira Server/Data Center.")
        user = client.object("myself")
        accessible = client.project(project)
        return {
            "server": server,
            "authenticated_user": user.get("name"),
            "project": accessible.get("key"),
            "read_only_checks": "passed",
            "writes_require_preflight": True,
            "corporate_integration": (
                "Read-only connection observed; controlled write verification pending."
            ),
        }
    if args.command == "metadata":
        return metadata(args, client, store, project)
    if args.command == "search":
        return client.search(args.jql)
    return issues(args, client, store, config, project)


def metadata(
    args: argparse.Namespace, client: JiraClient, store: ContextStore, project: str
) -> object:
    if args.action == "project":
        return client.project(args.key or project)
    if args.action == "fields":
        return client.fields(project, args.issue_type)
    return client.object(
        "issue/" + store.issue(args.issue or args.key) + "/transitions",
        {"expand": "transitions.fields"},
    )


def issues(
    args: argparse.Namespace,
    client: JiraClient,
    store: ContextStore,
    config: Config,
    project: str,
) -> object:
    if args.action in {"list", "select"}:
        if args.action == "select":
            personal_cli.require_picker(args)
        presenter = IssueViews(client, config)
        epic = (
            epic_context.resolve(args, client, store, config)
            if args.command == "story"
            else None
        )
        epic_context.notice(epic, args)
        rows = listing.issues(args, presenter, project, epic=epic)
        return (
            personal_cli.select(rows, args, client, store)
            if args.action == "select"
            else [presenter.normalize(row) for row in rows]
        )
    key = store.issue(args.issue or args.key)
    if args.action == "open":
        url = client.url(key)
        if not webbrowser.open(url):
            raise UserError(
                "Browser did not open; use the issue URL from jira issue view."
            )
        return {"url": url}
    presenter = IssueViews(client, config)
    issue = presenter.get(key, "Story" if args.command == "story" else None)
    return presenter.normalize(issue, detail=args.detail, include_description=True)


def reports(
    args: argparse.Namespace, client: JiraClient, config: Config
) -> dict[str, object]:
    if args.issue:
        raise UserError("Reports use project scope; --issue is unsupported.")
    return reporting.build(
        client,
        config,
        reporting.Request(
            args.action,
            project_key(
                args.project
                or os.environ.get("JIRA_PROJECT")
                or config.reporting.default_project
            ),
            mine=args.mine,
            since=args.since,
        ),
    )


def run(args: argparse.Namespace) -> None:
    if args.command == "completion":
        if not args.quiet:
            sys.stdout.write(completion())
        return
    directory = application_directory()
    config = load_config(directory)
    args.format = args.format or config.output.format
    if args.command != "report" and args.format not in {"table", "json"}:
        raise UserError("CSV and Markdown formats require a report command.")
    store = ContextStore(directory, config)
    if args.command == "config":
        if args.action == "set":
            set_config(directory, args.key, args.value)
        emit(load_config(directory).model_dump(), args)
        return
    if args.command == "context":
        emit(local(args, store), args)
        return
    token = read_pat(directory)
    client = JiraClient(config, token)
    try:
        if args.command == "report":
            report_cli.emit(
                redact(reports(args, client, config), token.get_secret_value()), args
            )
            return
        project = project_key(
            args.project
            or os.environ.get("JIRA_PROJECT")
            or (store.selection().project if args.command != "my" else None)
            or config.defaults.project
        )
        result: object
        if args.command in write_cli.COMMANDS:
            result = write_cli.execute(args, client, store, config, project)
        elif args.command in {"epic", "my"}:
            result = personal_cli.execute(args, client, store, config, project)
        else:
            result = remote(args, client, store, config, project)
        emit(result, args, token.get_secret_value())
        if isinstance(result, dict) and result.get("success") is False:
            raise UserError(
                str(
                    result.get("watcher_warning")
                    or "Attachment operation incomplete; "
                    "inspect results before any retry."
                )
            )
    finally:
        client.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0: success; 1: local/network/API failure; 2: argument syntax."""
    args = parser().parse_args(
        argv,
        namespace=argparse.Namespace(
            issue=None, project=None, format=None, quiet=False
        ),
    )
    try:
        run(args)
    except UserError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    except OSError, UnicodeError:
        sys.stderr.write("Error: Cannot access application files or the browser.\n")
        return 1
    return 0
