"""잡과 대화 항목, 승인 요청의 데이터 모델."""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

# thread/start 에 넘기는 값들. codex CLI의 -s / --ask-for-approval 과 같은 축이다.
SANDBOX_READ_ONLY = "read-only"
SANDBOX_WORKSPACE_WRITE = "workspace-write"
SANDBOX_DANGER_FULL_ACCESS = "danger-full-access"

APPROVAL_NEVER = "never"
APPROVAL_ON_REQUEST = "on-request"
APPROVAL_UNTRUSTED = "untrusted"


class JobOrigin(Enum):
    """잡이 어디서 들어왔는지.

    HTTP 창구는 REMOTE 잡만 보여 준다. 사람이 GUI에서 만든 잡은 클로드 세션이
    제출한 것이 아니므로, 목록·조회·이벤트 어디에도 섞이면 안 된다.
    """

    LOCAL = "local"  # GUI의 새 잡 대화상자
    REMOTE = "remote"  # 내장 HTTP 창구

    @property
    def is_visible_to_clients(self) -> bool:
        return self is JobOrigin.REMOTE


class JobState(Enum):
    """잡의 진행 상태.

    승인 대기는 별도 상태로 두지 않는다. 승인을 기다리는 동안에도 잡은 RUNNING이고,
    대기 중인 요청은 Job.pending_approvals 로 따로 센다.
    """

    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def is_final(self) -> bool:
        return self in (JobState.SUCCEEDED, JobState.FAILED, JobState.INTERRUPTED)


class ApprovalDecision(Enum):
    """승인 요청에 대한 답.

    app-server는 v2 요청과 레거시 요청이 서로 다른 문자열을 쓴다. 화면과 정책은
    이 열거형 하나로 다루고, 실제 전송값은 to_wire()에서 갈라 준다.
    """

    ACCEPT = "accept"
    ACCEPT_FOR_SESSION = "acceptForSession"
    DECLINE = "decline"
    CANCEL = "cancel"
    TIMED_OUT = "timedOut"

    def to_wire(self, *, legacy: bool) -> str:
        if legacy:
            return _LEGACY_WIRE[self]
        # v2에는 timed_out 값이 없어서 거절로 내려보낸다.
        return _V2_WIRE[self]


_V2_WIRE = {
    ApprovalDecision.ACCEPT: "accept",
    ApprovalDecision.ACCEPT_FOR_SESSION: "acceptForSession",
    ApprovalDecision.DECLINE: "decline",
    ApprovalDecision.CANCEL: "cancel",
    ApprovalDecision.TIMED_OUT: "decline",
}

_LEGACY_WIRE = {
    ApprovalDecision.ACCEPT: "approved",
    ApprovalDecision.ACCEPT_FOR_SESSION: "approved_for_session",
    ApprovalDecision.DECLINE: "denied",
    ApprovalDecision.CANCEL: "abort",
    ApprovalDecision.TIMED_OUT: "timed_out",
}


@dataclass(slots=True)
class JobSpec:
    """잡 하나를 어떻게 돌릴지에 대한 지시."""

    prompt: str
    label: str = ""
    cwd: str | None = None
    model: str | None = None
    effort: str | None = None
    sandbox: str = SANDBOX_WORKSPACE_WRITE
    approval_policy: str = APPROVAL_NEVER
    # 승인 요청에 아무도 답하지 않을 때. 무인 위임에서 턴이 영영 멈추는 걸 막는다.
    approval_timeout_sec: int = 300
    approval_timeout_decision: ApprovalDecision = ApprovalDecision.DECLINE
    # 권한 상승 요청만 다르게 처리하고 싶을 때 쓴다. 명령 실행·파일 변경은 자동으로
    # 허용하되 권한 상승은 사람 없이 통과시키지 않는 조합을 만들기 위한 것이다.
    # None이면 종류를 가리지 않고 approval_timeout_decision을 쓴다.
    permission_timeout_decision: ApprovalDecision | None = None

    def timeout_decision_for(self, kind: str) -> ApprovalDecision:
        """승인 종류에 맞는 타임아웃 처리 결정."""
        if kind == "permissions" and self.permission_timeout_decision is not None:
            return self.permission_timeout_decision
        return self.approval_timeout_decision


