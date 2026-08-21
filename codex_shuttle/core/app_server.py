"""codex app-server(stdio JSON-RPC)와의 연결."""

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from codex_shuttle import APP_NAME, __version__
from codex_shuttle.core.codex_cli import configure_process

# 알림 메서드 이름. codex app-server generate-json-schema 결과에서 가져왔다.
NOTIFY_RATE_LIMITS_UPDATED = "account/rateLimits/updated"
NOTIFY_ACCOUNT_UPDATED = "account/updated"

METHOD_ACCOUNT_READ = "account/read"
METHOD_RATE_LIMITS_READ = "account/rateLimits/read"
METHOD_MODEL_LIST = "model/list"
# 활성 모델 provider 확인용. 구버전 CLI에는 없어서 실패할 수 있다.
METHOD_CONFIG_READ = "config/read"
METHOD_THREAD_START = "thread/start"
METHOD_TURN_START = "turn/start"
METHOD_TURN_INTERRUPT = "turn/interrupt"
METHOD_THREAD_UNSUBSCRIBE = "thread/unsubscribe"

# JSON-RPC 표준 오류 코드. 처리할 수 없는 서버 요청을 거절할 때 쓴다.
JSONRPC_METHOD_NOT_FOUND = -32601

_REQUEST_TIMEOUT_MS = 30_000
_STDERR_KEEP_LINES = 40
_TERMINATE_WAIT_MS = 2000
_KILL_WAIT_MS = 1000
_CREATE_NO_WINDOW = 0x08000000


def _kill_tree(pid: int) -> None:
    """cmd.exe를 거쳐 띄운 자식까지 함께 종료한다(Windows 전용).

    `cmd /c codex app-server` 로 실행하면 cmd가 부모로 남아, cmd만 죽여서는 실제
    codex 프로세스가 고아가 된다. 평소에는 stdin이 닫히면 codex가 스스로 끝나므로
    이 경로를 타지 않고, 정상 종료가 실패했을 때만 쓰는 마지막 수단이다.
    """
    if sys.platform != "win32" or pid <= 0:
        return
    subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(pid)],
        capture_output=True,
        check=False,
        creationflags=_CREATE_NO_WINDOW,
    )

ResultCallback = Callable[[dict], None]
ErrorCallback = Callable[[str], None]


@dataclass(slots=True)
class _Pending:
    on_result: ResultCallback
    on_error: ErrorCallback | None
    timer: QTimer


