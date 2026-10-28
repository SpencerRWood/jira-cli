# Jira CLI

A local-first Windows CLI for Jira Data Center. S01 provides configuration,
working context, read-only diagnostics, metadata, and issue discovery. S02 adds
metadata-validated issue and epic creation, comments, selected-file attachments,
field edits, workflow transitions, and epic associations. S03 adds personal daily
workflows, Epic discovery, local context pickers, and command-aware completion.
S04 adds daily, weekly, and aging reports with explicit local exports.
The complete Windows release handoff follows in S05.

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

### Corporate Windows certificate trust

HTTPS uses the operating system's managed certificate store through
[`truststore`](https://truststore.readthedocs.io/), including the Windows certificate
store on your work PC. If Windows already trusts the corporate Jira certificate
chain, the CLI can use that trust without a separate PEM CA bundle. Certificates
still require a valid chain and hostname; this does not bypass verification or
install certificates into Windows. Corporate connectivity/PAT access must still
be verified on the authorized work PC.

Explicit process-level `SSL_CERT_FILE` or `SSL_CERT_DIR` settings override the
system-store default, with the file taking precedence when both are set. Invalid
overrides fail rather than switching trust sources. The application `.env` reads
only `JIRA_PAT`; it does not load certificate or proxy variables. Existing
HTTPX proxy variables and `NO_PROXY` remain effective.

If you previously set a temporary CA override while troubleshooting, remove it
from the current PowerShell process before testing managed Windows trust:

```powershell
Remove-Item Env:SSL_CERT_FILE -ErrorAction SilentlyContinue
Remove-Item Env:SSL_CERT_DIR -ErrorAction SilentlyContinue
jira auth test --project WWRAP
```

Use those removal commands only for troubleshooting settings you added; preserve
organization-required overrides. A globally installed copy must be rebuilt and
reinstalled to receive this change. From a checkout containing this fix:

```powershell
$jiraBuildDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ('jira-cli-build-' + [guid]::NewGuid())
uv build --wheel --out-dir $jiraBuildDirectory
if ($LASTEXITCODE -ne 0) { throw 'Wheel build failed' }
$jiraWheel = @(Get-ChildItem -Path $jiraBuildDirectory -Filter '*.whl')
if ($jiraWheel.Count -ne 1) { throw 'Expected exactly one Jira CLI wheel' }
uv tool install --python 3.14 --reinstall $jiraWheel[0].FullName
if ($LASTEXITCODE -ne 0) { throw 'Tool installation failed' }
jira auth test --project WWRAP
```

Run the final authentication command inside the corporate network after configuring
the authorized Jira URL and PAT. A remaining certificate error means the chain,
hostname, required proxy, or trust-store policy still needs investigation. Do not
switch to HTTP, `verify=False`, or an unverified downloaded certificate.

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
jira story view WWRAP-123
jira story list
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
`issue list` and `story list` apply the selected project, current-user assignment,
and configured active status categories by default. `story list` additionally
restricts issue type to Story. Jira permission errors remain authoritative.
All Foundation operations against Jira are GET requests, with no automatic retries.

Common flags can appear before or after subcommands: `--issue`, `--project`,
`--format table|json`, `--quiet`, and `--non-interactive`. The Foundation commands
above never prompt. Exit codes are 0 for success, 1 for local/network/API failures, and 2 for
argument syntax errors. Errors use stderr; `--quiet` suppresses successful stdout.
PAT values are redacted from terminal data, and errors omit response bodies and
transport details. Structured output is JSON; table output is intended for people.

Issue keys in terminal lists and views link to their Jira issues, including
My Today, parent/child issues, context, pickers and terminal reports. Use your
terminal's follow-link gesture (often Ctrl+click). Support depends on the terminal
host; keys remain readable without it. JSON, CSV, Markdown, saved reports and
redirected table output do not contain terminal hyperlink escape sequences.

Enable PowerShell 7 completion in the current session:

```powershell
jira completion powershell | Out-String | Invoke-Expression
```

Completion derives commands, subcommands, options, and finite option choices from
the argument parser. It needs no configuration, credentials, or network, and never
queries Jira while you type. Dynamic issue keys and server-defined status values
remain explicit arguments or interactive choices.

### Consistent Issue, Story, and Epic views

`issue view` handles any issue type. `story view` and `epic view` validate the
returned type and fail clearly on a mismatch. Each accepts a positional key,
`--issue KEY`, or the existing working context when the key is omitted. Explicit
views do not apply list filters. All three use shared retrieval/normalization and
the same table/JSON presentation. `epic view` retains grouped child Stories,
`--mine`, and `--all-stories`; `--detail` is available on all three views.

Default view JSON uses `key`, `url`, `summary`, `description`, `type`, `priority`,
`status`, `assignee`, and `epic`. Descriptions appear in default Issue, Story, and
Epic views without `--detail`; lists remain compact.

Terminal `issue list`, `story list`, and `epic list` output uses one issue per
table row, with Key, Summary, Status, Priority, and Assignee. Issue/Story tables
also show the Epic key; Epic tables show personal active-Story counts and a
completed-with-active-Stories warning. Long summaries wrap within the terminal.
Emails and URLs remain available in `--format json` and individual views rather
than crowding the list. Empty lists print `No matching issues.`; JSON retains
its existing array/envelope schema and `--quiet` suppresses terminal output.

Status/priority are readable names; `epic` is a directly associated
Epic key or null. A priority ID without a name is resolved against Jira's actual
priority metadata. Unknown names stay null; icon filenames never determine priority.
Assignees use only Jira `displayName` and, when supplied/permitted, `emailAddress`.
Simple `Last, First` names become `First Last`; multipart, annotated, or ambiguous
names stay unchanged. Missing assignees/emails are valid. Avatar/icon objects are
excluded. Detailed views add whitelisted dates, comments, attachments,
and related issue keys. Raw REST envelopes and the former nested Epic envelope are
replaced by this normalized schema; scripts should use these field names. Explicit
`search --jql` still returns the search response issues without changing the JQL.

The existing single YAML schema gains optional defaults; old config files still
load without edits. Presentation settings stay under the existing `output` section
instead of adding a second display section:

```yaml
defaults:
  project: WWRAP
  issue_type: Story
  filters:
    assigned_to: me
    status_categories: [new, indeterminate]
output:
  format: table
  show_issue_urls: true
  assignee:
    name_format: first_last
    include_email: true
    include_avatar: false
  priority:
    source: name
  status:
    source: name
```

These are additions to your current config, not a replacement for its Jira URL,
authentication, context, Epic, or reporting settings. Category values are Jira's
stable keys `new`, `indeterminate`, and `done`, resolved to IDs from live category
metadata before constructing JQL. `assigned_to` accepts `me` or `all`;
`name_format` accepts `first_last` or `original`. Email display can be disabled.
Avatar inclusion and icon-derived priority/status sources are unsupported. PAT
loading from the fixed application `.env` is unchanged.

For Issue/Story lists and pickers, `--mine` explicitly selects your assignments;
`--include-done` retains the ownership filter but includes all categories.
`--all` includes all visible assignees/categories within the selected project and
restores the former unfiltered Issue-list behavior. `--all` and `--mine` conflict;
`--all --include-done` is redundant but valid. `--project` retains its established
precedence. `story select` and `issue select` also require unresolved issues by
default (`resolution IS EMPTY`); `--all` or `--include-done` removes that restriction.
They use the existing local context picker, with no Jira writes. My Today keeps
its own unresolved/current-user semantics and cross-project default.

When your active issue is an Epic, `story list`, `story select`, and `create story`
automatically use it as the parent Epic. Table output starts with
`Epic context: WWRAP-100 — Stories scoped to this Epic.` Set it with
`jira context set WWRAP-100` or `jira epic select`. Story lists still apply your
assigned/active defaults; `--all` broadens those filters while retaining the Epic
scope. Without an explicit `--project`, the Epic determines the scope rather than
the configured default project (creation uses the Epic's project). An explicit
`--project` remains authoritative.

```powershell
jira context set WWRAP-100
jira story list
jira story list --all
jira create story --summary 'Fixture summary' --description 'Fixture details'
jira story list --epic WWRAP-200
jira story list --no-epic
jira create story --summary 'Standalone story' --no-epic
```

Automatically scoped creation keeps the Epic context, so repeated creations stay
under it. `--epic KEY` overrides the parent for a command; `--no-epic` bypasses the
Epic context. Those two options conflict. Story creation always leaves existing
context unchanged, including explicit overrides/bypasses. To supply Epic Link through `--field` or an
input file while an Epic is active, also use `--no-epic` to avoid supplying two
parents. Picking a Story explicitly changes active context to that Story.
Non-Epic context does not constrain Story lists or creation. Inaccessible context
or missing issue type fails clearly; use `--no-epic` to bypass it deliberately.
Generic Issue commands, explicit issue views, and supplied JQL remain unchanged.
The notice is omitted for JSON and quiet output, preserving scripting formats.

Epic listing/selection stays separate: relevant Epics are the deduplicated union
of assigned open Epics and Epics with active personal Stories, including completed
Epics from the second rule. General list defaults never add a blanket assignee
filter to Epics. Existing `--mine`, `--with-my-stories`, `--all`, and `--project`
remain; Epic `--include-done` broadens only the assigned-Epic rule, without counting
completed child Stories as active. The existing `epics` YAML section is unchanged.
Assigned Epics with an empty Jira resolution are included even when their status
category is Done and they have no active children. The selected project and
configured relevant-filter switches still apply.

See the [Windows handoff verification guide](docs/windows-handoff-testing.md) for
read-first corporate checks, filter comparisons, and separately authorized inline
description testing. Automated fixtures are not evidence of live Jira integration.

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

`my today` lists unresolved non-Epic issues assigned to the authenticated user
across all accessible projects. Only an explicit `--project` restricts this command; YAML,
`JIRA_PROJECT`, and saved project context do not narrow it. By default, it sorts by
staleness, showing the least recently updated issues first. Explicit `--sort due-date`,
`priority`, `status`, or `staleness` uses Jira's configured order, with
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
Here, assigned open Epics also include unresolved Epics regardless of status
category. Resolved Done Epics require active personal Stories or an explicit
`--include-done`/`--all` selection.

`epic view` shows Epic metadata and your child Stories grouped by status and
priority, including completed Stories. `--all-stories` includes other assignees;
the active personal count and completed-Epic marker still refer only to you.
`--mine` makes the default personal scope explicit. `--issue` and existing issue
context work as for issue views. All views and lists are GET-only and leave
context unchanged. Jira visibility and permissions remain authoritative.

The optional `issue select`, `story select`, and `epic select` menus show a numbered
table of keys, summaries, and statuses on stderr, without internal filtering flags,
and save only the chosen key as local active context. They never update Jira.
Blank input, interruption, or an invalid number leaves context unchanged. Pickers
require an interactive terminal, table output, and enabled context persistence;
`--non-interactive`, redirected stdin, `--format json`, and `--quiet` fail clearly
without prompting. Scripts should use explicit keys or `context set KEY`.
Status selection continues to use Jira's valid transition choices in `set status`.

## Reports and local exports

```powershell
jira report daily
jira report weekly --mine --project WWRAP --since 2026-10-19 --format json
jira report aging --format csv --output '.\private reports\aging.csv'
jira report weekly --format markdown --output '.\private reports\weekly.md'
```

Reports default to `reporting.default_project` (WWRAP). `JIRA_PROJECT` and explicit
`--project` override that value, in that order. Saved working context does not
alter report scope. `--mine` restricts current assignee to the authenticated user;
omitting it includes accessible issues in the selected project. `--issue` is
unsupported for project reports. Every request is GET-only and leaves context
unchanged. No reports are scheduled, uploaded, or sent to external services.

Daily reports include currently active issues, observed blockers, and overdue
flags. Active means Jira status category outside Done, independently of resolution.
Aging reports include active issues last updated **at least**
`reporting.aging_threshold_days` days ago (default 14). An explicit `--since
YYYY-MM-DD` limits daily/aging rows to last updates on or after that UTC date;
without it, older active issues remain included. The window start shown for these
snapshots defaults to midnight UTC; it does not remove older current work.

Weekly reports distinguish `completed_at` events, `currently_active` work, and
`carried_over` work. Completion means an observed transition from a non-Done status
category into Done in **[start, end)**. A reopened issue can have completion events
and remain active. An old Done issue is not counted simply because it is Done now,
and a Done-to-Done status transition is not another completion. Carried over means
currently active, created before start, and also non-Done at start. Counts represent
distinct issues, while `completed_at` preserves each qualifying transition.

The weekly default is the rolling `reporting.weekly_lookback_days` (7) before one
captured observation time; `--since` replaces the start with midnight UTC on the
chosen date. Every format records UTC, the start, exclusive end, thresholds, counts,
and definitions. Dates and history timestamps are normalized to UTC. Due dates are
overdue before the observation UTC date; staleness uses elapsed time. Sorting by
issue key and fixed time inputs make fixture reports reproducible. Jira searches
page through the current project/assignee scope and filter timestamps locally to
avoid interpreting JQL date literals in a different Jira user time zone.

For weekly history, the CLI reads Data Center issue `expand=changelog` and maps
status IDs to the instance's current status-category metadata. It checks the
returned history is complete and reconciles transitions against the current
status. Missing/deleted status metadata, inaccessible or truncated history, and
issues changing during collection fail the report without producing a partial
export. No unsupported Cloud history endpoint is substituted. Current category
definitions and current project/assignee visibility apply; this is not historical
ownership attribution or a reconstruction of past category configuration. Actual
corporate history availability must be verified inside the corporate network.

`--format table|json|csv|markdown` works before or after report subcommands. JSON
contains the full report object. CSV has one `record_type=metadata` row containing
JSON metadata, followed by `record_type=issue` rows; empty reports retain metadata.
Arrays use JSON cells. CSV formula prefixes are neutralized with a leading apostrophe.
Text controls/newlines and non-ASCII are escaped in textual exports, and Markdown
content is escaped to avoid active links/images or injected table rows. Blocker
rules and explicitly labeled inferred risks match My Today.

Without `--output`, the report goes to stdout. `--quiet` suppresses stdout; an
explicit output file is still written. `--output` creates one UTF-8 file atomically
in an existing local directory and refuses to overwrite any existing file or link.
No raw issue bodies, comments, attachments, history authors, or credentials are
exported. Known PAT values are redacted before rendering or saving. Reports still
contain selected corporate issue data: choose an authorized local folder, apply
corporate access/retention rules, and keep exports out of public repositories.
On POSIX the new file is private to its owner; Windows directory ACLs apply.

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
jira create story --summary 'Test inline description' --description 'Fixture requirements text' --non-interactive
jira create story --from '.\issue.yaml' --summary 'Explicit override' --no-context
jira comment add 'Authorized test comment' --issue WWRAP-123
jira comment add --file '.\comment.txt' --issue WWRAP-123
jira attach '.\selected file.txt' '.\another selected file.pdf' --issue WWRAP-123
jira set status 'In Progress' --issue WWRAP-123 --non-interactive
jira set field priority High --issue WWRAP-123
jira assign --issue WWRAP-123
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

`jira assign --issue WWRAP-123` assigns that issue to your authenticated Jira
account. Omit `--issue` to use the active working context. This performs a real
Jira write after checking assignment permission and that your account is active
and assignable to the target issue; it leaves local context unchanged. It does
not require the issue to appear in your default lists. An uncertain write is
never retried automatically: inspect Jira before retrying. During corporate
verification, run read-only checks first and only run this command on an
explicitly authorized test issue.

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

Both `create story` and `create epic` accept `--description` for inline text,
including PowerShell here-string multiline input, or `--description-file` for
an explicit UTF-8 file. Supplying both is a syntax error before any Jira request.
Explicit inline/file descriptions override input YAML or generic field assignments;
without either, the existing YAML/metadata-driven interactive behavior remains.
Server-assigned keys and confirmed-creation context updates are unchanged.

Missing required creation/transition values, field choices, and epic keys prompt
only in an interactive terminal. `--non-interactive` and redirected stdin fail
instead of prompting. Prompts go to stderr to preserve JSON stdout.

Story creation assigns the authenticated Jira user by default, after checking
assignment permissions and assignability. An explicit assignee from `--assignee`,
`--field`, or input YAML takes precedence. Missing identity, unavailable assignment
metadata, or denied assignment fails before creating anything; the CLI does not
silently leave the Story unassigned. Story creation returns the server-assigned key
and URL and never changes active context. Use `jira context set KEY` or a picker
when you want to switch to the new Story.

Both creation commands accept comma-separated Jira usernames as watchers:

```powershell
jira create story --summary 'Fixture summary' --description 'Fixture details' --watchers 'alice,bob'
jira create epic --summary 'Fixture Epic' --field 'Epic Name=Fixture Epic' --watchers 'alice, bob'
```

Quote the list in PowerShell. Spaces around names are trimmed; duplicates are
removed while preserving order. Empty entries and control characters are rejected.
These are usernames, not display names or email addresses. The CLI checks the
Manage Watchers permission before creation when adding other users; self-watching
does not require that permission. Jira must have watching enabled and permit the
requested users to watch the issue.

Watchers use Jira's separate [Add watcher API](https://docs.atlassian.com/software/jira/docs/api/REST/8.14.0/#api/2/issue-addWatcher),
after the issue key is confirmed. If an addition fails or is uncertain, the CLI
stops adding watchers, exits with code 1, and returns the created key/URL plus each
watcher's `added`, `failed`, `uncertain`, or `not_attempted` result. Inspect that
existing issue and add remaining watchers there; do not rerun creation. There is
no automatic retry or rollback. Without `--watchers`, no watcher request is made.

To add watchers to an existing issue of any type without creating anything:

```powershell
jira watcher add --issue WWRAP-123 --watchers 'alice,bob'
jira watcher add --watchers 'alice,bob' # Uses your active issue context
```

This leaves context unchanged and uses the same username parsing, permission
checks, per-user results, and single-attempt behavior as creation. It requires
access to the target issue; adding other users also requires Manage Watchers.
After a partial failure, inspect Jira and retry only remaining additions against
the existing issue, rather than creating a replacement.

Epic creation retains its existing context-update behavior:
`--no-context`, `creation.set_context_after_create=false`, or `context.persist=false`
disables that update. These settings do not enable context updates for Stories.
Failed or uncertain creation never
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
TLS tests additionally use a loopback HTTPS server with generated fixture CAs,
without changing OS certificate stores or contacting corporate services. No
corporate PAT or Jira instance is used. Coverage includes failure and pagination
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
[S04](https://projects.woodhost.cloud/work_packages/568) covers FR-11 and QR-data,
following S01 and S03. Corporate integration remains a separate downstream gate.
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
