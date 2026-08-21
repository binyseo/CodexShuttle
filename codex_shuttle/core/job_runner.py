"""app-server 위에서 잡을 돌리고 스트리밍 이벤트를 잡 상태로 옮긴다."""

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from codex_shuttle.core.app_server import (
    JSONRPC_METHOD_NOT_FOUND,
    METHOD_THREAD_START,
    METHOD_THREAD_UNSUBSCRIBE,
    METHOD_TURN_INTERRUPT,
    METHOD_TURN_START,
    AppServerClient,
)
from codex_shuttle.core.job import (
    ApprovalDecision,
    ApprovalRequest,
    Job,
    JobItem,
    JobSpec,
    JobState,
    item_text,
    new_job_id,
)

# 델타 알림 -> 그 델타가 속한 항목 타입. item/started 보다 델타가 먼저 와도
# 올바른 타입으로 항목을 만들 수 있게 미리 매핑해 둔다.
_DELTA_ITEM_TYPES = {
    "item/agentMessage/delta": "agentMessage",
    "item/reasoning/textDelta": "reasoning",
    "item/reasoning/summaryTextDelta": "reasoning",
    "item/commandExecution/outputDelta": "commandExecution",
    "item/plan/delta": "plan",
}

# 우리가 답할 수 있는 승인 요청. 여기 없는 서버 요청은 오류로 거절한다.
_APPROVAL_KINDS = {
    "item/commandExecution/requestApproval": "command",
    "item/fileChange/requestApproval": "fileChange",
    "item/permissions/requestApproval": "permissions",
    "execCommandApproval": "command",
    "applyPatchApproval": "fileChange",
}

# codex가 백엔드에서 401을 받으면 클라이언트에 새 access token을 요구한다. 이 경로는
# 자격증명을 직접 소유한 클라이언트(예: Codex 데스크톱 앱)를 위한 것이고, 우리는
# ~/.codex/auth.json을 codex가 관리하도록 두므로 줄 토큰이 없다.
METHOD_AUTH_REFRESH = "account/chatgptAuthTokens/refresh"
AUTH_EXPIRED_MESSAGE = (
    "Codex auth has expired. Run codex login in a terminal again."
)

_TURN_STATUS_TO_STATE = {
    "completed": JobState.SUCCEEDED,
    "failed": JobState.FAILED,
    "interrupted": JobState.INTERRUPTED,
}

# 동시에 돌릴 잡 수. codex 턴이 그만큼 같이 돌아 토큰을 나눠 쓰므로 상한을 둔다.
DEFAULT_MAX_CONCURRENT = 5
# 끝난 잡을 몇 건까지 들고 있을지. 세션이 끊겼다 돌아와 결과를 받을 수 있어야 해서
# 바로 버리지 않는다.
DEFAULT_MAX_COMPLETED = 100


