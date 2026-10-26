# Jira CLI

A local-first Windows CLI for Jira Data Center. S01 provides configuration,
working context, read-only diagnostics, metadata, and issue discovery. Issue writes,
personal workflows, reports, and the complete release handoff follow in S02–S05.

## Install and configure

Use Python 3.14 and an approved uv installation. From this checkout:

```powershell
uv build
uv tool install (Get-ChildItem dist/*.whl).FullName
uv tool update-shell
jira --help
```

Open a new PowerShell terminal if PATH changed. Use the same command in VS Code's
PowerShell terminal. This builds locally; it does not publish a release.

The single configuration file is `%APPDATA%\jira-cli\config.yaml`. Copy
`examples/config.yaml` there and replace its placeholder URL with the authorized
corporate Jira URL. No configuration is discovered from the current directory.

Store `JIRA_PAT` in `%APPDATA%\jira-cli\.env` only when corporate policy allows
plaintext local storage. Restrict that directory and file to your Windows account
using approved corporate ACL procedures. Never commit credentials, upload corporate
data, or paste a PAT into command arguments. A process-level `JIRA_PAT` overrides
the application credential file. `.env` interpolation and cwd discovery are disabled.
The CLI preserves TLS verification and HTTPX's enterprise certificate/proxy settings;
redirects are not followed and credentials are not forwarded to another origin.

`JIRA_BASE_URL` and `JIRA_PROJECT` are explicit process overrides. They do not get
written into YAML by `config set`. On Unix development hosts the equivalent app
directory is `$XDG_CONFIG_HOME/jira-cli` or `~/.config/jira-cli`. It is a platform
location rule, with no legacy configuration search or fallback loader.

## Foundation commands

```powershell
jira config show
jira config set defaults.project WWRAP
jira context set WWRAP-123
jira context show
jira context set WWRAP-456 --global
jira context use-project WWRAP
jira context clear
jira issue view --issue WWRAP-123 --detail --format json
jira issue list --project WWRAP --format json
jira search --jql 'project = WWRAP AND assignee = currentUser()' --format json
jira issue open WWRAP-123
jira metadata project WWRAP
jira metadata fields --type Story
jira metadata transitions WWRAP-123
jira auth test
jira doctor
```

Only run network commands inside an authorized network against an approved Jira
instance. `auth test` checks the current user and selected project. `doctor` also
reports server/version information. Neither enables writes nor attests that
corporate controlled-write verification has passed. Metadata uses the Data Center
REST v2 paginated creation-metadata endpoints; unsupported deployments fail clearly
rather than silently switching to old or Cloud endpoints.

Context lives in application `state.json`, outside YAML and working repositories.
Explicit `--issue` overrides directory context, then global context; it never alters
saved selection. Reads never change context. `context clear` clears the chosen scope;
clearing a directory selection allows configured global fallback to apply. Use
`--global` to clear global context. Project selection uses `--project`, `JIRA_PROJECT`,
directory or global context, then YAML's WWRAP default. Directory keys are absolute
and normalized for the platform.

Issue views default to compact fields; `--detail` includes accessible metadata,
description, comments, attachments, links, and configured custom fields. Search
consumes all reported pages. Arbitrary `search --jql` is used exactly as provided;
`issue list` applies the selected project. Jira permission errors remain authoritative.
All Foundation operations against Jira are GET requests, with no automatic retries.

Common flags can appear before or after subcommands: `--issue`, `--project`,
`--format table|json`, `--quiet`, and `--non-interactive`. Foundation commands never
prompt. Exit codes are 0 for success, 1 for local/network/API failures, and 2 for
argument syntax errors. Errors use stderr; `--quiet` suppresses successful stdout.
PAT values are redacted from terminal data, and errors omit response bodies and
transport details. Structured output is JSON; table output is intended for people.

Enable root-command PowerShell completion in the current session:

```powershell
jira completion powershell | Out-String | Invoke-Expression
```

Completion uses a static local command list and needs no credentials or network.
Interactive issue/epic selection and richer completion belong to S03.

## Development and validation

```sh
uv sync --frozen --group dev
wood repo validate --json
wood repo verify --json
uv build
```

Tests use in-process HTTPX mock transports and temporary configuration/state files;
no corporate PAT or Jira instance is used. Coverage includes failure and pagination
contracts, redaction, context precedence, and CLI output. Windows CI additionally
builds the wheel, installs it through uv, executes `jira.exe`, and loads its
PowerShell completion. A local macOS pass does not attest a Windows CI pass.

Development Complete uses offline automated tests, Windows packaging/installation,
security checks, and documentation. Corporate Integration Verified remains a
separate downstream gate requiring the authorized Windows work PC, PAT, WWRAP
access, and controlled live Jira reads and writes. Mocks cannot pass that gate.

## Planning and delivery

[Jira CLI project](https://projects.woodhost.cloud/projects/jira-cli): ID 9.
[Initiative](https://projects.woodhost.cloud/work_packages/570): ID 570.
Planning Increment: R1 — Jira CLI Initial Release, Version ID 28.
[S01](https://projects.woodhost.cloud/work_packages/565) references FR-01, FR-03,
FR-04, FR-12, and QR-security from the
[approved workbook](https://docs.google.com/spreadsheets/d/1I8nfLlJfabjrG7c9QThGA1X3Xf1RvKf_uTAmzkTFud8/edit)
and [requirements](https://docs.google.com/document/d/1vuodIc88J8FoNRge8-VOiwapaIwMXtVpAjK8FlyYl6k/edit).

Root `pyproject.toml` declares project/Initiative/release IDs and the integration
contract. Story feature branches start from development and PRs target development.
Both main and development require PRs, validation, and routing checks. Development
does not publish or deploy. Accumulated validation and explicit release approval
precede development → protected main integration and its pinned release workflow.
R1 is independent of repository semantic versions; Released In remains blank.

Template provenance: `SpencerRWood/template-python-library`, fetched `origin/main`
revision `216687b41193bc1cb215a21cd93117a03eaca447` on 2026-10-26. The original
template checkout and its local commits were preserved.

API references: [Atlassian PAT authentication](https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html)
and [Jira Data Center REST examples](https://developer.atlassian.com/server/jira/platform/jira-rest-api-examples/).
