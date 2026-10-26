# Jira CLI

A local-first Windows CLI for Jira Data Center. S01 provides configuration,
working context, read-only diagnostics, metadata, and issue discovery. S02 adds
metadata-validated issue and epic creation, comments, selected-file attachments,
field edits, workflow transitions, and epic associations. S03 adds personal daily
workflows, Epic discovery, local context pickers, and command-aware completion.
Reports and the complete release handoff follow in S04–S05.

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
`--format table|json`, `--quiet`, and `--non-interactive`. The Foundation commands
above never prompt. Exit codes are 0 for success, 1 for local/network/API failures, and 2 for
argument syntax errors. Errors use stderr; `--quiet` suppresses successful stdout.
PAT values are redacted from terminal data, and errors omit response bodies and
transport details. Structured output is JSON; table output is intended for people.

Enable PowerShell 7 completion in the current session:

```powershell
jira completion powershell | Out-String | Invoke-Expression
```

Completion derives commands, subcommands, options, and finite option choices from
the argument parser. It needs no configuration, credentials, or network, and never
queries Jira while you type. Dynamic issue keys and server-defined status values
remain explicit arguments or interactive choices.

## Personal daily workflows

```powershell
jira my today --format json
jira my today --project WWRAP --sort staleness
jira epic list
jira epic list --mine
jira epic list --with-my-stories
jira epic list --all --project WWRAP
jira epic view WWRAP-100
jira epic view --issue WWRAP-100 --all-stories
jira issue select
jira epic select
```

`my today` lists unresolved issues assigned to the authenticated user across all
accessible projects. Only an explicit `--project` restricts this command; YAML,
`JIRA_PROJECT`, and saved project context do not narrow it. `--sort due-date`
(default), `priority`, `status`, or `staleness` uses Jira's configured order, with
the issue key as a tie breaker. Staleness means oldest last update first.
Observation times and due-date comparisons use UTC. Overdue and stale items
(using `reporting.aging_threshold_days`) are labeled inferred risks. Blockers are
reported only for an observed inward `is blocked by` link to an issue outside the
Done status category. Missing dependency status is labeled an inferred risk;
other/localized link descriptions are not guessed to mean blocking.

`epic list` uses the selected project and the deduplicated union of open Epics
assigned to you and Epics containing your active Stories. Active means outside
Jira's Done status category. Counts include accessible personal Stories across
projects, and completed Epics with active personal Stories carry an explicit
marker. `--mine` uses only assigned open Epics; `--with-my-stories` uses only the
personal Story rule; `--all` includes all accessible Epics in the selected project.
The `epics.include_*` YAML switches customize the default relevant filter; explicit
filter flags override them. Epic Link is discovered by its Data Center field
schema, never by a fixed custom field number or display name. Missing or ambiguous
metadata fails clearly.

`epic view` shows Epic metadata and your child Stories grouped by status and
priority, including completed Stories. `--all-stories` includes other assignees;
the active personal count and completed-Epic marker still refer only to you.
`--mine` makes the default personal scope explicit. `--issue` and existing issue
context work as for issue views. All views and lists are GET-only and leave
context unchanged. Jira visibility and permissions remain authoritative.

The optional `issue select` and `epic select` menus show numbered choices on stderr
and save only the chosen key as local active context. They never update Jira.
Blank input, interruption, or an invalid number leaves context unchanged. Pickers
require an interactive terminal, table output, and enabled context persistence;
`--non-interactive`, redirected stdin, `--format json`, and `--quiet` fail clearly
without prompting. Scripts should use explicit keys or `context set KEY`.
Status selection continues to use Jira's valid transition choices in `set status`.

## Issue and epic writes

Only use writes against authorized corporate Jira projects and test issues. Every
write command first checks the reported Server/Data Center version (8.14+ for PAT
support) and its target's effective permissions. Creation, editing, and transitions
also validate the current instance's field/transition metadata. This preflight is
per command; a previous `doctor` run does not enable unchecked writes. Unsupported
metadata schemas and ambiguous fields/values fail clearly, without guessing.

```powershell
jira create story --summary 'Investigate a test issue' --description-file '.\body.txt' --priority High --assignee username --epic WWRAP-100 --non-interactive
jira create epic --summary 'Test epic' --field 'Epic Name=Test epic' --non-interactive
jira create story --from '.\issue.yaml' --summary 'Explicit override' --no-context
jira comment add 'Authorized test comment' --issue WWRAP-123
jira comment add --file '.\comment.txt' --issue WWRAP-123
jira attach '.\selected file.txt' '.\another selected file.pdf' --issue WWRAP-123
jira set status 'In Progress' --issue WWRAP-123 --non-interactive
jira set field priority High --issue WWRAP-123
jira set field description --from-file '.\description.txt' --issue WWRAP-123
jira set field labels '["review","test"]' --issue WWRAP-123
jira set epic WWRAP-100 --issue WWRAP-123
jira set epic --clear --issue WWRAP-123
```