class JobRunner(QObject):
    """잡의 생명주기를 관리한다.

    thread/start 로 스레드를 만들고 turn/start 로 턴을 돌린 다음, 스트리밍 알림을
    받아 Job과 JobItem에 반영한다. 승인 요청은 화면으로 올리되, 잡마다 정해진
    시간 안에 답이 없으면 정책대로 자동 처리해 턴이 멈춰 있는 걸 막는다.
    """

    jobAdded = pyqtSignal(object)  # Job
    jobChanged = pyqtSignal(object)  # Job
    itemChanged = pyqtSignal(object, object)  # Job, JobItem
    approvalRequested = pyqtSignal(object, object)  # Job, ApprovalRequest
    approvalResolved = pyqtSignal(object, object)  # Job, ApprovalRequest
    authExpired = pyqtSignal(str)  # 안내 문구
    jobRemoved = pyqtSignal(str)  # 보관 한도를 넘겨 버려진 job_id

    def __init__(
        self,
        client: AppServerClient,
        parent: QObject | None = None,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        max_completed: int = DEFAULT_MAX_COMPLETED,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._queue: list[str] = []
        self._by_thread: dict[str, Job] = {}
        self._approvals: dict[object, tuple[Job, ApprovalRequest, QTimer]] = {}
        self._max_concurrent = max(1, max_concurrent)
        self._max_completed = max(0, max_completed)

        client.notified.connect(self._on_notified)
        client.serverRequest.connect(self._on_server_request)
        client.disconnected.connect(self._on_disconnected)

    def jobs(self) -> list[Job]:
        """제출 순서대로 잡 목록을 돌려준다. GUI용이라 출처를 가리지 않는다."""
        return [self._jobs[job_id] for job_id in self._order]

    def client_jobs(self, client_id: str = "") -> list[Job]:
        """HTTP 창구에 노출할 잡을 고른다.

        client_id를 주면 그 클라이언트가 낸 잡으로 좁힌다. 비워 두면 전부다.
        """
        return [
            job
            for job in self.jobs()
            if not client_id or job.client_id == client_id
        ]

    def job(self, job_id: str) -> Job | None:
        """GUI용 조회. 출처를 가리지 않는다."""
        return self._jobs.get(job_id)

    def client_job(self, job_id: str, client_id: str = "") -> Job | None:
        """HTTP 창구용 조회. 다른 클라이언트의 잡은 id를 알아도 돌려주지 않는다."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if client_id and job.client_id != client_id:
            return None
        return job

    def submit(self, spec: JobSpec, client_id: str = "") -> Job:
        """잡을 제출한다. 실패해도 Job 객체는 항상 돌려준다."""
        job = Job(job_id=new_job_id(), spec=spec, client_id=client_id)
        self._jobs[job.job_id] = job
        self._order.append(job.job_id)
        self._queue.append(job.job_id)
        self.jobAdded.emit(job)

        if not self._client.is_ready:
            self._queue.remove(job.job_id)
            self._fail(job, "The app-server is not ready, so the job cannot start.")
            return job

        # 상한에 걸리면 QUEUED로 남아 있다가 앞의 잡이 끝날 때 시작된다.
        self._pump()
        return job

    @property
    def active_count(self) -> int:
        return sum(
            1
            for job in self._jobs.values()
            if job.state in (JobState.STARTING, JobState.RUNNING)
        )

    @property
    def queued_count(self) -> int:
        return len(self._queue)

    def forget(self, job_id: str) -> bool:
        """끝난 잡 하나를 기록에서 지운다.

        진행 중인 잡은 지우지 않는다. 먼저 중단해야 한다.
        """
        job = self._jobs.get(job_id)
        if job is None or not job.state.is_final:
            return False
        self._jobs.pop(job_id, None)
        if job_id in self._order:
            self._order.remove(job_id)
        self.jobRemoved.emit(job_id)
        return True

    def forget_finished(self) -> int:
        """끝난 잡을 모두 지우고 지운 개수를 돌려준다."""
        removed = 0
        for job_id in [j for j in list(self._order) if self._jobs[j].state.is_final]:
            if self.forget(job_id):
                removed += 1
        return removed

    def _pump(self) -> None:
        """여유가 생기면 대기 중인 잡을 제출 순서대로 시작한다."""
        while self._queue and self.active_count < self._max_concurrent:
            job_id = self._queue.pop(0)
            job = self._jobs.get(job_id)
            if job is None or job.state is not JobState.QUEUED:
                continue
            self._start_thread(job)

    def interrupt(self, job_id: str) -> None:
        """진행 중인 턴을 중단한다."""
        job = self._jobs.get(job_id)
        if job is None or job.state.is_final:
            return
        if not job.thread_id or not job.turn_id:
            self._finish(job, JobState.INTERRUPTED, "Stopped before it started.")
            return
        self._client.request(
            METHOD_TURN_INTERRUPT,
            {"threadId": job.thread_id, "turnId": job.turn_id},
            on_result=lambda _result: None,
            on_error=lambda message: self._note_error(job, message),
        )

    def _start_thread(self, job: Job) -> None:
        job.state = JobState.STARTING
        self.jobChanged.emit(job)

        spec = job.spec
        params = {
            "sandbox": spec.sandbox,
            "approvalPolicy": spec.approval_policy,
        }
        if spec.cwd:
            params["cwd"] = spec.cwd
        if spec.model:
            params["model"] = spec.model

        self._client.request(
            METHOD_THREAD_START,
            params,
            on_result=lambda result: self._on_thread_started(job, result),
            on_error=lambda message: self._fail(job, "thread/start failed: " + message),
        )

    def _on_thread_started(self, job: Job, result: dict) -> None:
        thread = result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not thread_id:
            self._fail(job, "The thread/start response has no thread id.")
            return

        job.thread_id = thread_id
        self._by_thread[thread_id] = job
        self._start_turn(job)

    def _start_turn(self, job: Job) -> None:
        params = {
            "threadId": job.thread_id,
            "input": [{"type": "text", "text": job.spec.prompt}],
        }
        if job.spec.effort:
            params["effort"] = job.spec.effort

        self._client.request(
            METHOD_TURN_START,
            params,
            on_result=lambda result: self._on_turn_started(job, result),
            on_error=lambda message: self._fail(job, "turn/start failed: " + message),
        )

    def _on_turn_started(self, job: Job, result: dict) -> None:
        turn = result.get("turn")
        if isinstance(turn, dict):
            job.turn_id = turn.get("id") or job.turn_id
        if job.state is not JobState.RUNNING:
            job.state = JobState.RUNNING
            job.started_at = time.time()
        self.jobChanged.emit(job)

    def _on_notified(self, method: str, params: dict) -> None:
        job = self._resolve_job(params)
        if job is None:
            return

        if method in _DELTA_ITEM_TYPES:
            self._append_delta(job, method, params)
        elif method == "item/started":
            self._apply_item(job, params.get("item"), completed=False)
        elif method == "item/completed":
            self._apply_item(job, params.get("item"), completed=True)
        elif method == "turn/started":
            self._on_turn_started(job, params)
        elif method == "turn/completed":
            self._on_turn_completed(job, params)
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage")
            if isinstance(usage, dict):
                job.token_usage = usage
                self.jobChanged.emit(job)
        elif method == "error":
            self._note_error(job, str(params.get("message") or "Unknown error"))

    def _resolve_job(self, params: dict) -> Job | None:
        thread_id = params.get("threadId")
        if not thread_id:
            thread = params.get("thread")
            if isinstance(thread, dict):
                thread_id = thread.get("id")
        if not isinstance(thread_id, str):
            return None
        return self._by_thread.get(thread_id)

    def _append_delta(self, job: Job, method: str, params: dict) -> None:
        item_id = params.get("itemId")
        delta = params.get("delta")
        if not isinstance(item_id, str) or not isinstance(delta, str):
            return
        item = job.ensure_item(item_id, _DELTA_ITEM_TYPES[method])
        item.text += delta
        self.itemChanged.emit(job, item)

    def _apply_item(self, job: Job, payload: object, *, completed: bool) -> None:
        if not isinstance(payload, dict):
            return
        item_id = payload.get("id")
        if not isinstance(item_id, str):
            return

        item = job.ensure_item(item_id, str(payload.get("type") or "unknown"))
        item.item_type = str(payload.get("type") or item.item_type)
        item.payload = payload
        item.completed = completed

        # 완료 시점의 본문이 스트리밍으로 쌓은 것보다 정확하다. 다만 해당 타입의
        # 본문 필드를 모르는 경우에는 쌓아 둔 텍스트를 유지한다.
        canonical = item_text(payload)
        if canonical:
            item.text = canonical

        self.itemChanged.emit(job, item)

    def _on_turn_completed(self, job: Job, params: dict) -> None:
        turn = params.get("turn")
        if not isinstance(turn, dict):
            return
        status = turn.get("status")
        state = _TURN_STATUS_TO_STATE.get(str(status))
        if state is None:
            return

        message = ""
        error = turn.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "")
            details = error.get("additionalDetails")
            if details:
                message = message + "\n" + str(details)

        self._finish(job, state, message)

    def _on_server_request(self, method: str, params: dict, request_id: object) -> None:
        if method == METHOD_AUTH_REFRESH:
            self._on_auth_refresh(request_id)
            return

        kind = _APPROVAL_KINDS.get(method)
        job = self._resolve_job(params)

        if kind is None or job is None:
            # 답하지 않으면 턴이 멈추므로, 처리할 수 없어도 반드시 오류로 답한다.
            self._client.respond_error(
                request_id,
                JSONRPC_METHOD_NOT_FOUND,
                "CodexShuttle does not handle this request: " + method,
            )
            if job is not None:
                self._note_error(job, "Unhandled server request: " + method)
            return

        request = ApprovalRequest(
            request_id=request_id,
            method=method,
            kind=kind,
            thread_id=str(params.get("threadId") or ""),
            turn_id=str(params.get("turnId") or ""),
            item_id=str(params.get("itemId") or ""),
            title=_approval_title(kind, params),
            reason=str(params.get("reason") or ""),
            params=params,
        )

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(max(1, job.spec.approval_timeout_sec) * 1000)
        timer.timeout.connect(
            lambda: self.resolve_approval(
                request_id, job.spec.timeout_decision_for(kind), timed_out=True
            )
        )

        self._approvals[request_id] = (job, request, timer)
        job.approvals.append(request)
        timer.start()

        self.approvalRequested.emit(job, request)
        self.jobChanged.emit(job)

    def _on_auth_refresh(self, request_id: object) -> None:
        """codex가 401을 받아 새 access token을 요구한 경우.

        줄 토큰이 없으므로 거절하는 것이 맞는 응답이다. 다만 화면에서 원인을 알 수
        있도록 안내 문구를 남긴다. 이 요청에는 threadId가 실려 오지 않아 어느 잡
        때문인지 특정할 수 없으므로, 진행 중인 잡 전부에 표시한다.
        """
        self._client.respond_error(
            request_id, JSONRPC_METHOD_NOT_FOUND, AUTH_EXPIRED_MESSAGE
        )
        for job in self.jobs():
            if not job.state.is_final:
                self._note_error(job, AUTH_EXPIRED_MESSAGE)
        self.authExpired.emit(AUTH_EXPIRED_MESSAGE)

    def resolve_approval(
        self,
        request_id: object,
        decision: ApprovalDecision,
        *,
        permissions: dict | None = None,
        timed_out: bool = False,
    ) -> None:
        """승인 요청에 답한다.

        permissions는 권한 요청에서만 쓰며, 생략하면 요청받은 프로필을 그대로
        허용한다(요청과 응답 스키마가 동일하다). 일부만 허용하려면 잘라낸 프로필을
        넘긴다.
        """
        entry = self._approvals.pop(request_id, None)
        if entry is None:
            return
        job, request, timer = entry
        timer.stop()
        timer.deleteLater()

        # resolved 표시만 하면 pending_approvals에서 자동으로 빠진다.
        request.resolved = True
        request.decision = decision

        self._client.respond(request_id, _approval_result(request, decision, permissions))

        if timed_out:
            self._note_error(
                job,
                "No approval answer within {0}s, so it was resolved as {1}: {2}".format(
                    job.spec.approval_timeout_sec, decision.value, request.title
                ),
            )

        self.approvalResolved.emit(job, request)
        self.jobChanged.emit(job)

    def _on_disconnected(self, reason: str) -> None:
        for request_id in list(self._approvals):
            _job, _request, timer = self._approvals.pop(request_id)
            timer.stop()
            timer.deleteLater()
        for job in self.jobs():
            if not job.state.is_final:
                self._finish(job, JobState.FAILED, reason)

    def _note_error(self, job: Job, message: str) -> None:
        """잡을 끝내지 않고 오류 메모만 남긴다."""
        job.error = message if not job.error else job.error + "\n" + message
        self.jobChanged.emit(job)

    def _fail(self, job: Job, message: str) -> None:
        self._finish(job, JobState.FAILED, message)

    def _finish(self, job: Job, state: JobState, message: str) -> None:
        if job.state.is_final:
            return
        job.state = state
        job.finished_at = time.time()
        if message:
            job.error = message if not job.error else job.error + "\n" + message
        if job.thread_id:
            self._by_thread.pop(job.thread_id, None)
            self._release_thread(job.thread_id)
        if job.job_id in self._queue:
            self._queue.remove(job.job_id)
        self.jobChanged.emit(job)

        # 자리가 났으니 대기 중인 잡을 올리고, 오래된 기록은 정리한다.
        self._pump()
        self._trim_history()

    def _release_thread(self, thread_id: str) -> None:
        """끝난 잡의 codex 스레드를 놓아 준다.

        thread/start로 연 스레드는 놓아 주지 않으면 app-server 메모리에 계속
        남는다. 잡 하나가 세션 하나인 구조라 끝나는 즉시 정리한다. 이 메서드가
        없는 codex 버전도 있을 수 있어 실패는 조용히 넘긴다.
        """
        if not self._client.is_ready:
            return
        self._client.request(
            METHOD_THREAD_UNSUBSCRIBE,
            {"threadId": thread_id},
            on_result=lambda _result: None,
            on_error=lambda _message: None,
        )

    def _trim_history(self) -> None:
        """끝난 잡을 보관 한도까지만 남긴다. 진행 중인 잡은 세지 않는다."""
        finished = [
            job_id for job_id in self._order if self._jobs[job_id].state.is_final
        ]
        excess = len(finished) - self._max_completed
        if excess <= 0:
            return
        for job_id in finished[:excess]:
            self._jobs.pop(job_id, None)
            self._order.remove(job_id)
            self.jobRemoved.emit(job_id)


def _approval_title(kind: str, params: dict) -> str:
    if kind == "command":
        return str(params.get("command") or "Command approval")
    if kind == "fileChange":
        root = params.get("grantRoot")
        return "File change approval" + (" · " + str(root) if root else "")
    if kind == "permissions":
        return "Elevated permission"
    return "Approval required"


def _approval_result(
    request: ApprovalRequest, decision: ApprovalDecision, permissions: dict | None
) -> dict:
    if request.kind != "permissions":
        return {"decision": decision.to_wire(legacy=request.is_legacy)}

    granted = decision in (
        ApprovalDecision.ACCEPT,
        ApprovalDecision.ACCEPT_FOR_SESSION,
    )
    if not granted:
        # 빈 프로필이 곧 거절이다. 이 요청에는 decision 필드가 없다.
        return {"permissions": {}, "scope": "turn"}

    profile = permissions
    if profile is None:
        requested = request.params.get("permissions")
        profile = requested if isinstance(requested, dict) else {}
    scope = "session" if decision is ApprovalDecision.ACCEPT_FOR_SESSION else "turn"
    return {"permissions": profile, "scope": scope}