@dataclass(slots=True)
class JobItem:
    """대화 뷰에 그릴 항목 하나.

    text는 스트리밍 델타가 누적되는 자리이고, payload는 item/completed 로 받은
    ThreadItem 원본이다.
    """

    item_id: str
    item_type: str
    text: str = ""
    payload: dict = field(default_factory=dict)
    completed: bool = False
    started_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class ApprovalRequest:
    """서버가 올린 승인 요청 하나."""

    request_id: object
    method: str
    kind: str  # command / fileChange / permissions / userInput / unknown
    thread_id: str
    turn_id: str
    item_id: str
    title: str  # 화면에 굵게 보여줄 한 줄
    reason: str
    params: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    decision: "ApprovalDecision | None" = None

    @property
    def is_legacy(self) -> bool:
        """레거시 승인 요청인지. 응답 문자열 집합이 다르다."""
        return self.method in ("execCommandApproval", "applyPatchApproval")


@dataclass(slots=True)
class Job:
    """제출된 잡 하나의 전체 상태."""

    job_id: str
    spec: JobSpec
    # 기본값을 LOCAL로 둔다. 출처를 명시하지 않은 잡이 실수로 클로드 세션 쪽에
    # 노출되는 것보다, 안 보이는 편이 안전하다.
    origin: JobOrigin = JobOrigin.LOCAL
    client_id: str = ""
    state: JobState = JobState.QUEUED
    thread_id: str | None = None
    turn_id: str | None = None
    items: list[JobItem] = field(default_factory=list)
    # 처리된 승인도 남긴다. 대화 내역을 파일로 뽑을 때 누가 무엇을 허용했는지가
    # 함께 있어야 나중에 읽는 사람이 판단 근거를 알 수 있다.
    approvals: list[ApprovalRequest] = field(default_factory=list)
    error: str = ""
    token_usage: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def pending_approvals(self) -> list[ApprovalRequest]:
        """아직 답하지 않은 승인 요청."""
        return [request for request in self.approvals if not request.resolved]

    @property
    def title(self) -> str:
        if self.spec.label:
            return self.spec.label
        head = self.spec.prompt.strip().splitlines()
        return head[0][:60] if head else "(empty prompt)"

    @property
    def elapsed_sec(self) -> float:
        started = self.started_at or self.created_at
        return (self.finished_at or time.time()) - started

    def final_message(self) -> str:
        """클라이언트에 돌려줄 최종 응답 본문.

        codex의 마지막 어시스턴트 메시지를 결과로 본다. 없으면 빈 문자열이고,
        그 경우 error 쪽을 보게 된다.
        """
        for candidate in reversed(self.items):
            if candidate.item_type == "agentMessage" and candidate.completed:
                return candidate.text
        return ""

    def item(self, item_id: str) -> JobItem | None:
        for candidate in self.items:
            if candidate.item_id == item_id:
                return candidate
        return None

    def ensure_item(self, item_id: str, item_type: str) -> JobItem:
        """항목을 찾고 없으면 만든다.

        델타가 item/started 보다 먼저 도착해도 내용을 잃지 않도록 하기 위함이다.
        """
        existing = self.item(item_id)
        if existing is not None:
            return existing
        created = JobItem(item_id=item_id, item_type=item_type)
        self.items.append(created)
        return created


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def item_text(payload: dict) -> str:
    """완료된 ThreadItem에서 화면에 쓸 본문을 뽑는다.

    항목 타입마다 본문이 들어 있는 필드가 달라서 한 곳에 모아 둔다. 모르는 타입은
    빈 문자열을 돌려주고, 그 경우 스트리밍으로 쌓아 둔 텍스트를 그대로 쓴다.
    """
    kind = payload.get("type")
    if kind in ("agentMessage", "plan"):
        return str(payload.get("text") or "")
    if kind == "commandExecution":
        return str(payload.get("aggregatedOutput") or "")
    if kind == "reasoning":
        return _join_fragments(payload.get("summary")) or _join_fragments(
            payload.get("content")
        )
    if kind == "userMessage":
        return _join_fragments(payload.get("content"))
    return ""


def _join_fragments(value: object) -> str:
    """문자열 목록이든 {text: ...} 목록이든 하나의 본문으로 합친다."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for entry in value:
        if isinstance(entry, str):
            parts.append(entry)
        elif isinstance(entry, dict):
            text = entry.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)
