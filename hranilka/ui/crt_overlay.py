"""CRT-режим (7.2): прозрачный поверх окна оверлей — горизонтальные линии
через каждые 3 px (альфа ~20) и лёгкая виньетка. Рисуется один раз в
кэш-пиксмап, пересчитывается только при изменении размера."""
from typing import Optional

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

LINE_STEP = 3
LINE_ALPHA = 20
VIGNETTE_ALPHA = 70


def render_crt_pixmap(width: int, height: int, dpr: float = 1.0) -> QPixmap:
    """Пиксмап оверлея: scanlines + виньетка (чистая функция)."""
    w, h = max(1, width), max(1, height)
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    try:
        line = QColor(0, 0, 0, LINE_ALPHA)
        for y in range(0, h, LINE_STEP):
            p.fillRect(0, y, w, 1, line)
        g = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        g.setColorAt(0.0, QColor(0, 0, 0, 0))
        g.setColorAt(0.6, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, VIGNETTE_ALPHA))
        p.fillRect(0, 0, w, h, g)
    finally:
        p.end()
    return pm


class CrtOverlay(QWidget):
    """Ребёнок окна на весь его размер; события мыши пропускает насквозь."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._host = host
        self._cache: Optional[QPixmap] = None
        self._cache_key: Optional[tuple[int, int, float]] = None
        self.render_count = 0
        host.installEventFilter(self)
        self._fit()

    def _fit(self) -> None:
        self.setGeometry(self._host.rect())
        self.raise_()

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:            # noqa: N802
        if obj is self._host and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show,
                                               QEvent.Type.ChildAdded):
            self._fit()
        return False

    def paintEvent(self, e) -> None:                                    # noqa: N802
        key = (self.width(), self.height(), self.devicePixelRatioF())
        if self._cache is None or self._cache_key != key:
            self._cache = render_crt_pixmap(*key)
            self._cache_key = key
            self.render_count += 1
        p = QPainter(self)
        try:
            p.drawPixmap(QPoint(0, 0), self._cache)
        finally:
            p.end()


def set_crt(host: QWidget, enabled: bool) -> Optional[CrtOverlay]:
    """Включить/выключить CRT-оверлей на окне (идемпотентно)."""
    existing: Optional[CrtOverlay] = host.findChild(CrtOverlay)
    if enabled:
        if existing is None:
            existing = CrtOverlay(host)
        existing.show()
        existing.raise_()
        return existing
    if existing is not None:
        existing.hide()
        host.removeEventFilter(existing)
        existing.setParent(None)
        existing.deleteLater()
    return None
