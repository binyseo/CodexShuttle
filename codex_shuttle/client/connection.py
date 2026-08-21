"""로컬 소켓으로 GUI 프로세스에 붙는다."""

import json

from PyQt6.QtNetwork import QLocalSocket

from codex_shuttle.core.ipc import socket_name

CONNECT_TIMEOUT_MS = 2000
REQUEST_TIMEOUT_MS = 10000
# 잡이 끝날 때까지 기다릴 때는 상한을 두지 않는다. 로컬 소켓에는 HTTP 같은 유휴
# 타임아웃이 없어서, 양쪽 프로세스가 살아 있는 한 연결이 유지된다.
NO_TIMEOUT = -1

NOT_RUNNING_MESSAGE = (
    "CodexShuttle is not running. Start the GUI app first "
    "(codex-shuttle gui)."
)


class NotRunningError(RuntimeError):
    """GUI 프로세스에 붙지 못했다."""


class ConnectionLostError(RuntimeError):
    """대기 중에 연결이 끊겼다."""


class Connection:
    """요청 한 줄을 보내고 응답 한 줄을 읽는 얇은 클라이언트."""

    def __init__(self) -> None:
        self._socket = QLocalSocket()
        self._buffer = bytearray()
        self._next_id = 1

    def __enter__(self) -> "Connection":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def open(self) -> None:
        self._socket.connectToServer(socket_name())
        if not self._socket.waitForConnected(CONNECT_TIMEOUT_MS):
            raise NotRunningError(NOT_RUNNING_MESSAGE)

    def close(self) -> None:
        self._socket.disconnectFromServer()

    def request(self, op: str, timeout_ms: int = REQUEST_TIMEOUT_MS, **fields) -> dict:
        """요청을 보내고 그에 대한 응답을 돌려준다."""
        request_id = self._next_id
        self._next_id += 1
        payload = {"op": op, "id": request_id}
        payload.update(fields)
        self._write(payload)
        return self.read_message(timeout_ms)

    def read_message(self, timeout_ms: int = REQUEST_TIMEOUT_MS) -> dict:
        """한 줄을 읽어 온다. 버퍼에 이미 있으면 기다리지 않는다."""
        while True:
            message = self._take_buffered()
            if message is not None:
                return message
            if not self._socket.waitForReadyRead(timeout_ms):
                if self._socket.state() is not QLocalSocket.LocalSocketState.ConnectedState:
                    raise ConnectionLostError(
                        "Lost the connection to CodexShuttle. The app may have exited."
                    )
                raise TimeoutError("No response came back.")
            self._buffer.extend(bytes(self._socket.readAll()))

    def _take_buffered(self) -> dict | None:
        index = self._buffer.find(b"\n")
        if index < 0:
            return None
        raw = bytes(self._buffer[:index])
        del self._buffer[: index + 1]
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        return json.loads(text)

    def _write(self, payload: dict) -> None:
        self._socket.write(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        )
        self._socket.flush()
