"""위젯이 공유하는 색상과 스타일시트."""

from PyQt6.QtGui import QColor, QPalette

from codex_shuttle.core.status import CheckStatus

# 상태 점과 헤드라인에 쓰는 강조색. 시스템 테마가 어두우면 밝은 쪽을 쓴다.
_LIGHT = {
    CheckStatus.UNKNOWN: "#8c959f",
    CheckStatus.CHECKING: "#0969da",
    CheckStatus.OK: "#1a7f37",
    CheckStatus.WARNING: "#bf8700",
    CheckStatus.ERROR: "#cf222e",
}

_DARK = {
    CheckStatus.UNKNOWN: "#8b949e",
    CheckStatus.CHECKING: "#58a6ff",
    CheckStatus.OK: "#3fb950",
    CheckStatus.WARNING: "#d29922",
    CheckStatus.ERROR: "#f85149",
}

STATUS_TEXT = {
    CheckStatus.UNKNOWN: "Not checked",
    CheckStatus.CHECKING: "Checking",
    CheckStatus.OK: "OK",
    CheckStatus.WARNING: "Warning",
    CheckStatus.ERROR: "Error",
}

CARD_STYLE = """
QFrame#StatusCard {
    border: 1px solid palette(mid);
    border-radius: 10px;
    background-color: palette(base);
}
QLabel#CardTitle {
    font-weight: 600;
}
QToolButton#DetailToggle {
    border: none;
    padding: 0px;
}
QPlainTextEdit#DetailView {
    border: 1px solid palette(mid);
    border-radius: 6px;
    background-color: palette(alternate-base);
}
"""


def is_dark(palette: QPalette) -> bool:
    """시스템 팔레트가 어두운 테마인지."""
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def status_color(status: CheckStatus, palette: QPalette) -> str:
    """상태에 대응하는 강조색을 현재 테마에 맞춰 돌려준다."""
    table = _DARK if is_dark(palette) else _LIGHT
    return table[status]


def _tinted(palette: QPalette, alpha: int) -> str:
    """본문 글자색에 알파를 섞어 만든 색.

    팔레트에서 파생시키므로 밝은 테마와 어두운 테마 양쪽에서 대비가 유지된다.
    """
    color = QColor(palette.color(QPalette.ColorRole.WindowText))
    color.setAlpha(alpha)
    return "rgba({0}, {1}, {2}, {3})".format(
        color.red(), color.green(), color.blue(), color.alpha()
    )


def muted_color(palette: QPalette) -> str:
    """부제·라벨용 흐린 글자색."""
    return _tinted(palette, 150)


def track_color(palette: QPalette) -> str:
    """게이지의 빈 구간 색."""
    return _tinted(palette, 40)


def with_alpha(color: str, alpha: int) -> str:
    """색 문자열에 알파를 입혀 rgba 문자열로 돌려준다."""
    value = QColor(color)
    value.setAlpha(alpha)
    return "rgba({0}, {1}, {2}, {3})".format(
        value.red(), value.green(), value.blue(), value.alpha()
    )
