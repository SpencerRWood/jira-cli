"""One YAML configuration and a fixed, application-owned credential location."""

import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


class UserError(Exception):
    """An actionable message safe for a terminal; never contains response bodies."""


def issue_key(value: str) -> str:
    """Accept Jira-managed keys, never paths or JQL fragments."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", value):
        raise UserError("Expected an issue key such as WWRAP-123.")
    return value


def project_key(value: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
        raise UserError("Expected a project key such as WWRAP.")
    return value


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Auth(Model):
    method: Literal["pat"] = "pat"
    # An environment variable name, never a credential value.
    token_env: Literal["JIRA_PAT"] = "JIRA_PAT"  # noqa: S105


class Jira(Model):
    base_url: str = ""
    deployment: Literal["data_center"] = "data_center"
    auth: Auth = Field(default_factory=Auth)


class Defaults(Model):
    project: str = "WWRAP"
    issue_type: str = "Story"


class Creation(Model):
    set_context_after_create: bool = True


class Context(Model):
    persist: bool = True
    scope: Literal["directory", "global"] = "directory"
    fallback_to_global: bool = True


class Epics(Model):
    default_filter: Literal["relevant"] = "relevant"
    include_assigned_open: bool = True
    include_with_my_active_stories: bool = True
    include_completed_epics_with_active_stories: bool = True


class Reporting(Model):
    default_project: str = "WWRAP"
    aging_threshold_days: int = Field(default=14, gt=0)
    weekly_lookback_days: int = Field(default=7, gt=0)


class Output(Model):
    format: Literal["table", "json"] = "table"
    show_issue_urls: bool = True


class Config(Model):
    jira: Jira = Field(default_factory=Jira)
    defaults: Defaults = Field(default_factory=Defaults)
    creation: Creation = Field(default_factory=Creation)
    context: Context = Field(default_factory=Context)
    epics: Epics = Field(default_factory=Epics)
    reporting: Reporting = Field(default_factory=Reporting)
    output: Output = Field(default_factory=Output)


def application_directory() -> Path:
    """APPDATA is the Windows contract; Unix paths allow offline development."""
    if root := os.environ.get("APPDATA"):
        return Path(root) / "jira-cli"
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "jira-cli"
    )


def load_config(directory: Path) -> Config:
    path = directory / "config.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        config = Config.model_validate(data if data is not None else {})
        # Explicit environment overrides never load files relative to the cwd.
        updates = config.model_dump()
        if override_url := os.environ.get("JIRA_BASE_URL"):
            updates["jira"]["base_url"] = override_url
        if project := os.environ.get("JIRA_PROJECT"):
            updates["defaults"]["project"] = project
        config = Config.model_validate(updates)
        project_key(config.defaults.project)
        if config.jira.base_url:
            base_url(config)
        return config
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise UserError(
            "Cannot read config.yaml; check YAML and supported fields."
        ) from exc


def read_pat(directory: Path) -> SecretStr:
    try:
        # No cwd discovery, interpolation, mutation of os.environ, or token logging.
        value = os.environ.get("JIRA_PAT") or dotenv_values(
            directory / ".env", interpolate=False
        ).get("JIRA_PAT")
    except (OSError, UnicodeError) as exc:
        raise UserError("Cannot read the application .env credential file.") from exc
    if not value or not value.strip() or any(c.isspace() for c in value):
        raise UserError("Set JIRA_PAT in the application .env or process environment.")
    return SecretStr(value)


def base_url(config: Config) -> str:
    value = config.jira.base_url.rstrip("/")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise UserError("Set jira.base_url to a valid HTTPS Jira URL.") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise UserError(
            "Set jira.base_url to an HTTPS Jira URL without credentials or query."
        )
    return value


def set_config(directory: Path, key: str, value: str) -> None:
    """Update only a supported YAML key, keeping process overrides out of the file."""
    path = directory / "config.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        config = Config.model_validate(data if data is not None else {})
        parts = key.split(".")
        updated = config.model_dump()
        target = updated
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                raise UserError("Unknown configuration key; inspect jira config show.")
            target = target[part]
        if parts[-1] not in target or isinstance(target[parts[-1]], dict):
            raise UserError("Set a supported scalar configuration key.")
        target[parts[-1]] = yaml.safe_load(value)
        checked = Config.model_validate(updated)
        project_key(checked.defaults.project)
        if checked.jira.base_url:
            base_url(checked)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            yaml.safe_dump(checked.model_dump(), sort_keys=False), encoding="utf-8"
        )
        temporary.replace(path)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise UserError(
            "Invalid configuration value or unwritable config.yaml."
        ) from exc
