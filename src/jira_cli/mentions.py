"""Resolve Data Center usernames before producing wiki mention markup."""

import json
import re

from jira_cli.client import JiraClient
from jira_cli.config import UserError
from jira_cli.descriptions import CODE

USERNAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
INLINE = re.compile(r"(?<![\w@./:+-])@([A-Za-z0-9_][A-Za-z0-9_.-]*)(?![\w@-])")
EXISTING = re.compile(r"\[~([^\]\n]+)\]")


def resolve(client: JiraClient, reference: str) -> str:
    if not USERNAME.fullmatch(reference):
        raise UserError("Invalid mention username " + json.dumps(reference) + ".")
    start = 0
    matches: dict[str, str] = {}
    previous: list[object] | None = None
    while True:
        try:
            users = client.get(
                "user/search",
                {
                    "username": reference,
                    "startAt": start,
                    "maxResults": 100,
                    "includeActive": "true",
                    "includeInactive": "false",
                },
            )
        except UserError as exc:
            raise UserError(
                f"Cannot resolve @{reference}: {exc} No comment submitted; "
                "check Browse users permission and retry the original comment."
            ) from exc
        if not isinstance(users, list) or any(not isinstance(u, dict) for u in users):
            raise UserError(
                f"Cannot resolve @{reference}: invalid user search response."
            )
        if users and users == previous:
            raise UserError(
                f"Cannot resolve @{reference}: user search pagination repeated."
            )
        for user in users:
            name = user.get("name")
            if (
                isinstance(name, str)
                and name.casefold() == reference.casefold()
                and user.get("active") is True
            ):
                matches[str(user.get("key") or name)] = name
        if len(users) < 100:
            break
        start += len(users)
        previous = users
    if len(matches) != 1:
        reason = "unknown or inactive username" if not matches else "ambiguous username"
        raise UserError(
            f"Cannot resolve @{reference}: {reason}. No comment submitted; "
            "verify the exact Jira username and retry the original comment."
        )
    name = next(iter(matches.values()))
    if not USERNAME.fullmatch(name):
        raise UserError(
            f"Cannot resolve @{reference}: unsupported Jira username markup."
        )
    return name


def prepare(
    client: JiraClient, body: str, explicit: tuple[str, ...]
) -> tuple[str, list[str]]:
    parts = CODE.split(body)
    references = dict.fromkeys(name.casefold() for name in explicit)
    for index, part in enumerate(parts):
        if not index % 2:
            for match in INLINE.finditer(EXISTING.sub("", part)):
                # A terminal period is punctuation, never part of inline usernames.
                references.setdefault(match[1].rstrip(".").casefold(), None)
    resolved = {name: resolve(client, name) for name in references}
    present: set[str] = set()
    for index, part in enumerate(parts):
        if index % 2:
            continue
        present.update(m[1].casefold() for m in EXISTING.finditer(part))

        def replace(match: re.Match[str]) -> str:
            reference = match[1].rstrip(".")
            name = resolved[reference.casefold()]
            present.add(name.casefold())
            return "[~" + name + "]" + match[1][len(reference) :]

        # Existing wiki markup is opaque, including unusual names containing @.
        spans = re.split(r"(\[~[^\]\n]+\])", part)
        parts[index] = "".join(
            span if number % 2 else INLINE.sub(replace, span)
            for number, span in enumerate(spans)
        )
    result = "".join(parts)
    append = []
    for name in resolved.values():
        if name.casefold() not in present:
            append.append("[~" + name + "]")
            present.add(name.casefold())
    if append:
        result += "\n" + " ".join(append)
    return result, list(dict.fromkeys(resolved.values()))
