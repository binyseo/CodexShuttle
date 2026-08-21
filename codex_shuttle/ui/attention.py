"""주의를 끌어야 하는 곳을 주기적으로 강조한다."""

from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer

_DEFAULT_INTERVAL_MS = 600


class AttentionFlasher(QObject):
    """켜짐/꺼짐을 번갈아 콜백으로 알려 주는 깜빡임 컨트롤러.

    무엇을 어떻게 바꿀지는 호출한 쪽이 정한다. 탭 라벨 색이든 목록 행 배경이든
    같은 컨트롤러를 쓸 수 있게 하기 위함이다.
    """

    def __init__(
        self,
        apply_state: Callable[[bool], None],
        parent: QObject | None = None,
        interval_ms: int = _DEFAULT_INTERVAL_MS,
    ) -> None:
        super().__init__(parent)
        self._apply = apply_state
        self._on = False
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    @property
    def is_active(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if self._timer.isActive():
            return
        self._on = True
        self._apply(True)
        self._timer.start()

    def stop(self) -> None:
        was_active = self._timer.isActive()
        self._timer.stop()
        if was_active or self._on:
            self._on = False
            self._apply(False)

    def set_active(self, active: bool) -> None:
        self.start() if active else self.stop()

    def _tick(self) -> None:
        self._on = not self._on
        self._apply(self._on)
