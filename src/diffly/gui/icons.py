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

    if kind in ("up", "down"):
        if kind == "up":
            points = ((12, 19), (12, 5), (6, 11), (12, 5), (18, 11))
        else:
            points = ((12, 5), (12, 19), (6, 13), (12, 19), (18, 13))
        painter.drawLine(QPointF(*points[0]), QPointF(*points[1]))
        painter.drawLine(QPointF(*points[2]), QPointF(*points[3]))
        painter.drawLine(QPointF(*points[3]), QPointF(*points[4]))
    else:
        left = QRectF(2.5, 4.5, 7, 15)
        right = QRectF(14.5, 4.5, 7, 15)
        target_left = kind.endswith("left")
        frame_color = QColor(ink)
        frame_color.setAlpha(135)
        painter.setPen(QPen(frame_color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(left, 1.5, 1.5)
        painter.drawRoundedRect(right, 1.5, 1.5)
        painter.setPen(pen)
        if kind.startswith("apply"):
            if target_left:
                painter.drawLine(QPointF(18, 12), QPointF(5, 12))
                painter.drawLine(QPointF(10, 8), QPointF(5, 12))
                painter.drawLine(QPointF(10, 16), QPointF(5, 12))
            else:
                painter.drawLine(QPointF(6, 12), QPointF(19, 12))
                painter.drawLine(QPointF(14, 8), QPointF(19, 12))
                painter.drawLine(QPointF(14, 16), QPointF(19, 12))
        elif kind.startswith("delete"):
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