class AppServerClient(QObject):
    """codex app-server 프로세스를 띄우고 JSON-RPC로 대화한다.

    프로세스는 UI가 살아 있는 동안 유지한다. 요청은 id로 구분해 콜백에 연결하고,
    서버가 먼저 보내는 알림은 notified 시그널로 흘린다. 응답은 요청 순서대로 오지
    않으므로 반드시 id로 짝을 맞춘다.
    """

    connected = pyqtSignal()
    disconnected = pyqtSignal(str)  # 사유
    notified = pyqtSignal(str, dict)  # method, params
    # 서버가 클라이언트에 보내는 요청(승인 등). 반드시 respond()로 답해야 턴이 진행된다.
    serverRequest = pyqtSignal(str, dict, object)  # method, params, request_id

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._pending: dict[int, _Pending] = {}
        self._buffer = bytearray()
        self._stderr: list[str] = []
        self._next_id = 1
        self._ready = False

    @property
    def is_ready(self) -> bool:
        """initialize 응답까지 끝나 요청을 받을 수 있는 상태인지."""
        return self._ready

    @property
    def is_running(self) -> bool:
        return self._process is not None

    def start(self, executable: str) -> None:
        """app-server를 띄우고 initialize 핸드셰이크를 보낸다."""
        if self._process is not None:
            return

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        configure_process(process, executable, ["app-server"])
        process.readyReadStandardOutput.connect(self._on_stdout)
        process.readyReadStandardError.connect(self._on_stderr)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_process_error)

        self._process = process
        self._buffer = bytearray()
        self._stderr = []
        self._ready = False
        process.start()
        self._handshake()

    def stop(self) -> None:
        """프로세스를 정리한다. 창을 닫을 때 호출한다."""
        process, self._process = self._process, None
        self._ready = False
        self._fail_pending("The app-server was shut down.")
        if process is None:
            return
        process.readyReadStandardOutput.disconnect()
        process.readyReadStandardError.disconnect()
        process.finished.disconnect()
        process.errorOccurred.disconnect()
        pid = int(process.processId())
        # stdin을 닫으면 app-server가 스스로 끝난다. 이게 정상 경로다.
        process.closeWriteChannel()
        process.terminate()
        if not process.waitForFinished(_TERMINATE_WAIT_MS):
            _kill_tree(pid)
            process.kill()
            process.waitForFinished(_KILL_WAIT_MS)
        process.deleteLater()

    def request(
        self,
        method: str,
        params: dict | None = None,
        *,
        on_result: ResultCallback,
        on_error: ErrorCallback | None = None,
    ) -> None:
        """요청을 보내고 응답이 오면 콜백을 호출한다."""
        if not self._ready:
            self._invoke_error(on_error, "The app-server is not ready yet.")
            return
        self._send(method, params, on_result, on_error)

    def _handshake(self) -> None:
        params = {
            "clientInfo": {"name": APP_NAME, "version": __version__},
            "capabilities": {},
        }
        self._send(
            "initialize",
            params,
            self._on_initialized,
            lambda message: self._shutdown_with("initialize failed: " + message),
        )

    def _on_initialized(self, _result: dict) -> None:
        self._ready = True
        self.connected.emit()

    def _send(
        self,
        method: str,
        params: dict | None,
        on_result: ResultCallback,
        on_error: ErrorCallback | None,
    ) -> None:
        process = self._process
        if process is None:
            self._invoke_error(on_error, "The app-server is not running.")
            return

        request_id = self._next_id
        self._next_id += 1

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(_REQUEST_TIMEOUT_MS)
        timer.timeout.connect(lambda: self._on_request_timeout(request_id, method))
        self._pending[request_id] = _Pending(on_result, on_error, timer)
        timer.start()

        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )

    def _on_stdout(self) -> None:
        process = self._process
        if process is None:
            return
        self._buffer.extend(bytes(process.readAllStandardOutput()))
        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                break
            raw = bytes(self._buffer[:index])
            del self._buffer[: index + 1]
            self._dispatch(raw)

    def _dispatch(self, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            # 프로토콜과 무관한 출력이 섞일 수 있다. 진단용으로만 남긴다.
            self._remember_stderr("Not JSON: " + text[:200])
            return
        if not isinstance(message, dict):
            return

        request_id = message.get("id")
        if request_id is not None and ("result" in message or "error" in message):
            self._resolve(request_id, message)
            return

        method = message.get("method")
        if not method:
            return
        params = message.get("params")
        params = params if isinstance(params, dict) else {}

        if request_id is None:
            self.notified.emit(method, params)
            return

        # id가 붙은 method는 서버가 응답을 기다리는 요청이다. 답하지 않으면 턴이 멈춘다.
        self.serverRequest.emit(method, params, request_id)

    def respond(self, request_id: object, result: dict) -> None:
        """서버 요청에 결과를 돌려준다."""
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def respond_error(self, request_id: object, code: int, message: str) -> None:
        """처리할 수 없는 서버 요청을 오류로 거절한다."""
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    def _write(self, payload: dict) -> None:
        process = self._process
        if process is None:
            return
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        process.write(line.encode("utf-8"))

    def _resolve(self, request_id: object, message: dict) -> None:
        pending = self._pending.pop(request_id, None) if isinstance(request_id, int) else None
        if pending is None:
            return
        pending.timer.stop()
        pending.timer.deleteLater()

        error = message.get("error")
        if error is not None:
            detail = error.get("message") if isinstance(error, dict) else str(error)
            self._invoke_error(pending.on_error, detail or "Unknown error")
            return

        result = message.get("result")
        pending.on_result(result if isinstance(result, dict) else {})

    def _on_request_timeout(self, request_id: int, method: str) -> None:
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        pending.timer.deleteLater()
        seconds = _REQUEST_TIMEOUT_MS // 1000
        self._invoke_error(
            pending.on_error, "{0} did not answer within {1}s.".format(method, seconds)
        )

    def _on_stderr(self) -> None:
        process = self._process
        if process is None:
            return
        text = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            self._remember_stderr(line)

    def _remember_stderr(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        self._stderr.append(stripped)
        del self._stderr[:-_STDERR_KEEP_LINES]

    @property
    def stderr_tail(self) -> str:
        """최근 stderr 출력. 연결 실패를 화면에 설명할 때 쓴다."""
        return "\n".join(self._stderr)

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._shutdown_with("The app-server exited (code {0}).".format(exit_code))

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if error is QProcess.ProcessError.FailedToStart:
            self._shutdown_with("Could not start the app-server.")

    def _shutdown_with(self, reason: str) -> None:
        if self._process is None and not self._ready:
            # 이미 정리된 뒤 중복 호출된 경우
            if not self._pending:
                return
        self._process = None
        self._ready = False
        self._fail_pending(reason)
        self.disconnected.emit(reason)

    def _fail_pending(self, reason: str) -> None:
        pending, self._pending = self._pending, {}
        for item in pending.values():
            item.timer.stop()
            item.timer.deleteLater()
            self._invoke_error(item.on_error, reason)

    @staticmethod
    def _invoke_error(callback: ErrorCallback | None, message: str) -> None:
        if callback is not None:
            callback(message)
