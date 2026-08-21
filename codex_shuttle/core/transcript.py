"""잡의 대화 내역을 파일로 뽑는다.

화면에서 보는 것과 같은 순서로, 승인 요청과 그 결정까지 제자리에 끼워 넣는다.
나중에 이 파일만 보고도 무슨 일이 있었는지 알 수 있어야 한다.
"""

import json
from datetime import datetime

from codex_shuttle.core.job import ApprovalDecision, ApprovalRequest, Job

_ITEM_TITLES = {
    "userMessage": "User",
    "agentMessage": "Codex",
    "reasoning": "Reasoning",
    "plan": "Plan",
    "commandExecution": "Command",
    "fileChange": "File change",
    "mcpToolCall": "MCP tool",
    "dynamicToolCall": "Tool call",
    "webSearch": "Web search",
    "contextCompaction": "Context compaction",
    "subAgentActivity": "Subagent",
}

# 본문을 코드 블록으로 감쌀 항목들. 들여쓰기와 공백이 의미를 갖는 출력이다.
_FENCED = {
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "dynamicToolCall",
}

_APPROVAL_TITLES = {
    "command": "Command approval",
    "fileChange": "File change approval",
    "permissions": "Elevated permission",
}

_DECISION_TITLES = {
    ApprovalDecision.ACCEPT: "Allow",
    ApprovalDecision.ACCEPT_FOR_SESSION: "Allow for session",
    ApprovalDecision.DECLINE: "Deny",
    ApprovalDecision.CANCEL: "Deny and stop turn",
    ApprovalDecision.TIMED_OUT: "Auto-resolved after timeout",
}

_STATE_TITLES = {
    "queued": "Queued",
    "starting": "Starting",
    "running": "Running",
    "succeeded": "Succeeded",
    "failed": "Failed",
    "interrupted": "Interrupted",
}


def suggested_filename(job: Job, extension: str) -> str:
    """저장 대화상자에 채워 넣을 기본 파일명."""
    safe = "".join(
        character if character.isalnum() or character in " -_" else "_"
        for character in job.title
    ).strip()
    return "{0}-{1}.{2}".format(safe or "job", job.job_id, extension)


def to_markdown(job: Job) -> str:
    lines: list[str] = ["# " + job.title, ""]
    lines.extend(_metadata_rows(job))

    if job.error:
        lines.extend(["", "## Error", "", "```", job.error, "```"])

    lines.extend(["", "## Conversation", ""])
    for item in job.items:
        lines.extend(_item_block(item))
        # 이 항목에서 올라온 승인은 바로 뒤에 붙여 맥락을 잃지 않게 한다.
        for request in job.approvals:
            if request.item_id == item.item_id:
                lines.extend(_approval_block(request))

    orphans = [
        request
        for request in job.approvals
        if not any(item.item_id == request.item_id for item in job.items)
    ]
    for request in orphans:
        lines.extend(_approval_block(request))

    return "\n".join(lines).rstrip() + "\n"


def to_json(job: Job) -> str:
    """원본에 가까운 형태. 나중에 기계로 다시 읽을 때 쓴다."""
    payload = {
        "job_id": job.job_id,
        "label": job.title,
        "state": job.state.value,
        "client_id": job.client_id,
        "prompt": job.spec.prompt,
        "cwd": job.spec.cwd,
        "model": job.spec.model,
        "effort": job.spec.effort,
        "sandbox": job.spec.sandbox,
        "approval_policy": job.spec.approval_policy,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "elapsed_sec": round(job.elapsed_sec, 1),
        "token_usage": job.token_usage,
        "result": job.final_message(),
        "error": job.error,
        "items": [
            {
                "id": item.item_id,
                "type": item.item_type,
                "text": item.text,
                "completed": item.completed,
                "started_at": item.started_at,
                "payload": item.payload,
            }
            for item in job.items
        ],
        "approvals": [
            {
                "kind": request.kind,
                "method": request.method,
                "item_id": request.item_id,
                "title": request.title,
                "reason": request.reason,
                "created_at": request.created_at,
                "resolved": request.resolved,
                "decision": request.decision.value if request.decision else None,
                "params": request.params,
            }
            for request in job.approvals
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _metadata_rows(job: Job) -> list[str]:
    rows = [
        ("Job ID", job.job_id),
        ("State", _STATE_TITLES.get(job.state.value, job.state.value)),
        ("Client", job.client_id or "(not set)"),
        ("Working folder", job.spec.cwd or "(not set)"),
        ("Model", job.spec.model or "(default)"),
        ("Reasoning effort", job.spec.effort or "(default)"),
        ("Sandbox", job.spec.sandbox),
        ("Approval policy", job.spec.approval_policy),
        ("Started", _stamp(job.started_at)),
        ("Finished", _stamp(job.finished_at)),
        ("Elapsed", "{0:.1f}s".format(job.elapsed_sec)),
        ("Tokens", _tokens(job.token_usage)),
    ]
    lines = ["| Field | Value |", "| --- | --- |"]
    lines.extend("| {0} | {1} |".format(key, value) for key, value in rows)
    return lines


def _item_block(item) -> list[str]:
    title = _ITEM_TITLES.get(item.item_type, item.item_type)
    heading = "### " + title
    if item.item_type == "commandExecution":
        exit_code = item.payload.get("exitCode")
        if exit_code is not None:
            heading += " - exit {0}".format(exit_code)
    if not item.completed:
        heading += " (incomplete)"

    lines = ["", heading, ""]

    command = item.payload.get("command")
    if command:
        lines.extend(["`$ {0}`".format(command), ""])

    body = item.text.strip()
    if not body:
        lines.append("_(no content)_")
    elif item.item_type in _FENCED:
        lines.extend(["```", body, "```"])
    else:
        lines.append(body)
    return lines


def _approval_block(request: ApprovalRequest) -> list[str]:
    title = _APPROVAL_TITLES.get(request.kind, "Approval request")
    lines = ["", "> **{0}** — {1}".format(title, _stamp(request.created_at)), ">"]
    lines.append("> - Target: `{0}`".format(request.title))
    if request.reason:
        lines.append("> - Reason: {0}".format(request.reason))
    if request.decision is not None:
        lines.append(
            "> - Decision: **{0}**".format(
                _DECISION_TITLES.get(request.decision, request.decision.value)
            )
        )
    elif not request.resolved:
        lines.append("> - Decision: _pending_")
    return lines


def _stamp(value: float | None) -> str:
    if not value:
        return "-"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _tokens(usage: dict) -> str:
    total = (usage or {}).get("total") or usage or {}
    if not isinstance(total, dict) or not total.get("totalTokens"):
        return "-"
    return "{0:,} total (in {1:,} · cached {2:,} · out {3:,})".format(
        total.get("totalTokens", 0),
        total.get("inputTokens", 0),
        total.get("cachedInputTokens", 0),
        total.get("outputTokens", 0),
    )
