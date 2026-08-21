"""QApplication 부트스트랩."""

import sys

from PyQt6.QtWidgets import QApplication

from codex_shuttle import APP_NAME, __version__
from codex_shuttle.client.connection import (
    Connection,
    ConnectionLostError,
    NotRunningError,
)
from codex_shuttle.ui.main_window import MainWindow


def run(argv: list[str] | None = None) -> int:
    """애플리케이션을 실행하고 종료 코드를 돌려준다."""
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    # 플랫폼 기본 스타일은 OS마다 여백과 색이 달라진다. Fusion으로 고정해 둔다.
    app.setStyle("Fusion")

    # 잡 상태와 app-server를 한 곳에 모아야 하므로 인스턴스는 하나여야 한다.
    if _focus_running_instance():
        print(APP_NAME + " is already running. Brought the existing window to the front.")
        return 0

    window = MainWindow()
    if not window.start_ipc():
        print(
            "Could not open the CLI socket. Another instance may be running.",
            file=sys.stderr,
        )
        return 1

    window.show()
    return app.exec()


def _focus_running_instance() -> bool:
    """이미 떠 있는 인스턴스가 있으면 그 창을 앞으로 올리고 True를 돌려준다."""
    try:
        with Connection() as connection:
            connection.request("focus")
        return True
    except (NotRunningError, ConnectionLostError, TimeoutError, OSError, ValueError):
        return False
