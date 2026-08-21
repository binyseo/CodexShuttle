---
name: codex-shuttle
description: Hand a coding task to codex and get the result back. Use it when a chunk of work — a refactor, a batch of edits, tidying tests — can be handed off whole to another agent. The CodexShuttle desktop app runs the job, shows it live, and handles approvals.
---

# Handing work to codex

The `codex-shuttle` command sends a task to codex and returns the result when it
finishes. The work runs inside the **CodexShuttle GUI app** on the user's machine,
where they can watch progress and approve anything that needs it.

## Step 1 — Bring the app up and check it can take work

Always run this before handing anything over.

```bash
codex-shuttle ensure
```

If the GUI app is down it **launches it for you**, waits for the environment check
to settle, then reports the state. If it is already up it returns immediately. You
never have to ask the user to start the app.

| Exit code | Meaning | What to do |
|---|---|---|
| `0` | Ready to take work | Move on |
| `5` | codex cannot take work | Relay `blockers` from the output to the user and stop |
| `3` | Could not launch the app | Tell the user and stop |

`blockers` names whichever applies: not installed, not signed in, usage exhausted.
Do not retry on your own — hand it to the user.

If `usage.remaining_percent` is low (say under 10%), say so before handing over a
big job. `window` carries the reset time.

### Not every setup goes through ChatGPT

`provider` tells you which one this is.

```json
"provider": { "name": "ollama", "chatgpt_auth": false, "source": "app-server" }
```

When `chatgpt_auth` is `false` the user runs codex against a local or third-party
model provider. Sign-in and usage limits do not apply there: `account` reads as not
signed in, `usage.remaining_percent` is `null`, and neither one becomes a blocker.
Do not tell such a user to run `codex login`, and do not weigh usage when picking a
model — read `models` instead, where `efforts` is usually empty for these.

**This check does not stay true.** The user can close the window at any moment, and
often does. Run `ensure` again right before each submit — see step 3.

### The response carries the model list

`models` lists the models available right now along with the reasoning efforts each
one accepts. If you plan to pass `--model` or `--effort`, **pick from here.** A value
that is not on the list still submits fine and then fails at `turn/start`.

```json
"models": [
  { "slug": "gpt-5.6-sol", "name": "GPT-5.6-Sol", "default": true,
    "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
    "default_effort": "low",
    "description": "Latest frontier agentic coding model." }
]
```

| Field | Meaning |
|---|---|
| `slug` | What goes into `--model`, verbatim |
| `efforts` | The `--effort` values this model accepts. Do not use one that is missing. An empty list means the model takes no effort setting — omit `--effort` |
| `default` | The model used when you omit `--model` |
| `default_effort` | The effort used when you omit `--effort`. `null` means codex decides |
| `description` | One line on what the model is for. **This is what you choose by** |

## Step 2 — Write the task to a file

Prompts get long, so pass one as a file. Cover three things.

1. **Scope** — the files or directories to touch
2. **Constraints** — what must not change, what has to hold
3. **A report** — `summarize what you changed and why in your final message`

Item 3 is the one that matters. **codex's final assistant message is the report**,
and that is what comes back through `--output-path`. Ask for it or you get nothing.

**Do not have codex write the result file itself.** CodexShuttle writes it.

```markdown
Merge the duplicated payment validation logic in the scope below.

Files
- src/payment/validator.py
- src/order/service.py

Constraints
- Do not change public API signatures
- Every existing test has to pass
- No new dependencies

Summarize what you changed and why in your final message. Length is fine.
```

### Put files under `.codex-shuttle/` inside `--cwd`

Write the task file and the report file **under `.codex-shuttle/` in the working
folder, using absolute paths.**

```
<--cwd>/.codex-shuttle/task-<label>.md      the task
<--cwd>/.codex-shuttle/report-<label>.md    the report (--output-path)
```

**Do not use shell-dependent notation like `/tmp`.** On Windows the tool that
creates the file and the shell that runs the command resolve `/tmp` to different
folders. You write the file, and the command still dies with `No such task file`.

Tell the user to add `.codex-shuttle/` to `.gitignore`.

### The report file needs no approval

The file `--output-path` points at is written by **CodexShuttle, not by codex.** So
it sits outside the sandbox question entirely, inside or outside `--cwd`, and
`--approval never` stays fine.

Approval matters for something else — **when the source codex has to edit lives
outside `--cwd`.** Only then add the flags below. If the user is away, an approval
request nobody answers gets auto-denied and the job stalls, so set the timeout
behavior too.

```bash
--approval on-request --approval-timeout 30 --on-timeout accept
```

## Step 3 — Submit it in the background

