"""GUI 프로세스와 CLI 사이의 로컬 소켓 창구.

macOS/Linux에서는 유닉스 도메인 소켓, Windows에서는 명명된 파이프로 열린다.
메시지는 app-server와 같은 방식인 줄 단위 JSON을 쓴다.

클로드 세션이 제출한 잡만 다룬다. GUI에서 사람이 만든 잡은 여기로 노출되지 않는다.
"""

import getpass
import json
import re

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from codex_shuttle.core.job import (
    ApprovalDecision,
    Job,
    JobSpec,
)
from codex_shuttle.core.job_runner import JobRunner

PROTOCOL_VERSION = 1
_CONNECT_PROBE_MS = 300

_SANDBOXES = ("read-only", "workspace-write", "danger-full-access")
_POLICIES = ("never", "on-request", "untrusted")


def socket_name() -> str:
    """소켓 이름. 여러 사용자가 한 기계를 쓸 때 충돌하지 않도록 사용자명을 붙인다."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "default"
    return "codex-shuttle-" + re.sub(r"[^A-Za-z0-9_.-]", "_", user)


def job_snapshot(job: Job) -> dict:
    """클라이언트에 돌려줄 잡 상태."""
    return {
        "job_id": job.job_id,
        "label": job.title,
        "state": job.state.value,
        "result": job.final_message(),
        "error": job.error,
        "elapsed_sec": round(job.elapsed_sec, 1),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "item_count": len(job.items),
        "pending_approvals": len(job.pending_approvals),
        "token_usage": job.token_usage,
        "cwd": job.spec.cwd,
        "model": job.spec.model,
        "sandbox": job.spec.sandbox,
        "approval_policy": job.spec.approval_policy,
    }


def build_spec(payload: dict) -> JobSpec:
    """제출 페이로드를 JobSpec으로 옮긴다. 잘못된 값은 ValueError로 튕긴다."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is empty.")

    sandbox = payload.get("sandbox") or "workspace-write"
    if sandbox not in _SANDBOXES:
        raise ValueError("Invalid sandbox value: " + str(sandbox))

    policy = payload.get("approval_policy") or "never"
    if policy not in _POLICIES:
        raise ValueError("Invalid approval_policy value: " + str(policy))

    spec = JobSpec(
        prompt=prompt,
        label=str(payload.get("label") or ""),
        cwd=payload.get("cwd") or None,
        model=payload.get("model") or None,
        effort=payload.get("effort") or None,
        sandbox=sandbox,
        approval_policy=policy,
    )

    timeout = payload.get("approval_timeout_sec")
    if timeout is not None:
        spec.approval_timeout_sec = max(1, int(timeout))
    spec.approval_timeout_decision = _decision(
        payload.get("approval_timeout_decision"), ApprovalDecision.DECLINE
    )
    raw_permission = payload.get("permission_timeout_decision")
    spec.permission_timeout_decision = (
        _decision(raw_permission, ApprovalDecision.DECLINE)
        if raw_permission is not None
        else None
    )
    return spec


def _decision(raw: object, fallback: ApprovalDecision) -> ApprovalDecision:
    if raw is None:
        return fallback
    try:
        return ApprovalDecision(str(raw))
    except ValueError:
        raise ValueError("Invalid approval decision value: " + str(raw)) from None