Field and status names above are examples: use names or IDs advertised by your
instance. `set status` sends a valid transition ID, never a raw status update.
Transitions may require extra values such as `--field 'Resolution=Fixed'`. A field's
metadata decides which values are permitted. `set field <field> --clear` clears an
optional field; required and read-only fields cannot be cleared. Project and issue
type moves are outside this command. Assignees must be active and assignable to the
target, with assignment permission. Epic Link uses the field identified by Jira's
Data Center Epic Link schema, and verifies the selected issue is an accessible Epic;
there is no hardcoded custom-field number or assumption that a subtask parent is an
Epic Link.

Creation input is one YAML mapping of metadata field names or IDs to values:

```yaml
summary: Authorized test story
description: A local UTF-8 description
priority: High
labels: [test, review]
```

`--field NAME=VALUE` overrides the file's same-named field. Dedicated arguments
(`--summary`, `--priority`, `--assignee`, `--description-file`) then take precedence.
Use quoted strings for identifier/date values in YAML. Extra required custom fields
use `--field`; Epic Name is included only if the instance advertises it. Arrays use
JSON on the command line, or native YAML lists in the input file. Creation never
accepts a chosen issue key, project field override, or issue-type override: Jira
allocates the key for the explicitly selected project and Story/Epic command.

Missing required creation/transition values, field choices, and epic keys prompt
only in an interactive terminal. `--non-interactive` and redirected stdin fail
instead of prompting. Prompts go to stderr to preserve JSON stdout.

Successful creation returns the server-assigned key and its URL, and then updates
active context by default. `--no-context`, `creation.set_context_after_create=false`,
or `context.persist=false` disables that update. Failed or uncertain creation never
changes selection. If creation is confirmed but saving local context fails, the
confirmed key is still returned with a warning; do not create it again.

Writes are single-attempt requests. A timeout, server error, unexpected success
status, or malformed success response reports an uncertain outcome: inspect Jira
before any manual retry. No automatic write retry is sent. Error bodies and
transport details stay out of output. No bulk mutation or unattended agent write
facility is provided.

Attachments accept explicitly named regular files only, validate the advertised
upload limit, and never scan a directory. Each selected file uploads once. After
the first failed or uncertain upload, remaining files are not attempted. JSON
output reports each file as `uploaded`, `failed`, `uncertain`, or `not_attempted`;
an incomplete operation exits 1. Preserve the successful results and inspect Jira
before retrying uncertain files. A comment accepts inline text or one UTF-8 file,
with no automatic context change.

## Development and validation

```sh
uv sync --frozen --group dev
wood repo validate --json
wood repo verify --json
uv build
```

Tests use in-process HTTPX mock transports and temporary configuration/state files;
no corporate PAT or Jira instance is used. Coverage includes failure and pagination
contracts, redaction, context precedence, CLI output, permission gates, typed field
metadata, uncertain writes, and partial attachment outcomes. Windows CI additionally
builds the wheel, installs it through uv, executes `jira.exe`, and loads its
PowerShell completion and exercises command, subcommand, and option completion.
A local macOS pass does not attest a Windows CI pass.

Development Complete uses offline automated tests, Windows packaging/installation,
security checks, and documentation. Corporate Integration Verified remains a
separate downstream gate requiring the authorized Windows work PC, PAT, WWRAP
access, and controlled live Jira reads and writes. Mocks cannot pass that gate.

## Planning and delivery

[Jira CLI project](https://projects.woodhost.cloud/projects/jira-cli): ID 9.
[Initiative](https://projects.woodhost.cloud/work_packages/570): ID 570.
Planning Increment: R1 — Jira CLI Initial Release, Version ID 28.
[S03](https://projects.woodhost.cloud/work_packages/567) covers FR-08, FR-09,
FR-10, and FR-12, following S01 on the approved R1 dependency graph.
[S01](https://projects.woodhost.cloud/work_packages/565) references FR-01, FR-03,
FR-04, FR-12, and QR-security from the
[approved workbook](https://docs.google.com/spreadsheets/d/1I8nfLlJfabjrG7c9QThGA1X3Xf1RvKf_uTAmzkTFud8/edit)
and [requirements](https://docs.google.com/document/d/1vuodIc88J8FoNRge8-VOiwapaIwMXtVpAjK8FlyYl6k/edit).
[S02](https://projects.woodhost.cloud/work_packages/566) covers FR-02, FR-05, FR-06,
FR-07, and QR-safety, and depends on the completed S01 foundation.

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
Write endpoint contract: [Jira Data Center issue API](https://developer.atlassian.com/server/jira/platform/rest/v10000/api-group-issue/).