### Confirm the app is alive first — every single time

The app is a window on the user's desktop and they close it whenever they like. An
`ensure` from earlier in the conversation proves nothing about now.

```bash
codex-shuttle ensure
```

Run it **immediately before every submit** and read the exit code. It is cheap and
idempotent: when the app is already up it returns at once, and when it is down it
brings the app back before you lose a job to it. One `ensure` covers a batch of jobs
you are about to fire together.

| Exit code | What it means | What to do |
|---|---|---|
| `0` | Up and ready | Submit |
| `5` | codex cannot take work | Relay `blockers` and stop |
| `3` | Could not bring the app up | Tell the user and stop |

```bash
codex-shuttle run \
  --task /Users/foo/shop-api/.codex-shuttle/task-payment.md \
  --label "[payment] merge validation" \
  --cwd /Users/foo/shop-api \
  --client-id "$CLAUDE_CODE_SESSION_ID" \
  --output-path /Users/foo/shop-api/.codex-shuttle/report-payment.md \
  --sandbox workspace-write \
  --approval never \
  --wait
```

**Always run this with `run_in_background`.** The process exits exactly when the job
ends, and that exit is the completion signal. Get on with other work while it runs.
In the foreground it will hit the tool timeout.

### Arguments you should not skip

| Argument | If you skip it |
|---|---|
| `--cwd` | The folder the GUI app was started in becomes the working folder. That can open writes to the home directory |
| `--label` | The user loses the only clue for telling jobs apart in the list |
| `--client-id` | The user cannot tell which session submitted the job |
| `--output-path` | A long report scatters into a temp folder where nobody will read it |
| `--wait` | You submit and never get the result |

### Session identifier

Put `$CLAUDE_CODE_SESSION_ID` in `--client-id`. It is what labels the job in the GUI
list. Leave it empty and the user cannot tell jobs apart on screen.

```bash
echo "$CLAUDE_CODE_SESSION_ID"   # if empty, use the fallback below
```

If the variable is empty, pass a fixed string that identifies this session instead
(for example `plan-2026-08-21`). Do not pass an empty value — the job then shows up
as a bare `Claude` in the list, indistinguishable from every other session's.

### Choosing sandbox and approval

| Situation | Setting |
|---|---|
| Investigation only, touches no files | `--sandbox read-only --approval never` |
| Edits stay inside the working folder | `--sandbox workspace-write --approval never` |
| **Source to edit lives outside the working folder** | `--sandbox workspace-write --approval on-request --on-timeout accept` |

With `on-request`, codex asks on the user's screen whenever it reaches outside the
sandbox. Set the timeout behavior for when nobody is there.

- `--on-timeout accept` — nobody there, go ahead (unattended handoff)
- `--on-timeout decline` — nobody there, skip that one action and continue
- `--on-permission-timeout decline` — never let an elevation through unattended

When auto-allowing, keep `--approval-timeout` short, **15-30 seconds**. That number
is dead waiting time.

### Choosing a model

**Usually omit `--model`.** The entry with `default: true` is codex's own pick, and
it is the right one for most work.

Reach for a different model only when the shape of the job argues for it, and decide
from each entry's `description` — **never from the slug.** Slugs change between codex
versions; the descriptions say what each model is for.

| When the job is | Look for a `description` that says |
|---|---|
| Mechanical and repetitive — renames, formatting, mass edits | small, fast, cost-efficient |
| Ordinary day-to-day work, especially when usage is thin | balanced, everyday |
| Hard design work or tricky debugging | frontier — usually the default already |

```bash
--model gpt-5.4-mini    # only after reading its description in the ensure output
```

Check `usage.remaining_percent` first. When it is low, a cheaper model finishes the
job without burning through the user's window — say so rather than silently picking
the biggest one. On a setup where `provider.chatgpt_auth` is `false` the field is
`null` and there is no window to spend — pick on `description` alone.

If the user names a model, use it and do not second-guess them.

### Choosing reasoning effort

**Usually omit `--effort` too.** The model's `default_effort` is fine.

| When the job is | Setting |
|---|---|
| Normal | Omit |
| A broad refactor that needs design judgement | `--effort high` |
| Repetitive edits, formatting cleanup | `--effort low` |

Never pass an effort missing from that model's `efforts`. The submit goes through and
then `turn/start` fails, so the work is wasted. Raising effort costs more tokens and
more waiting.

Model and effort are separate knobs. High effort on a small model is not the same as
a bigger model — if the work needs judgement, move the model, not just the effort.

## Step 4 — Read the result

When the background process exits, one JSON object is sitting on stdout.

