# CodexShuttle

[한국어](README.md) | English

**A tool that lets Claude drive Codex as a sub-agent.**

A Claude session hands a job to Codex and picks the result back up to keep
working. After installation there is nothing for you to do — launching the app,
dispatching jobs, and collecting results are all handled by Claude. All you say
is "have Codex do this one."

```
Claude session ──▶ CodexShuttle GUI ──▶ Claude session
 (delegate work)     (does the work)     (reads the result)
```

Every job opens a fresh Codex session and runs independently. When the GUI app
initializes, the list of available models and the reasoning efforts each one
supports is handed to Claude, so you can tell Claude which model and effort to
run any given job with.

Each job is an isolated session — it knows nothing about your Claude
conversation or any previous job. Work orders are therefore passed as a
markdown file, and that file has to carry enough context — scope, constraints,
background — for the job to come back the way you want.

When Claude assigns a job, a GUI app pops up automatically so you can watch it
run. Intermediate steps — command execution, reasoning, file changes — stay in
that window and never reach Claude, so nothing but the final result costs
tokens. One caveat: if you force-quit the GUI app, Claude cannot receive the
result of a job that was still running.

## Requirements

| | |
|---|---|
| uv | [Install guide](https://docs.astral.sh/uv/getting-started/installation/) — `winget install astral-sh.uv` (macOS: `brew install uv`) |
| Codex CLI | `npm install -g @openai/codex`, then `codex login`. Signing in is not needed if you point codex at a local model provider such as Ollama |
| OS | macOS · Windows (uses Qt local sockets) |

No separate Python install is needed. uv fetches the right version on its own.

## Installation

```bash
uv tool install git+https://github.com/binyseo/CodexShuttle
```

No clone, no virtualenv. This one line creates an isolated environment and puts
the `codex-shuttle` command on your PATH.

### Installing the skill

The `SKILL.md` that teaches Claude how to use this tool ships inside the
package. One command puts it where Claude reads it.

```bash
codex-shuttle install-skill --user            # ~/.claude/skills/ — every project
codex-shuttle install-skill --project         # only this folder's .claude/skills/
codex-shuttle install-skill --project ~/proj  # a specific folder
```

That is everything you have to do. From your next Claude session on, Claude
reads the skill and handles the rest — from launching the app to collecting
job results.

### Updating

```bash
uv tool upgrade codex-shuttle
```

The skill file usually takes care of itself. What `install-skill` puts on disk is
a copy, so upgrading the tool does not refresh it — but the environment check
Claude runs before every handoff spots a stale copy, reinstalls it, and says so.

Two cases still need you.

| Situation | Command |
|---|---|
| Coming from a version before 0.3.0 — skills from back then carry no self-update instruction, so install once by hand | `codex-shuttle install-skill --user` |
| You edited the installed file yourself — the automatic refresh leaves it alone so your changes survive | `codex-shuttle install-skill --user --force` |

If you installed it with `--project`, use `--project` in place of `--user`. Claude
sessions that are already open have to restart before they see the new skill.

## The GUI

### Environment tab

Shows at a glance whether the Codex CLI is installed, whether you are signed
in, how much usage remains, and which models are available. Claude checks the
same information before dispatching a job, and stops to tell you if something
is wrong.

| Card | When healthy |
|---|---|
| Codex CLI | Green dot + version |
| Sign-in | `ChatGPT · Plus`, etc. |
| Usage limits | Remaining percentage and reset time |
| Available models | The list |

If `model_provider` in `config.toml` points at a local or third-party provider
such as Ollama, ChatGPT sign-in and usage limits do not apply. The Sign-in card
disappears, Usage limits turns grey with `Not used by ...`, and the provider name
shows up on the Codex CLI card. Jobs are no longer blocked for being signed out.

If any card is red, follow the guidance on that card. Press `F5` to re-check.

### Jobs tab

Lists the jobs Claude has dispatched, with the full conversation detail for
each.

- Every job shows `Claude session · state · elapsed`. The session is the `client-id`, or `Claude` when none was passed
- A job waiting for approval flashes its row. If you are on another tab, the **Jobs** tab label flashes
- **Stop** interrupts the running turn
- **Save transcript** exports the full conversation as Markdown or JSON, approval history included
- Remove a finished job with right-click → **Remove**, or **Clear finished** at the bottom

## Operating rules

| Item | Value |
|---|---|
| Concurrency | Up to 5. Beyond that, jobs queue and start in order |
| Job retention | The 100 most recent finished jobs |
| Approval wait default | 300 seconds. On timeout, resolved by the action set on the job |
| Job lifecycle | One job is one session. On finish, the Codex thread is released via `thread/unsubscribe` |
| Report delivery | Up to 2,000 characters ride in the stdout JSON; longer reports spill to a file announced via `result_path` |
| Error cap | Up to 2,000 characters on stdout; longer errors spill to a file announced via `error_path` |

## Layout

```
codex_shuttle/
  core/
    codex_cli.py     Codex CLI install and version checks
    app_server.py    codex app-server (stdio JSON-RPC) connection
    account.py       account, usage limits, model list parsing
    environment.py   the three above, folded into one environment state
    job.py           job, conversation item, and approval request models
    job_runner.py    job execution, streaming updates, approval handling
    ipc.py           local socket endpoint for the CLI
    transcript.py    conversation export to file
  ui/                PyQt6 widgets and windows
  client/            the codex-shuttle command
  skill/SKILL.md     the skill shipped to Claude (install-skill copies it)
```

## Notes

Claude writes task files and reports under `<project>/.codex-shuttle/`. Add one
line to `.gitignore` so they stay out of the repository.

```
.codex-shuttle/
```