class LocalJobServer(QObject):
    """CLI 요청을 받아 JobRunner로 넘기고, 결과를 기다리는 쪽에 돌려준다."""

    focusRequested = pyqtSignal()

    def __init__(
        self,
        runner: JobRunner,
        environment=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        # EnvironmentMonitor. health 요청에 작업 가능 여부를 실어 주기 위해 참조한다.
        self._environment = environment
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._buffers: dict[QLocalSocket, bytearray] = {}
        # 잡이 끝나기를 기다리는 소켓들. 한 잡에 여러 명이 걸려 있어도 된다.
        self._waiters: dict[str, list[QLocalSocket]] = {}

        runner.jobChanged.connect(self._on_job_changed)
        runner.jobRemoved.connect(self._on_job_removed)

    def start(self) -> bool:
        """창구를 연다. 이미 다른 인스턴스가 떠 있으면 False.

        listen()이 성공했다고 단독이라고 볼 수 없다. Windows 명명된 파이프는 같은
        이름으로 서버가 여러 개 열리기 때문이다. 그래서 먼저 붙어 보고 판단한다.
        """
        name = socket_name()
        if self._is_instance_alive(name):
            return False

        # 죽은 프로세스가 남긴 소켓 파일이 있으면 치운다(유닉스 계열).
        QLocalServer.removeServer(name)
        return self._server.listen(name)

    @staticmethod
    def _is_instance_alive(name: str) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(name)
        alive = probe.waitForConnected(_CONNECT_PROBE_MS)
        probe.abort()
        return alive

    def stop(self) -> None:
        for socket in list(self._buffers):
            socket.disconnectFromServer()
        self._buffers.clear()
        self._waiters.clear()
        self._server.close()

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

    def _on_ready_read(self, socket: QLocalSocket) -> None:
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        while True:
            index = buffer.find(b"\n")
            if index < 0:
                break
            raw = bytes(buffer[:index])
            del buffer[: index + 1]
            self._handle_line(socket, raw)

    def _on_disconnected(self, socket: QLocalSocket) -> None:
        self._buffers.pop(socket, None)
        for job_id in list(self._waiters):
            waiters = self._waiters[job_id]
            if socket in waiters:
                waiters.remove(socket)
            if not waiters:
                self._waiters.pop(job_id, None)
        socket.deleteLater()

    def _handle_line(self, socket: QLocalSocket, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            self._send(socket, {"ok": False, "error": "Could not parse the JSON."})
            return
        if not isinstance(message, dict):
            return

        request_id = message.get("id")
        op = message.get("op")
        try:
            self._dispatch(socket, request_id, str(op), message)
        except ValueError as error:
            self._send(socket, {"id": request_id, "ok": False, "error": str(error)})

    def _dispatch(
        self, socket: QLocalSocket, request_id: object, op: str, message: dict
    ) -> None:
        client_id = str(message.get("client_id") or "")

        if op == "health":
            payload = {
                "id": request_id,
                "ok": True,
                "protocol": PROTOCOL_VERSION,
                "jobs": {
                    "active": self._runner.active_count,
                    "queued": self._runner.queued_count,
                },
            }
            if self._environment is not None:
                payload.update(self._environment.snapshot())
            self._send(socket, payload)
            return

        if op == "focus":
            self.focusRequested.emit()
            self._send(socket, {"id": request_id, "ok": True})
            return

        if op == "submit":
            self._handle_submit(socket, request_id, message, client_id)
            return

        job_id = str(message.get("job_id") or "")
        job = self._runner.client_job(job_id, client_id)
        if job is None:
            self._send(
                socket,
                {"id": request_id, "ok": False, "error": "No such job: " + job_id},
            )
            return

        if op == "cancel":
            self._runner.interrupt(job.job_id)
            self._send(socket, {"id": request_id, "ok": True, "job": job_snapshot(job)})
            return

        if op == "wait":
            self._send(socket, {"id": request_id, "ok": True, "job": job_snapshot(job)})
            self._register_wait(socket, job)
            return

        self._send(
            socket, {"id": request_id, "ok": False, "error": "Unknown request: " + op}
        )

    def _handle_submit(
        self, socket: QLocalSocket, request_id: object, message: dict, client_id: str
    ) -> None:
        spec = build_spec(message.get("spec") or {})
        job = self._runner.submit(spec, client_id=client_id)
        self._send(
            socket,
            {"id": request_id, "ok": True, "job_id": job.job_id, "job": job_snapshot(job)},
        )
        if message.get("wait"):
            self._register_wait(socket, job)

    def _register_wait(self, socket: QLocalSocket, job: Job) -> None:
        """완료 통지를 받을 소켓을 등록한다. 이미 끝난 잡이면 바로 보낸다."""
        if job.state.is_final:
            self._send_done(socket, job)
            return
        self._waiters.setdefault(job.job_id, []).append(socket)

    def _on_job_changed(self, job: Job) -> None:
        if not job.state.is_final:
            return
        for socket in self._waiters.pop(job.job_id, []):
            self._send_done(socket, job)

    def _on_job_removed(self, job_id: str) -> None:
        """지워진 잡을 기다리던 클라이언트가 영영 매달리지 않게 끊어 준다.

        보통은 완료 통지가 먼저 나가므로 대기자가 남아 있지 않지만, 사람이 목록에서
        직접 지우는 경우까지 감안한 방어다.
        """
        for socket in self._waiters.pop(job_id, []):
            self._send(
                socket,
                {
                    "type": "job.done",
                    "job": {
                        "job_id": job_id,
                        "state": "failed",
                        "result": "",
                        "error": "The job record was deleted.",
                    },
                },
            )

    def _send_done(self, socket: QLocalSocket, job: Job) -> None:
        self._send(socket, {"type": "job.done", "job": job_snapshot(job)})

    @staticmethod
    def _send(socket: QLocalSocket, payload: dict) -> None:
        if socket.state() is not QLocalSocket.LocalSocketState.ConnectedState:
            return
        socket.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        socket.flush()
