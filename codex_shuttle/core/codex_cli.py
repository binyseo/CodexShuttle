"""Codex CLI가 설치되어 있는지, 어떤 버전인지 확인한다."""

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from codex_shuttle.core.status import CheckStatus

INSTALL_HINT = "Install: npm install -g @openai/codex"

_VERSION_PATTERN = re.compile(r"\b(\d+\.\d+\.\d+(?:[-+.][\w.]+)?)\b")
_CHECK_TIMEOUT_MS = 15_000
_CREATE_NO_WINDOW = 0x08000000


@dataclass(slots=True)
class CodexCliInfo:
    """CLI 체크 1회의 결과 스냅샷."""

    status: CheckStatus = CheckStatus.UNKNOWN
    headline: str = "Not checked"
    executable: str | None = None
    version: str | None = None
    detail: str = ""

    @property
    def is_available(self) -> bool:
        return self.status is CheckStatus.OK


def _fallback_candidates() -> list[Path]:
    """PATH에 없을 때 흔히 설치되는 위치들.

    npm 전역 설치 경로가 PATH에 빠져 있는 경우가 잦아서, 찾지 못했다고 단정하기
    전에 몇 군데를 더 본다.
    """
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "npm" / "codex.cmd")
    home = Path.home()
    candidates.append(home / ".npm-global" / "bin" / "codex")
    candidates.append(home / ".local" / "bin" / "codex")
    candidates.append(Path("/usr/local/bin/codex"))
    # Apple Silicon Homebrew. Intel 맥의 /usr/local 과 접두사가 다르다.
    candidates.append(Path("/opt/homebrew/bin/codex"))
    return candidates


def _hide_console(process: QProcess) -> None:
    """자식 프로세스의 콘솔 창이 깜빡이지 않게 한다(Windows 전용)."""
    modifier = getattr(process, "setCreateProcessArgumentsModifier", None)
    if modifier is None:
        return

    def _apply(arguments) -> None:
        try:
            arguments.flags |= _CREATE_NO_WINDOW
        except AttributeError:
            pass

    modifier(_apply)


def configure_process(process: QProcess, executable: str, args: list[str]) -> None:
    """플랫폼에 맞게 실행 대상을 설정한다.

    Windows에서 codex는 npm이 만들어 준 codex.cmd 배치 파일이라 CreateProcess로
    직접 뜨지 않는다. 반드시 cmd.exe를 거쳐야 하는데, 경로에 공백이 있으면 cmd의
    따옴표 처리 규칙에 걸린다. /s 를 주고 명령 전체를 큰따옴표로 한 번 더 감싸면
    cmd가 바깥 따옴표만 벗겨내므로 안전하다.
    """
    if sys.platform != "win32":
        process.setProgram(executable)
        process.setArguments(args)
        return

    quoted = " ".join('"' + part + '"' for part in (executable, *args))
    process.setProgram(os.environ.get("COMSPEC") or "cmd.exe")
    process.setArguments([])
    process.setNativeArguments('/d /s /c "' + quoted + '"')
    _hide_console(process)


class CodexCliChecker(QObject):
    """codex --version 을 실행해 CLI 설치 상태를 판정한다.

    QProcess를 쓰기 때문에 Qt 이벤트 루프를 막지 않는다. 검사 시작 시 CHECKING 을,
    끝나면 확정 상태를 stateChanged 로 한 번씩 내보낸다.
    """

    stateChanged = pyqtSignal(object)  # CodexCliInfo

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._info = CodexCliInfo()
        self._process: QProcess | None = None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(_CHECK_TIMEOUT_MS)
        self._timeout.timeout.connect(self._on_timeout)

    @property
    def info(self) -> CodexCliInfo:
        """마지막으로 확정된(또는 진행 중인) 결과."""
        return self._info

    @property
    def is_running(self) -> bool:
        return self._process is not None

    def check(self) -> None:
        """검사를 시작한다. 이미 진행 중이면 무시한다."""
        if self._process is not None:
            return

        executable = self._resolve_executable()
        if executable is None:
            self._emit(
                CodexCliInfo(
                    status=CheckStatus.ERROR,
                    headline="Not installed",
                    detail="Could not find the codex executable on PATH.\n" + INSTALL_HINT,
                )
            )
            return

        self._emit(
            CodexCliInfo(
                status=CheckStatus.CHECKING,
                headline="Checking…",
                executable=executable,
            )
        )
        self._start(executable)

    @staticmethod
    def _resolve_executable() -> str | None:
        found = shutil.which("codex")
        if found:
            return found
        for candidate in _fallback_candidates():
            if candidate.exists():
                return str(candidate)
        return None

    def _start(self, executable: str) -> None:
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        configure_process(process, executable, ["--version"])
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)

        self._process = process
        self._timeout.start()
        process.start()

    def _on_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        process = self._takeover()
        if process is None:
            return

        stdout = self._decode(process.readAllStandardOutput())
        stderr = self._decode(process.readAllStandardError())
        executable = self._info.executable
        combined = "\n".join(part for part in (stdout, stderr) if part)

        if exit_code != 0:
            self._emit(
                CodexCliInfo(
                    status=CheckStatus.ERROR,
                    headline="Failed (exit {0})".format(exit_code),
                    executable=executable,
                    detail=combined or "No output.",
                )
            )
            return

        match = _VERSION_PATTERN.search(stdout) or _VERSION_PATTERN.search(stderr)
        if match is None:
            # 실행 자체는 됐으니 설치는 정상이다. 버전만 못 읽은 상황이라 경고로 둔다.
            self._emit(
                CodexCliInfo(
                    status=CheckStatus.WARNING,
                    headline="Could not read version",
                    executable=executable,
                    detail=combined or "No output.",
                )
            )
            return

        version = match.group(1)
        self._emit(
            CodexCliInfo(
                status=CheckStatus.OK,
                headline="v" + version,
                executable=executable,
                version=version,
                detail=combined,
            )
        )

    def _on_error(self, error: QProcess.ProcessError) -> None:
        process = self._takeover()
        if process is None:
            return

        if error is QProcess.ProcessError.FailedToStart:
            headline = "Cannot run"
            detail = "Could not start the process.\n" + INSTALL_HINT
        else:
            headline = "Error while running"
            detail = process.errorString()

        self._emit(
            CodexCliInfo(
                status=CheckStatus.ERROR,
                headline=headline,
                executable=self._info.executable,
                detail=detail,
            )
        )

    def _on_timeout(self) -> None:
        process = self._takeover()
        if process is None:
            return

        process.kill()
        self._emit(
            CodexCliInfo(
                status=CheckStatus.ERROR,
                headline="No response",
                executable=self._info.executable,
                detail="Gave up after {0}s with no response.".format(_CHECK_TIMEOUT_MS // 1000),
            )
        )

    def _takeover(self) -> QProcess | None:
        """진행 중인 프로세스를 회수한다.

        errorOccurred 와 finished 가 연달아 오거나 타임아웃과 겹칠 수 있어서, 결과를
        확정하는 쪽이 먼저 프로세스를 가져가고 나머지 호출은 None 을 받아 빠져나간다.
        """
        process = self._process
        if process is None:
            return None
        self._process = None
        self._timeout.stop()
        process.deleteLater()
        return process

    @staticmethod
    def _decode(data) -> str:
        return bytes(data).decode("utf-8", errors="replace").strip()

    def _emit(self, info: CodexCliInfo) -> None:
        self._info = info
        self.stateChanged.emit(info)
