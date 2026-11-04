"""Классические иконки сообщений Win98 (предупреждение / ошибка / вопрос /
информация), нарисованные QPainter в коде. Кэш по (вид, размер)."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF

KINDS = ("warning", "error", "question", "info")
DEFAULT_SIZE = 32

_OUTLINE = QColor("#000000")
_SHADOW = QColor(0, 0, 0, 90)
_cache: dict[tuple[str, int], QPixmap] = {}


def _glyph(p: QPainter, rect: QRectF, text: str, color: QColor, px: float) -> None:
    font = QFont("Arial")
    font.setPixelSize(max(6, int(px)))
    font.setBold(True)
    p.setFont(font)
    p.setPen(color)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def _draw_warning(p: QPainter, s: float) -> None:
    tri = QPolygonF([QPointF(s * 0.5, s * 0.06), QPointF(s * 0.96, s * 0.90),
                     QPointF(s * 0.04, s * 0.90)])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_SHADOW)
    p.drawPolygon(tri.translated(s * 0.04, s * 0.04))
    p.setPen(QPen(_OUTLINE, max(1.0, s / 24), Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
    p.setBrush(QColor("#FFFF00"))
    p.drawPolygon(tri)
    _glyph(p, QRectF(0, s * 0.30, s, s * 0.60), "!", _OUTLINE, s * 0.50)


def _draw_disc(p: QPainter, s: float, fill: QColor) -> QRectF:
    rect = QRectF(s * 0.06, s * 0.06, s * 0.86, s * 0.86)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_SHADOW)
    p.drawEllipse(rect.translated(s * 0.04, s * 0.04))
    p.setPen(QPen(_OUTLINE, max(1.0, s / 24)))
    p.setBrush(fill)
    p.drawEllipse(rect)
    return rect


def _draw_error(p: QPainter, s: float) -> None:
    _draw_disc(p, s, QColor("#FF0000"))
    pen = QPen(QColor("#FFFFFF"), max(2.0, s * 0.11), Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.SquareCap)
    p.setPen(pen)
    a, b = s * 0.30, s * 0.64
    p.drawLine(QPointF(a, a), QPointF(b, b))
    p.drawLine(QPointF(b, a), QPointF(a, b))


def _draw_question(p: QPainter, s: float) -> None:
    rect = _draw_disc(p, s, QColor("#0000FF"))
    _glyph(p, rect, "?", QColor("#FFFFFF"), s * 0.60)


def _draw_info(p: QPainter, s: float) -> None:
    rect = _draw_disc(p, s, QColor("#0000FF"))
    _glyph(p, rect, "i", QColor("#FFFFFF"), s * 0.60)


_DRAWERS = {"warning": _draw_warning, "error": _draw_error,
            "question": _draw_question, "info": _draw_info}


def msg_icon(kind: str, size: int = DEFAULT_SIZE) -> QPixmap:
    """Иконка сообщения. Неизвестный вид -> «info». Требует QApplication."""
    if kind not in _DRAWERS:
        kind = "info"
    size = max(8, int(size))
    key = (kind, size)
    pm = _cache.get(key)
    if pm is None:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            _DRAWERS[kind](p, float(size))
        finally:
            p.end()
        _cache[key] = pm
    return pm


def clear_cache() -> None:
    _cache.clear()


__all__ = ["KINDS", "DEFAULT_SIZE", "msg_icon", "clear_cache"]
