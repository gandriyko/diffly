"""Small vector-drawn toolbar icons shared by Qt and native macOS toolbars."""

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def _pixmap(kind: str, color: str) -> QPixmap:
    pixmap = QPixmap(48, 48)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    ink = QColor(color)
    pen = QPen(ink, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    if kind == "refresh":
        # Clockwise arc with a gap at the upper right; the chevron sits on the arc's top end.
        painter.drawArc(QRectF(4.5, 5.5, 15, 15), 90 * 16, 300 * 16)
        painter.drawLine(QPointF(9.5, 2.5), QPointF(12.5, 5.5))
        painter.drawLine(QPointF(12.5, 5.5), QPointF(9.5, 8.5))
    elif kind in ("up", "down"):
        if kind == "up":
            points = ((12, 19), (12, 5), (6, 11), (12, 5), (18, 11))
        else:
            points = ((12, 5), (12, 19), (6, 13), (12, 19), (18, 13))
        painter.drawLine(QPointF(*points[0]), QPointF(*points[1]))
        painter.drawLine(QPointF(*points[2]), QPointF(*points[3]))
        painter.drawLine(QPointF(*points[3]), QPointF(*points[4]))
    elif kind == "paste":
        # A clipboard silhouette distinguishes Paste from the Copy sheets icon.
        painter.drawRoundedRect(QRectF(4, 5, 16, 17), 2, 2)
        painter.drawRoundedRect(QRectF(9, 2.5, 6, 5), 1.2, 1.2)
        painter.drawLine(QPointF(8, 11), QPointF(16, 11))
        painter.drawLine(QPointF(8, 15), QPointF(16, 15))
        painter.drawLine(QPointF(8, 19), QPointF(13, 19))
    elif kind in ("apply_left", "apply_right"):
        # A full-width arrow keeps the destination clear at toolbar size.
        arrow_pen = QPen(ink, 2.2)
        arrow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        arrow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(arrow_pen)
        if kind == "apply_left":
            painter.drawLine(QPointF(20, 12), QPointF(4, 12))
            painter.drawLine(QPointF(10, 6), QPointF(4, 12))
            painter.drawLine(QPointF(4, 12), QPointF(10, 18))
        else:
            painter.drawLine(QPointF(4, 12), QPointF(20, 12))
            painter.drawLine(QPointF(14, 6), QPointF(20, 12))
            painter.drawLine(QPointF(20, 12), QPointF(14, 18))
    elif kind in ("append_left", "append_right"):
        # Shift variant: the arrow moves up and a plus below says "add under", not "replace".
        arrow_pen = QPen(ink, 2.2)
        arrow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        arrow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(arrow_pen)
        if kind == "append_left":
            painter.drawLine(QPointF(20, 8), QPointF(4, 8))
            painter.drawLine(QPointF(9, 3.5), QPointF(4, 8))
            painter.drawLine(QPointF(4, 8), QPointF(9, 12.5))
        else:
            painter.drawLine(QPointF(4, 8), QPointF(20, 8))
            painter.drawLine(QPointF(15, 3.5), QPointF(20, 8))
            painter.drawLine(QPointF(20, 8), QPointF(15, 12.5))
        painter.drawLine(QPointF(12, 14.5), QPointF(12, 21.5))
        painter.drawLine(QPointF(8.5, 18), QPointF(15.5, 18))
    else:
        target_left = kind.endswith("left")
        frame_color = QColor(ink)
        frame_color.setAlpha(135)
        painter.setPen(QPen(frame_color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(2.5, 4.5, 7, 15), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(14.5, 4.5, 7, 15), 1.5, 1.5)
        painter.setPen(pen)
        center = 6 if target_left else 18
        painter.drawLine(QPointF(center - 2, 10), QPointF(center + 2, 14))
        painter.drawLine(QPointF(center + 2, 10), QPointF(center - 2, 14))
    painter.end()
    return pixmap


def toolbar_icon(kind: str, color: str) -> QIcon:
    return QIcon(_pixmap(kind, color))


def native_icon_png(kind: str) -> bytes:
    image = _pixmap(kind, "#000000")
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)