```json
{
  "job_id": "a1b2c3d4e5f6",
  "label": "[payment] merge validation",
  "state": "succeeded",
  "result": "Merged the validation into validator.py. ...",
  "result_chars": 1843,
  "result_path": "/Users/foo/shop-api/.codex-shuttle/report-payment.md",
  "error": "",
  "error_path": null,
  "elapsed_sec": 214.0,
  "token_usage": { "totalTokens": 84210 }
}
```

**A short report rides along; a long one goes to a file.** codex's final message has
no length limit and could swallow stdout whole, so up to 2,000 characters land in
`result` and anything longer spills to a file. You do not need to know the length in
advance.

| `result` | `result_path` | What to do |
|---|---|---|
| body | `null` | Read it as is. No file to clean up |
| body | path | You passed `--output-path`. Either one works |
| `null` | path | It ran past 2,000 characters. **Read that file** |

| Field | Meaning |
|---|---|
| `result_chars` | Report length. `0` means codex left no final message |
| `result_path` | Where the body was written: your `--output-path`, or a temp file if you omitted it and the body was long |
| `error_path` | Set when the error ran past 2,000 characters. `error` then holds only the head |

| Exit code | `state` | Meaning |
|---|---|---|
| `0` | `succeeded` | Finished normally |
| `1` | `failed` | Failed. Reason in `error`, or `error_path` if long |
| `2` | `interrupted` | The user hit **Stop** in the GUI, or an approval was denied. A person stopped it — do not resubmit on your own |
| `3` | — | The app is down. Two different cases — see below |
| `4` | — | Bad request. Reason on `stderr` |

If `state` is `succeeded` but `result_chars` is `0`, codex left no report. Most
likely the prompt never asked for one.

### Exit code 3 means one of two things

Tell them apart by whether the `job_id:` line ever showed up on stderr.

| stderr | What happened | What to do |
|---|---|---|
| No `job_id:` line | The submit never landed. The app was already down | Run `ensure`, then submit again |
| `job_id:` line is there | The job was running and the app went down under it | **The user closed the window. Do not resubmit on your own** — say what happened and ask |

The second case is a person pulling the plug, same as hitting **Stop**. Treat it that
way.

**The full conversation does not come back.** Command output, reasoning, and
intermediate messages are not in the result. If the user needs those, point them at
the **Save transcript** button in the GUI.

## Running several jobs at once

Five jobs run concurrently. Beyond that they queue and start in order. Give each a
different `--cwd` and they cannot collide on files.

```bash
codex-shuttle run --cwd ~/work/api \
  --task ~/work/api/.codex-shuttle/task.md \
  --output-path ~/work/api/.codex-shuttle/report.md \
  --label "[api] ..." --client-id "$CLAUDE_CODE_SESSION_ID" --wait &

codex-shuttle run --cwd ~/work/worker \
  --task ~/work/worker/.codex-shuttle/task.md \
  --output-path ~/work/worker/.codex-shuttle/report.md \
  --label "[worker] ..." --client-id "$CLAUDE_CODE_SESSION_ID" --wait &
```

Each process gets only its own job's result. They never cross.

## Stopping a job

Right after submit, the `job_id` appears **on stderr as a single line**. Nothing
reaches stdout until the job ends, so this is how you get the id to stop it.

```
job_id: a1b2c3d4e5f6
```

```bash
codex-shuttle cancel a1b2c3d4e5f6 --client-id "$CLAUDE_CODE_SESSION_ID"
```

Stopping makes the waiting `--wait` process exit with **code 2**. The same signal
arrives when the user hits **Stop** in the GUI.

There are no query commands. Submit one job, wait for it with `--wait`, done.

## Do not

- **Do not expect a long report on stdout.** Past 2,000 characters `result` is
  `null` and you have to read the file at `result_path`.
- **Do not trust an earlier `ensure`.** Check again right before you submit. The
  window may be gone by now.
- **Do not read the exit code of a shell line you appended after the command.** Put
  `codex-shuttle run` last, or capture `$?` from the command itself. A trailing
  `echo` will happily report success over a failed submit.
- **Do not pass an empty `--client-id`.** The user loses track of which session a
  job came from.
- **Do not run `--wait` in the foreground.** Long jobs will hit the tool timeout.
- **Do not retry when `ensure` exits 5.** Whatever `blockers` names — a missing CLI,
  sign-in, an exhausted window — is the user's to fix, not yours to work around.
- **Do not launch the app yourself.** Use `ensure` instead of backgrounding
  `codex-shuttle gui` — `ensure` handles the app dying with your session.
- **Do not reach for `--sandbox danger-full-access`.** Only when the user asks for it
  explicitly.
