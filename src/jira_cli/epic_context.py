"""Verified Epic scoping for Story commands, without changing saved context."""

import argparse

from rich.console import Console

from jira_cli.client import JiraClient
from jira_cli.config import Config, UserError, issue_key
from jira_cli.context import ContextStore
from jira_cli.presentation import linked
from jira_cli.views import IssueViews, named


def flags(parser: argparse.ArgumentParser) -> None:
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--epic", help="Use this Epic instead of active context.")
    scope.add_argument(
        "--no-epic", action="store_true", help="Ignore Epic context for this command."
    )


def resolve(
    args: argparse.Namespace, client: JiraClient, store: ContextStore, config: Config
) -> str | None:
    if args.no_epic:
        return None
    explicit = args.epic
    key = explicit or store.selection().issue
    if not key:
        return None
    presenter = IssueViews(client, config)
    row = presenter.get(issue_key(key), "Epic" if explicit else None)
    kind = named(presenter.fields(row).get("issuetype"))
    if not kind:
        raise UserError(
            "Active context issue type is unavailable; cannot scope Stories."
        )
    return issue_key(key) if kind.casefold() == "epic" else None


def notice(
    epic: str | None, args: argparse.Namespace, *, jira_url: str | None = None
) -> None:
    if epic and args.format == "table" and not args.quiet:
        origin = "Epic override" if args.epic else "Epic context"
        Console(stderr=True, markup=False, highlight=False).print(
            linked(
                f"{origin}: {issue_key(epic)} — Stories scoped to this Epic.", jira_url
            )
        )
