import os
from pathlib import Path
import re
import sys
from threading import Event
from time import monotonic
import traceback

from PyQt5.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRectF,
    QSequentialAnimationGroup,
    QThread,
    QTimer,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QMovie,
    QPainter,
    QPainterPath,
    QPen,
    QStandardItem,
    QStandardItemModel,
)
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from automation.workflow import DOCKET_BATCH_SIZE, run_searches
from config.app_settings import APP_NAME, CIRCUIT_COURTS, RUNS_ROOT
from utils.docket_input import (
    default_output_folder_for_court,
    docket_count,
    docket_lines,
    merge_dockets,
    normalize_dockets,
    parse_ca_feedback_rows,
    parse_court_coded_dockets,
    parse_feedback_renamed_rows,
    parse_usap_docket_rows,
)
from utils.directory_input import parse_pdf_directory
from utils.user_settings import clear_saved_user_id, load_saved_user_id, save_user_id


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def asset_path(filename):
    base_path = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return base_path / "assets" / filename


class CustomTitleBar(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.drag_position = None
        self.setObjectName("TitleBar")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_position is not None and event.buttons() & Qt.LeftButton:
            self.window.move(event.globalPos() - self.drag_position)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_position = None
        super().mouseReleaseEvent(event)


class DocketLineEdit(QLineEdit):
    double_clicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class ThemedMessageDialog(QDialog):
    def __init__(self, parent, title, message, kind="info", button_text="OK"):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if parent and parent.windowIcon():
            self.setWindowIcon(parent.windowIcon())

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)

        card = QFrame()
        card.setObjectName("PromptCard")
        card.setProperty("kind", kind)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 160))
        card.setGraphicsEffect(shadow)
        root_layout.addWidget(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 20, 22, 20)
        card_layout.setSpacing(14)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        badge = QLabel(self._badge_text(kind))
        badge.setObjectName("PromptBadge")
        badge.setProperty("kind", kind)
        badge.setAlignment(Qt.AlignCenter)
        header_row.addWidget(badge)

        title_label = QLabel(title)
        title_label.setObjectName("PromptTitle")
        title_label.setWordWrap(True)
        header_row.addWidget(title_label, 1)
        card_layout.addLayout(header_row)

        message_label = QLabel(message)
        message_label.setObjectName("PromptMessage")
        message_label.setWordWrap(True)
        message_label.setMinimumWidth(380)
        message_label.setMaximumWidth(500)
        card_layout.addWidget(message_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        ok_button = QPushButton(button_text)
        ok_button.setObjectName("PromptButton")
        ok_button.setProperty("kind", kind)
        ok_button.clicked.connect(self.accept)
        button_row.addWidget(ok_button)
        card_layout.addLayout(button_row)

        self.setStyleSheet(
            """
            QDialog {
                background-color: transparent;
            }
            QFrame#PromptCard {
                background-color: #101722;
                border: 1px solid #22304a;
                border-radius: 16px;
            }
            QFrame#PromptCard[kind="success"] {
                border-color: #22d3ee;
            }
            QFrame#PromptCard[kind="warning"] {
                border-color: #f59e0b;
            }
            QFrame#PromptCard[kind="error"] {
                border-color: #fb7185;
            }
            QLabel#PromptBadge {
                min-width: 54px;
                max-width: 54px;
                min-height: 24px;
                max-height: 24px;
                border-radius: 12px;
                color: #04131a;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #67e8f9, stop:1 #2563eb);
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 0px;
            }
            QLabel#PromptBadge[kind="success"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #86efac, stop:1 #22d3ee);
            }
            QLabel#PromptBadge[kind="warning"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #facc15, stop:1 #fb923c);
            }
            QLabel#PromptBadge[kind="error"] {
                color: #ffffff;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #fb7185, stop:1 #7f1d1d);
            }
            QLabel#PromptTitle {
                color: #f8fafc;
                font-size: 18px;
                font-weight: 900;
            }
            QLabel#PromptMessage {
                color: #dbe7f5;
                font-size: 13px;
            }
            QPushButton#PromptButton {
                min-width: 104px;
                min-height: 36px;
                border-radius: 10px;
                padding: 0 18px;
                color: #03131a;
                font-size: 12px;
                font-weight: 800;
                border: 1px solid #7dd3fc;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6ff7ff,
                    stop:0.35 #22d3ee,
                    stop:0.72 #5b7cff,
                    stop:1 #f0f9ff
                );
            }
            QPushButton#PromptButton:hover {
                border-color: #f0ff5f;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00f5ff,
                    stop:0.35 #38bdf8,
                    stop:0.72 #d946ef,
                    stop:1 #f0ff5f
                );
            }
            QPushButton#PromptButton:pressed {
                color: #ffffff;
                border-color: #ff2bd6;
                background: qlineargradient(
                    x1:0, y1:1, x2:1, y2:0,
                    stop:0 #5b21b6,
                    stop:0.35 #2563eb,
                    stop:0.72 #06b6d4,
                    stop:1 #ff2bd6
                );
            }
            QPushButton#PromptButton[kind="error"] {
                color: #ffffff;
                border-color: #fb7185;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #be123c, stop:1 #7f1d1d);
            }
            QPushButton#PromptButton[kind="warning"] {
                border-color: #facc15;
            }
            """
        )

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent:
            self.move(parent.frameGeometry().center() - self.rect().center())

    @staticmethod
    def _badge_text(kind):
        return {
            "success": "DONE",
            "warning": "CHECK",
            "error": "ISSUE",
        }.get(kind, "INFO")


class CourtMultiSelectCombo(QComboBox):
    selection_changed = pyqtSignal()

    def __init__(self, court_labels, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setFrame(False)
        self.lineEdit().setPlaceholderText("Select Court")
        self.setInsertPolicy(QComboBox.NoInsert)

        model = QStandardItemModel(self)
        for label in court_labels:
            item = QStandardItem(label)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setData(Qt.Unchecked, Qt.CheckStateRole)
            model.appendRow(item)
        self.setModel(model)
        self.view().pressed.connect(self._toggle_item)
        self._refresh_label()

    def checked_items(self):
        labels = []
        for row in range(self.model().rowCount()):
            item = self.model().item(row)
            if item.checkState() == Qt.Checked:
                labels.append(item.text())
        return labels

    def set_checked_items(self, labels, emit=False):
        label_set = set(labels)
        for row in range(self.model().rowCount()):
            item = self.model().item(row)
            item.setCheckState(Qt.Checked if item.text() in label_set else Qt.Unchecked)
        self._refresh_label()
        if emit:
            self.selection_changed.emit()

    def _toggle_item(self, index):
        item = self.model().itemFromIndex(index)
        item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        self._refresh_label()
        self.selection_changed.emit()

    def _refresh_label(self):
        labels = self.checked_items()
        if not labels:
            text = ""
        elif len(labels) == 1:
            text = labels[0]
        else:
            text = f"{len(labels)} courts selected"
        self.lineEdit().setText(text)


class DocketListPopup(QFrame):
    closed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.anchor = None
        self.court_labels = []
        self.docket_map = {}
        self.editors = {}
        self.setObjectName("DocketPanel")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.leave_timer = QTimer(self)
        self.leave_timer.setSingleShot(True)
        self.leave_timer.timeout.connect(self._hide_if_cursor_left)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(9)

        label = QLabel("DOCKET NUMBERS")
        label.setObjectName("FieldLabel")
        layout.addWidget(label)

        self.editor_area = QScrollArea()
        self.editor_area.setWidgetResizable(True)
        self.editor_area.setFrameShape(QFrame.NoFrame)
        self.editor_host = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_layout.setSpacing(9)
        self.editor_area.setWidget(self.editor_host)
        layout.addWidget(self.editor_area)

        hint = QLabel("Comma, semicolon, or newline separated values are accepted. Suffixes like _CA9 and USAP rows auto-route courts.")
        hint.setObjectName("PanelHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.resize(540, 360)

    def show_for_field(self, field, court_labels, docket_map):
        self.anchor = field
        self.court_labels = list(court_labels)
        self.docket_map = docket_map
        self._build_editors()
        bottom_left = field.mapToGlobal(QPoint(0, field.height() + 8))
        self.move(bottom_left)
        self.show()
        self.raise_()
        first_editor = next(iter(self.editors.values()), None)
        if first_editor:
            first_editor.setFocus()
            first_editor.selectAll()
        if not self.frameGeometry().contains(QCursor.pos()):
            self.leave_timer.start(850)

    def _build_editors(self):
        while self.editor_layout.count():
            item = self.editor_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.editors = {}

        editor_height = 150 if len(self.court_labels) == 1 else 96
        for court_label in self.court_labels:
            label = QLabel(court_label.upper())
            label.setObjectName("FieldLabel")
            self.editor_layout.addWidget(label)

            editor = QPlainTextEdit()
            editor.setObjectName("DocketPanelEditor")
            editor.setPlaceholderText("One docket per line")
            editor.setPlainText(docket_lines(self.docket_map.get(court_label, "")))
            editor.setMinimumHeight(editor_height)
            editor.setMaximumHeight(editor_height)
            editor.setLineWrapMode(QPlainTextEdit.NoWrap)
            editor.setTabChangesFocus(True)
            self.editor_layout.addWidget(editor)
            self.editors[court_label] = editor

        self.editor_layout.addStretch(1)

    def _apply_to_anchor(self):
        for court_label, editor in self.editors.items():
            self.docket_map[court_label] = normalize_dockets(editor.toPlainText())

    def enterEvent(self, event):
        self.leave_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.leave_timer.start(180)
        super().leaveEvent(event)

    def focusOutEvent(self, event):
        self.leave_timer.start(80)
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self.leave_timer.stop()
        self._apply_to_anchor()
        self.closed.emit(dict(self.docket_map))
        super().hideEvent(event)

    def _hide_if_cursor_left(self):
        if not self.frameGeometry().contains(QCursor.pos()):
            self.hide()


class SettingsPopup(QFrame):
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsPanel")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 12, 13, 12)
        layout.setSpacing(10)

        title = QLabel("OPTIONS")
        title.setObjectName("SettingsPanelTitle")
        layout.addWidget(title)

        self.options_layout = QVBoxLayout()
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_layout.setSpacing(9)
        layout.addLayout(self.options_layout)

    def set_options(self, widgets):
        while self.options_layout.count():
            item = self.options_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        for widget in widgets:
            self.options_layout.addWidget(widget)

    def show_near(self, anchor):
        self.adjustSize()
        top_left = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 8))
        self.move(top_left)
        self.show()
        self.raise_()

    def hideEvent(self, event):
        self.closed.emit()
        super().hideEvent(event)


class SetupCard(QFrame):
    resized = pyqtSignal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class AnimatedProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.sheen_position = 0
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.update_sheen)
        self.animation_timer.start(28)

    def update_sheen(self):
        if self.maximum() > 0 and self.value() > 0:
            progress_width = int((self.value() / self.maximum()) * self.width())
            self.sheen_position = (self.sheen_position + 5) % max(progress_width + 90, 180)
        else:
            self.sheen_position = 0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bar_rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = bar_rect.height() / 2

        track_path = QPainterPath()
        track_path.addRoundedRect(bar_rect, radius, radius)
        painter.fillPath(track_path, QColor("#0b1220"))
        painter.setPen(QColor("#22304a"))
        painter.drawPath(track_path)

        minimum = self.minimum()
        maximum = self.maximum()
        if maximum > minimum and self.value() > minimum:
            ratio = (self.value() - minimum) / (maximum - minimum)
            ratio = max(0.0, min(1.0, ratio))
            progress_width = bar_rect.width() * ratio

            if progress_width > 0:
                filled_rect = QRectF(
                    bar_rect.left(),
                    bar_rect.top(),
                    progress_width,
                    bar_rect.height(),
                )
                fill_radius = min(radius, filled_rect.width() / 2)
                fill_path = QPainterPath()
                fill_path.addRoundedRect(filled_rect, fill_radius, fill_radius)

                fill_gradient = QLinearGradient(
                    filled_rect.left(),
                    filled_rect.center().y(),
                    filled_rect.right(),
                    filled_rect.center().y(),
                )
                fill_gradient.setColorAt(0, QColor("#22d3ee"))
                fill_gradient.setColorAt(1, QColor("#2563eb"))

                painter.fillPath(fill_path, fill_gradient)

                sheen_gradient = QLinearGradient(
                    self.sheen_position - 45,
                    0,
                    self.sheen_position + 45,
                    0,
                )
                sheen_gradient.setColorAt(0, QColor(255, 255, 255, 0))
                sheen_gradient.setColorAt(0.5, QColor(255, 255, 255, 82))
                sheen_gradient.setColorAt(1, QColor(255, 255, 255, 0))

                painter.save()
                painter.setClipPath(fill_path)
                painter.fillRect(filled_rect, sheen_gradient)
                painter.restore()

        if self.isTextVisible():
            painter.setPen(QColor("#e2e8f0"))
            painter.drawText(bar_rect, Qt.AlignCenter, self.text())


class AnimatedWatermarkFrame(QFrame):
    def __init__(self, gif_path, parent=None, opacity=0.18, radius=14):
        super().__init__(parent)
        self.target_watermark_opacity = opacity
        self.watermark_opacity = 0
        self.watermark_radius = radius
        self.watermark_active = False
        self.fade_elapsed_ms = 0
        self.fade_duration_ms = 650
        self.fade_interval_ms = 16
        self.fade_timer = QTimer(self)
        self.fade_timer.timeout.connect(self._advance_watermark_fade)
        self.movie = QMovie(str(gif_path)) if Path(gif_path).exists() else None
        if self.movie:
            self.movie.setCacheMode(QMovie.CacheAll)
            self.movie.frameChanged.connect(self.update)

    def set_watermark_active(self, active):
        next_active = bool(active and self.movie and self.movie.isValid())
        if next_active == self.watermark_active and (
            not next_active or self.watermark_opacity >= self.target_watermark_opacity
        ):
            return

        self.watermark_active = next_active
        if self.movie:
            if self.watermark_active:
                self.watermark_opacity = 0
                self.fade_elapsed_ms = 0
                if self.movie.state() != QMovie.Running:
                    self.movie.start()
                self.fade_timer.start(self.fade_interval_ms)
            else:
                self.fade_timer.stop()
                self.watermark_opacity = 0
                self.movie.stop()
        self.update()

    def _advance_watermark_fade(self):
        if not self.watermark_active:
            self.fade_timer.stop()
            return

        self.fade_elapsed_ms += self.fade_interval_ms
        progress = min(self.fade_elapsed_ms / self.fade_duration_ms, 1)
        eased = 1 - pow(1 - progress, 3)
        self.watermark_opacity = self.target_watermark_opacity * eased
        if progress >= 1:
            self.watermark_opacity = self.target_watermark_opacity
            self.fade_timer.stop()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.watermark_active or not self.movie:
            return

        pixmap = self.movie.currentPixmap()
        if pixmap.isNull():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        frame_rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip_path = QPainterPath()
        clip_path.addRoundedRect(frame_rect, self.watermark_radius, self.watermark_radius)
        painter.setClipPath(clip_path)

        scaled = pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = int((self.width() - scaled.width()) / 2)
        y = int((self.height() - scaled.height()) / 2)
        painter.setOpacity(self.watermark_opacity)
        painter.drawPixmap(x, y, scaled)


class IdleScreen(QFrame):
    clicked = pyqtSignal()
    wakeup = pyqtSignal()

    def __init__(self, gif_path, parent=None):
        super().__init__(parent)
        self.setObjectName("IdleScreen")
        self.setCursor(Qt.ArrowCursor)
        self.setMouseTracking(True)
        self._fade_opacity = 0.0
        self.movie = QMovie(str(gif_path)) if Path(gif_path).exists() else None
        if self.movie:
            self.movie.setCacheMode(QMovie.CacheAll)
            self.movie.frameChanged.connect(self.update)
            self.movie.finished.connect(self.movie.start)
        self.hide()

    def get_fade_opacity(self):
        return self._fade_opacity

    def set_fade_opacity(self, value):
        self._fade_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    fadeOpacity = pyqtProperty(float, fget=get_fade_opacity, fset=set_fade_opacity)

    def start(self):
        if self.movie and self.movie.isValid() and self.movie.state() != QMovie.Running:
            self.movie.start()

    def stop(self):
        if self.movie:
            self.movie.stop()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._emit_wakeup()
        super().mouseMoveEvent(event)

    def enterEvent(self, event):
        self._emit_wakeup()
        super().enterEvent(event)

    def _emit_wakeup(self):
        if self.isVisible() and self._fade_opacity > 0.05:
            self.wakeup.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setOpacity(self._fade_opacity)

        frame_rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip_path = QPainterPath()
        clip_path.addRoundedRect(frame_rect, 18, 18)
        painter.setClipPath(clip_path)

        if self.movie and self.movie.isValid():
            pixmap = self.movie.currentPixmap()
            if not pixmap.isNull():
                scaled = pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                x = int((self.width() - scaled.width()) / 2)
                y = int((self.height() - scaled.height()) / 2)
                painter.drawPixmap(x, y, scaled)
            else:
                painter.fillRect(self.rect(), QColor("#0b1220"))
        else:
            painter.fillRect(self.rect(), QColor("#0b1220"))

        painter.fillRect(self.rect(), QColor(3, 8, 18, 76))


class IntroOverlay(QFrame):
    def __init__(self, title, font_family="", parent=None):
        super().__init__(parent)
        self.title = title
        self.font_family = font_family or "Segoe UI"
        self.glitch_font_families = []
        self.cjk_font_families = ["Yu Gothic UI", "Microsoft YaHei UI", "Meiryo", "MS Gothic"]
        self.scramble_glyphs = "アカサタナハマヤラワン月光電脳東京未来零壱弐参四五六七八九"
        self.scramble_glyphs = "\u30a2\u30ab\u30ca\u30de\u6708\u96fb\u6771\u96f6"
        self.cjk_font_families = ["Yu Gothic UI", "Meiryo", "Malgun Gothic", "Microsoft YaHei UI", "MS Gothic"]
        self.scramble_glyphs = "\u3042\u304b\u306a\u307e\u3089\u3093\u3042\u304b\u306a\u307e\u30a2\u30ab\u30ca\uac00\ub098\ub2e4\u6708\u96fb"
        self.scramble_numbers = "0123456789"
        self.scramble_symbols = "+-\u00d7\u00f7=\u2211\u221a\u03c0"
        self._title_progress = 0.0
        self._glitch_progress = 1.0
        self._lock_glow_opacity = 0.0
        self._aurora_bloom = 0.0
        self._aurora_motion = 0.0
        self._post_lock_glitch_progress = 0.0
        self._window_dissolve = 0.0
        self._fade_opacity = 1.0
        self.start_font_size = 57
        self.end_font_size = 33
        self.target_center_x = None
        self.target_center_y = 44
        self.setObjectName("IntroOverlay")

    def set_glitch_fonts(self, font_families):
        self.glitch_font_families = []
        seen = set()
        for family in [self.font_family, *font_families]:
            if family and family not in seen:
                self.glitch_font_families.append(family)
                seen.add(family)
        self.update()

    def get_title_progress(self):
        return self._title_progress

    def set_title_progress(self, value):
        self._title_progress = max(0.0, min(1.0, float(value)))
        self.update()

    titleProgress = pyqtProperty(float, fget=get_title_progress, fset=set_title_progress)

    def get_glitch_progress(self):
        return self._glitch_progress

    def set_glitch_progress(self, value):
        self._glitch_progress = max(0.0, min(1.0, float(value)))
        self.update()

    glitchProgress = pyqtProperty(float, fget=get_glitch_progress, fset=set_glitch_progress)

    def get_lock_glow_opacity(self):
        return self._lock_glow_opacity

    def set_lock_glow_opacity(self, value):
        self._lock_glow_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    lockGlowOpacity = pyqtProperty(float, fget=get_lock_glow_opacity, fset=set_lock_glow_opacity)

    def get_aurora_bloom(self):
        return self._aurora_bloom

    def set_aurora_bloom(self, value):
        self._aurora_bloom = max(0.0, min(1.0, float(value)))
        self.update()

    auroraBloom = pyqtProperty(float, fget=get_aurora_bloom, fset=set_aurora_bloom)

    def get_aurora_motion(self):
        return self._aurora_motion

    def set_aurora_motion(self, value):
        self._aurora_motion = max(0.0, min(1.0, float(value)))
        self.update()

    auroraMotion = pyqtProperty(float, fget=get_aurora_motion, fset=set_aurora_motion)

    def get_post_lock_glitch_progress(self):
        return self._post_lock_glitch_progress

    def set_post_lock_glitch_progress(self, value):
        self._post_lock_glitch_progress = max(0.0, min(1.0, float(value)))
        self.update()

    postLockGlitchProgress = pyqtProperty(
        float,
        fget=get_post_lock_glitch_progress,
        fset=set_post_lock_glitch_progress,
    )

    def get_window_dissolve(self):
        return self._window_dissolve

    def set_window_dissolve(self, value):
        self._window_dissolve = max(0.0, min(1.0, float(value)))
        self.update()

    windowDissolve = pyqtProperty(float, fget=get_window_dissolve, fset=set_window_dissolve)

    def get_fade_opacity(self):
        return self._fade_opacity

    def set_fade_opacity(self, value):
        self._fade_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    fadeOpacity = pyqtProperty(float, fget=get_fade_opacity, fset=set_fade_opacity)

    def set_target(self, center_x, center_y, font_size=33):
        self.target_center_x = float(center_x)
        self.target_center_y = float(center_y)
        self.end_font_size = int(font_size)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 18, 18)
        painter.setClipPath(path)

        background = QLinearGradient(rect.topLeft(), rect.bottomRight())
        background.setColorAt(0, QColor("#101826"))
        background.setColorAt(1, QColor("#172033"))
        painter.save()
        painter.setOpacity(self._fade_opacity * (1.0 - self._window_dissolve))
        painter.fillPath(path, background)
        painter.restore()
        self._draw_window_dissolve(painter, rect)

        self._draw_aurora_bloom(painter, rect)

        progress = self._title_progress
        ease_in_portion = 0.12
        if progress < ease_in_portion:
            raw_progress = (progress * progress) / (2 * ease_in_portion)
        else:
            raw_progress = progress - (ease_in_portion / 2)
        smooth_progress = min(1.0, raw_progress / (1 - (ease_in_portion / 2)))
        font_size = self.start_font_size + (
            (self.end_font_size - self.start_font_size) * smooth_progress
        )

        start_x = rect.center().x()
        start_y = rect.center().y()
        end_x = self.target_center_x if self.target_center_x is not None else rect.center().x()
        end_y = self.target_center_y
        center_x = start_x + (end_x - start_x) * smooth_progress
        center_y = start_y + (end_y - start_y) * smooth_progress

        font = QFont(self.font_family)
        base_pixel_size = max(1, int(self.start_font_size))
        font.setPixelSize(base_pixel_size)
        font.setWeight(QFont.Black)
        metrics = QFontMetrics(font)
        text_width = max(1, metrics.horizontalAdvance(self.title))
        text_height = max(72, metrics.height() + 18)
        available_width = max(120, self.width() - 72)
        continuous_scale = font_size / base_pixel_size
        visual_width = text_width * continuous_scale
        scale_x = min(1.0, available_width / visual_width)

        title_screen_rect = QRectF(
            center_x - (text_width * scale_x * continuous_scale) / 2,
            center_y - (text_height * continuous_scale) / 2,
            text_width * scale_x * continuous_scale,
            text_height * continuous_scale,
        )
        self._draw_window_glitch_leaks(painter, rect, title_screen_rect)

        painter.save()
        painter.translate(center_x, center_y)
        painter.scale(scale_x * continuous_scale, continuous_scale)
        title_rect = QRectF(-text_width / 2, -text_height / 2, text_width, text_height)
        painter.setPen(QColor(248, 250, 252, int(242 * self._fade_opacity)))
        self._draw_intro_title(painter, title_rect, base_pixel_size)
        painter.restore()

    def _post_lock_glitch_state(self):
        progress = self._post_lock_glitch_progress
        if (
            progress <= 0.0
            or progress >= 1.0
            or self._title_progress > 0.001
            or self._glitch_progress < 1.0
            or self._fade_opacity <= 0.01
        ):
            return None

        beats = (0.08, 0.24, 0.42, 0.63, 0.82)
        visual_progress = self._stepped_glitch_progress(progress, beats)
        pulse = 0.0
        beat_index = 0
        for index, beat in enumerate(beats):
            width = 0.092 if index in (2, 4) else (0.088 if index in (0, 3) else 0.08)
            hit = max(0.0, 1.0 - abs(visual_progress - beat) / width)
            hit = round((hit * hit * (3 - 2 * hit)) * 4) / 4
            if hit > pulse:
                pulse = hit
                beat_index = index

        pulse = max(pulse, 0.32)
        tail_fade = max(0.12, 1.0 - max(0.0, (progress - 0.90) / 0.10))
        split = pulse * tail_fade * self._fade_opacity
        if split <= 0.015:
            return None
        return beat_index, split, visual_progress

    def _stepped_glitch_progress(self, progress, beats):
        sliced_hold_windows = {
            2: (0.072, 0.036, 5),
            4: (0.066, 0.034, 5),
        }
        for index, (window, hold, steps) in sliced_hold_windows.items():
            beat = beats[index]
            distance = abs(progress - beat)
            if distance <= hold:
                return beat
            if distance > window:
                continue
            local = (progress - (beat - window)) / (window * 2)
            local = max(0.0, min(1.0, local))
            stepped_local = round(local * steps) / steps
            return (beat - window) + stepped_local * (window * 2)

        lag_windows = {
            1: (0.03, 4),
        }
        for index, (window, steps) in lag_windows.items():
            beat = beats[index]
            if abs(progress - beat) > window:
                continue
            local = (progress - (beat - window)) / (window * 2)
            local = max(0.0, min(1.0, local))
            stepped_local = round(local * steps) / steps
            return (beat - window) + stepped_local * (window * 2)
        return round(progress * 18) / 18

    def _glitch_unit(self, seed):
        value = (int(seed) * 1103515245 + 12345) & 0x7FFFFFFF
        return value / 0x7FFFFFFF

    def _draw_window_dissolve(self, painter, rect):
        dissolve = self._window_dissolve * self._fade_opacity
        if dissolve <= 0.01:
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        grid = 14
        seed = int(self._post_lock_glitch_progress * 18) * 97
        threshold = 0.18 + 0.62 * dissolve
        for row, y in enumerate(range(int(rect.top()) + 14, int(rect.bottom()) - 14, grid)):
            for col, x in enumerate(range(int(rect.left()) + 14, int(rect.right()) - 14, grid)):
                noise = self._glitch_unit(seed + row * 47 + col * 83)
                if noise > threshold:
                    continue
                width = 5 + int(9 * self._glitch_unit(seed + row * 61 + col * 19))
                height = 3 + int(7 * self._glitch_unit(seed + row * 23 + col * 71))
                painter.fillRect(QRectF(x, y, width, height), QColor(0, 0, 0, int(255 * dissolve)))
        painter.restore()

        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Screen)
        for index in range(5):
            y = rect.top() + rect.height() * (0.18 + 0.16 * index)
            offset = ((index % 2) * 2 - 1) * (18 + 18 * self._glitch_unit(seed + index * 43)) * dissolve
            painter.fillRect(
                QRectF(rect.left() + offset, y, rect.width() * (0.34 + 0.24 * self._glitch_unit(seed + index * 59)), 1.2),
                QColor(0, 245, 255, int(42 * dissolve)),
            )
            painter.fillRect(
                QRectF(rect.right() - rect.width() * 0.42 - offset, y + 4, rect.width() * 0.28, 1.0),
                QColor(255, 39, 102, int(34 * dissolve)),
            )
        painter.restore()

    def _draw_window_glitch_leaks(self, painter, rect, title_screen_rect=None):
        state = self._post_lock_glitch_state()
        if not state:
            return
        beat_index, split, progress = state

        intensity = min(1.0, split * (1.12 if beat_index in (2, 4) else 0.88))
        if intensity <= 0.02:
            return

        frame_tick = int(progress * 18)
        frame_seed = frame_tick * 211 + beat_index * 193
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setClipRect(rect)

        # Window-wide RGB leaks: short-lived horizontal row shifts and color bleed.
        painter.setCompositionMode(QPainter.CompositionMode_Screen)
        leak_colors = (
            QColor(0, 245, 255, int(96 * intensity)),
            QColor(255, 39, 102, int(84 * intensity)),
            QColor(138, 255, 224, int(58 * intensity)),
            QColor(248, 250, 252, int(44 * intensity)),
        )
        leak_count = 7 + (3 if beat_index in (2, 4) else 0)
        for index in range(leak_count):
            n1 = self._glitch_unit(frame_seed + index * 17)
            n2 = self._glitch_unit(frame_seed + index * 31)
            n3 = self._glitch_unit(frame_seed + index * 47)
            y = rect.top() + round((24 + n1 * max(1.0, rect.height() - 54)) / 8) * 8
            height = round((2.0 + n2 * (7.0 if beat_index in (2, 4) else 4.0)) / 2) * 2
            width = rect.width() * (0.13 + 0.56 * n3)
            x = rect.left() + round(((rect.width() - width) * self._glitch_unit(frame_seed + index * 59)) / 14) * 14
            direction = -1 if index % 2 else 1
            offset = round((direction * (18 + 64 * self._glitch_unit(frame_seed + index * 71)) * intensity) / 7) * 7
            painter.fillRect(QRectF(x + offset, y, width, height), leak_colors[index % len(leak_colors)])

            if index % 3 == 0:
                painter.fillRect(
                    QRectF(
                        rect.left(),
                        y + height + 1.6,
                        rect.width(),
                        0.65,
                    ),
                    QColor(0, 245, 255, int(28 * intensity)),
                )

        # A few clipped full-title echoes make the corruption feel like it comes from the wordmark.
        ghost_font = QFont(self.font_family)
        ghost_font.setPixelSize(34)
        ghost_font.setWeight(QFont.Black)
        painter.setFont(ghost_font)
        ghost_center = QRectF(rect.left(), rect.center().y() - 44, rect.width(), 88)
        ghost_bands = (
            (0.16, 0.055, -42, QColor(0, 245, 255, int(112 * intensity))),
            (0.31, 0.050, 34, QColor(255, 39, 102, int(92 * intensity))),
            (0.77, 0.052, -24, QColor(124, 58, 237, int(70 * intensity))),
        )
        for band_index, (y_ratio, height_ratio, offset_base, color) in enumerate(ghost_bands):
            band_rect = QRectF(
                rect.left(),
                rect.top() + rect.height() * y_ratio,
                rect.width(),
                max(8.0, rect.height() * height_ratio),
            )
            painter.save()
            painter.setClipRect(band_rect)
            painter.setPen(color)
            offset = offset_base * intensity * (1.0 + 0.18 * band_index)
            painter.drawText(ghost_center.translated(offset, 0), Qt.AlignCenter, self.title)
            painter.restore()

        # Giant text shards: sparse, loud, and readable during the corruption hits.
        shard_font = QFont("Consolas")
        shard_font.setStyleHint(QFont.TypeWriter)
        shard_font.setFixedPitch(True)
        shard_font.setWeight(QFont.Black)
        if hasattr(QFont, "NoAntialias"):
            shard_font.setStyleStrategy(QFont.NoAntialias)
        shards = ("COUNSEL", "COLLECTOR", "SYSTEM", "SYNC//", "0xCC")
        shard_count = 2 if beat_index in (2, 4) else 1
        for index in range(shard_count):
            size = 68 + int((22 if beat_index in (2, 4) else 14) * self._glitch_unit(frame_seed + index * 83))
            shard_font.setPixelSize(size)
            painter.setFont(shard_font)
            text = shards[int(self._glitch_unit(frame_seed + index * 97) * len(shards)) % len(shards)]
            x = rect.left() - 70 + self._glitch_unit(frame_seed + index * 109) * max(1.0, rect.width() - 260)
            if title_screen_rect is not None:
                top_baseline = rect.top() + 118 + 12 * self._glitch_unit(frame_seed + index * 127)
                bottom_baseline = rect.bottom() - 42 - 12 * self._glitch_unit(frame_seed + index * 131)
                y = top_baseline if (index + beat_index + frame_tick) % 2 == 0 else bottom_baseline
            else:
                y = rect.top() + 118 + self._glitch_unit(frame_seed + index * 127) * max(1.0, rect.height() - 214)
            x = rect.left() + round((x - rect.left()) / 18) * 18
            y = rect.top() + round((y - rect.top()) / 12) * 12
            skew = round(((self._glitch_unit(frame_seed + index * 137) - 0.5) * 72 * intensity) / 9) * 9
            alpha = int((72 + 72 * self._glitch_unit(frame_seed + index * 149)) * intensity)
            if title_screen_rect is not None:
                shard_metrics = QFontMetrics(shard_font)
                shard_rect = QRectF(
                    x - 18 + skew,
                    y - shard_metrics.ascent() - 8,
                    shard_metrics.horizontalAdvance(text) + 36,
                    shard_metrics.height() + 16,
                )
                avoid_rect = title_screen_rect.adjusted(-44, -30, 44, 30)
                if shard_rect.intersects(avoid_rect):
                    if shard_rect.center().y() <= avoid_rect.center().y():
                        y = max(rect.top() + shard_metrics.ascent() + 18, avoid_rect.top() - 18)
                    else:
                        y = min(rect.bottom() - shard_metrics.descent() - 18, avoid_rect.bottom() + shard_metrics.ascent() + 18)
                    y = rect.top() + round((y - rect.top()) / 12) * 12
            painter.setPen(QColor(0, 245, 255, alpha))
            painter.drawText(QPoint(int(x - 14 * intensity + skew), int(y)), text)
            painter.setPen(QColor(255, 39, 102, max(0, alpha - 8)))
            painter.drawText(QPoint(int(x + 14 * intensity + skew), int(y + 4)), text)
            painter.setPen(QColor(124, 58, 237, max(0, int(alpha * 0.46))))
            painter.drawText(QPoint(int(x + skew), int(y - 2)), text)

            block_y = y - size * 0.72
            painter.fillRect(
                QRectF(x + skew, block_y, max(42.0, len(text) * size * 0.22), 5.0),
                QColor(0, 245, 255, int(26 * intensity)),
            )

        painter.restore()

    def _draw_aurora_bloom(self, painter, rect):
        bloom_progress = self._aurora_bloom
        bloom = 0.0
        if bloom_progress > 0.0:
            if bloom_progress < 0.42:
                rise = bloom_progress / 0.42
                bloom = rise * rise * (3 - 2 * rise)
            else:
                fall = (bloom_progress - 0.42) / 0.58
                bloom = 1.0 - (fall * fall * (3 - 2 * fall))
        bloom *= self._fade_opacity
        if bloom <= 0.01:
            return

        drift = (self._glitch_progress * 1.7 + bloom_progress * 1.35) % 1.0
        gradient = QLinearGradient(
            rect.left() - rect.width() * 0.25 + rect.width() * drift,
            rect.top(),
            rect.right() + rect.width() * 0.25,
            rect.bottom(),
        )
        gradient.setColorAt(0.00, QColor(38, 227, 255, 0))
        gradient.setColorAt(0.18, QColor(38, 227, 255, int(54 * bloom)))
        gradient.setColorAt(0.42, QColor(123, 97, 255, int(78 * bloom)))
        gradient.setColorAt(0.68, QColor(236, 72, 255, int(62 * bloom)))
        gradient.setColorAt(0.84, QColor(122, 255, 226, int(44 * bloom)))
        gradient.setColorAt(1.00, QColor(122, 255, 226, 0))

        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Screen)
        painter.fillRect(rect, QBrush(gradient))

        ribbon = QPainterPath()
        vertical_drift = (bloom_progress - 0.5) * 34
        ribbon.moveTo(rect.left() - 30, rect.center().y() - 42 + vertical_drift)
        ribbon.cubicTo(
            rect.left() + rect.width() * 0.24,
            rect.center().y() - 112 - vertical_drift,
            rect.left() + rect.width() * 0.56,
            rect.center().y() + 72 + vertical_drift,
            rect.right() + 30,
            rect.center().y() - 8 - vertical_drift,
        )
        ribbon_gradient = QLinearGradient(rect.left(), rect.center().y() - 90, rect.right(), rect.center().y() + 90)
        ribbon_gradient.setColorAt(0.00, QColor(101, 207, 221, int(0 * bloom)))
        ribbon_gradient.setColorAt(0.30, QColor(101, 207, 221, int(52 * bloom)))
        ribbon_gradient.setColorAt(0.58, QColor(236, 72, 255, int(64 * bloom)))
        ribbon_gradient.setColorAt(1.00, QColor(122, 255, 226, int(0 * bloom)))
        painter.setPen(QPen(QBrush(ribbon_gradient), 34, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(ribbon)
        painter.restore()

    def _draw_intro_title(self, painter, title_rect, base_pixel_size):
        if (
            self._title_progress > 0.001
            or not self.glitch_font_families
            or (self._glitch_progress >= 1.0 and self._lock_glow_opacity <= 0.01)
        ):
            font = QFont(self.font_family)
            font.setPixelSize(base_pixel_size)
            font.setWeight(QFont.Black)
            painter.setFont(font)
            post_lock_active = (
                self._post_lock_glitch_progress > 0.0
                and self._title_progress <= 0.001
                and self._glitch_progress >= 1.0
                and self._fade_opacity > 0.01
            )
            if post_lock_active:
                self._draw_post_lock_title_glitch(painter, title_rect, font)
            else:
                painter.drawText(title_rect, Qt.AlignCenter, self.title)
            return

        resolve = self._glitch_progress
        lock_start = 0.18
        lock_window = 0.82
        active_letters = [index for index, char in enumerate(self.title) if char != " "]
        total_letters = max(1, len(active_letters))
        reveal_by_index = {}
        locked_letters = set()
        for lock_order, letter_index in enumerate(active_letters):
            reveal_start = 0.025 + 0.34 * lock_order / total_letters
            reveal_progress = min(1.0, max(0.0, (resolve - reveal_start) / 0.16))
            reveal_by_index[letter_index] = reveal_progress * reveal_progress * (3 - 2 * reveal_progress)
            threshold = lock_start + lock_window * (lock_order + 1) / total_letters
            if resolve >= threshold:
                locked_letters.add(letter_index)

        locked_ratio = len(locked_letters) / total_letters
        intensity = max(0.0, 1.0 - locked_ratio)
        cycle_count = 28
        frame = int(resolve * cycle_count)

        anchor_font = QFont(self.font_family)
        anchor_font.setPixelSize(base_pixel_size)
        anchor_font.setWeight(QFont.Black)
        anchor_metrics = QFontMetrics(anchor_font)
        total_width = max(1, anchor_metrics.horizontalAdvance(self.title))
        x = title_rect.center().x() - total_width / 2
        baseline_y = title_rect.center().y() + (
            anchor_metrics.ascent() - anchor_metrics.descent()
        ) / 2

        accent_cyan = QColor(101, 207, 221, int(78 * intensity * self._fade_opacity))
        accent_pink = QColor(236, 72, 255, int(64 * intensity * self._fade_opacity))
        main_color = QColor(248, 250, 252, int(242 * self._fade_opacity))

        for index, char in enumerate(self.title):
            slot_width = anchor_metrics.horizontalAdvance(char)
            if char == " ":
                x += max(slot_width, anchor_metrics.horizontalAdvance("N") // 2)
                continue
            reveal_opacity = reveal_by_index.get(index, 1.0)
            if reveal_opacity <= 0.01:
                x += slot_width
                continue

            is_cjk_scramble = False
            is_symbol_scramble = False
            is_number_scramble = False
            if index in locked_letters:
                family = self.font_family
                display_char = char
            else:
                family_index = (index * 2 + frame) % len(self.glitch_font_families)
                family = self.glitch_font_families[family_index]
                display_char = char
                scramble_phase = (frame + index * 3) % 10
                if scramble_phase in (0, 4, 8):
                    glyph_index = (frame * 3 + index * 7) % len(self.scramble_glyphs)
                    display_char = self.scramble_glyphs[glyph_index]
                    family = self.cjk_font_families[(frame + index) % len(self.cjk_font_families)]
                    is_cjk_scramble = True
                elif scramble_phase == 3:
                    symbol_index = (frame * 2 + index * 5) % len(self.scramble_symbols)
                    display_char = self.scramble_symbols[symbol_index]
                    family = self.glitch_font_families[(family_index + frame + index) % len(self.glitch_font_families)]
                    is_symbol_scramble = True
                elif scramble_phase == 7:
                    number_index = (frame + index * 5) % len(self.scramble_numbers)
                    display_char = self.scramble_numbers[number_index]
                    family = self.glitch_font_families[(family_index + frame + index) % len(self.glitch_font_families)]
                    is_number_scramble = True

            letter_font = QFont(family)
            if is_cjk_scramble:
                letter_font.setPixelSize(int(base_pixel_size * 0.68))
            elif is_symbol_scramble:
                letter_font.setPixelSize(int(base_pixel_size * 0.88))
            elif is_number_scramble:
                letter_font.setPixelSize(int(base_pixel_size * 0.94))
            else:
                letter_font.setPixelSize(base_pixel_size)
            letter_font.setWeight(QFont.Black)
            painter.setFont(letter_font)
            letter_metrics = QFontMetrics(letter_font)
            letter_width = letter_metrics.horizontalAdvance(display_char)
            letter_x = x + (slot_width - letter_width) / 2
            jitter = 0 if index in locked_letters else int(intensity * (((index * 7 + frame) % 5) - 2))

            if index in locked_letters:
                self._draw_locked_letter_glow(
                    painter,
                    letter_font,
                    char,
                    letter_x,
                    baseline_y,
                    slot_width,
                    index,
                    resolve,
                    reveal_opacity,
                )

            if intensity > 0.12 and index % 3 == frame % 3:
                painter.setPen(accent_cyan)
                painter.setOpacity(reveal_opacity)
                painter.drawText(int(letter_x - 2), int(baseline_y + jitter), display_char)
            if intensity > 0.12 and index % 4 == frame % 4:
                painter.setPen(accent_pink)
                painter.setOpacity(reveal_opacity)
                painter.drawText(int(letter_x + 2), int(baseline_y - jitter), display_char)

            painter.setPen(main_color)
            painter.setOpacity(reveal_opacity)
            painter.drawText(int(letter_x), int(baseline_y + jitter), display_char)
            painter.setOpacity(1.0)
            x += slot_width

        if locked_ratio >= 1.0:
            self._draw_post_lock_title_glitch(painter, title_rect, anchor_font)

    def _draw_post_lock_title_glitch(self, painter, title_rect, font):
        state = self._post_lock_glitch_state()
        if not state:
            return
        beat_index, split, progress = state

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(font)
        painter.setCompositionMode(QPainter.CompositionMode_Screen)

        edge_span = max(float(self.width()) * 1.4, title_rect.width() + 180)
        expanded_rect = QRectF(
            title_rect.center().x() - edge_span / 2,
            title_rect.top() - 8,
            edge_span,
            title_rect.height() + 16,
        )

        def draw_dos_title_echo():
            dos_font = QFont("Consolas")
            dos_font.setStyleHint(QFont.TypeWriter)
            dos_font.setFixedPitch(True)
            dos_font.setWeight(QFont.Black)
            dos_font.setPixelSize(max(18, int(font.pixelSize() * 0.86)))
            if hasattr(QFont, "NoAntialias"):
                dos_font.setStyleStrategy(QFont.NoAntialias)

            tick = int(progress * 18) + beat_index * 3
            block_x = ((tick % 5) - 2) * 3.5 * split
            block_y = (((tick // 2) % 3) - 1) * 1.8 * split

            painter.save()
            painter.setFont(dos_font)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QColor(3, 8, 18, int(192 * split)))
            painter.drawText(title_rect.translated(block_x + 3, block_y + 3), Qt.AlignCenter, self.title)
            painter.setCompositionMode(QPainter.CompositionMode_Screen)
            painter.setPen(QColor(0, 245, 255, int(162 * split)))
            painter.drawText(title_rect.translated(block_x - 8 * split, block_y), Qt.AlignCenter, self.title)
            painter.setPen(QColor(255, 39, 102, int(152 * split)))
            painter.drawText(title_rect.translated(block_x + 8 * split, block_y + 2), Qt.AlignCenter, self.title)
            painter.setPen(QColor(248, 250, 252, int(92 * split)))
            painter.drawText(title_rect.translated(block_x, block_y), Qt.AlignCenter, self.title)

            for index in range(5):
                unit = self._glitch_unit(tick * 101 + index * 37)
                y = title_rect.top() + title_rect.height() * (0.22 + unit * 0.62)
                width = title_rect.width() * (0.08 + 0.22 * self._glitch_unit(tick * 113 + index * 41))
                x = title_rect.left() + title_rect.width() * self._glitch_unit(tick * 127 + index * 43)
                painter.fillRect(
                    QRectF(x, y, width, 2.0 + 1.8 * split),
                    QColor(248, 250, 252, int(42 * split)),
                )
            painter.restore()

        def draw_sliced_title(upper_direction):
            slice_y = title_rect.top() + title_rect.height() * 0.51
            gap = 7.0 + 4.0 * split
            displacement = 26.0 * split
            upper_offset = displacement * upper_direction
            lower_offset = -displacement * upper_direction
            upper_rect = QRectF(
                expanded_rect.left(),
                title_rect.top() - 4,
                expanded_rect.width(),
                max(1.0, slice_y - title_rect.top() - gap),
            )
            lower_rect = QRectF(
                expanded_rect.left(),
                slice_y + gap,
                expanded_rect.width(),
                max(1.0, title_rect.bottom() - slice_y - gap + 4),
            )

            for clip_rect, offset, vertical_pull in (
                (upper_rect, upper_offset, -2.6 * split),
                (lower_rect, lower_offset, 2.6 * split),
            ):
                painter.save()
                painter.setClipRect(clip_rect)
                painter.setCompositionMode(QPainter.CompositionMode_Screen)
                painter.setPen(QColor(248, 250, 252, int(236 * split)))
                painter.drawText(title_rect.translated(offset, vertical_pull), Qt.AlignCenter, self.title)
                painter.setPen(QColor(0, 245, 255, int(214 * split)))
                painter.drawText(title_rect.translated(offset - 4.8 * split, vertical_pull - 0.8), Qt.AlignCenter, self.title)
                painter.setPen(QColor(255, 39, 102, int(205 * split)))
                painter.drawText(title_rect.translated(offset + 4.8 * split, vertical_pull + 0.8), Qt.AlignCenter, self.title)
                painter.restore()

        beat_configs = (
            (1, 14.0, ((0.31, 0.055, 18), (0.58, 0.045, -10))),
            (-1, 12.0, ((0.48, 0.070, -17),)),
            (1, 9.0, ((0.68, 0.070, -14),)),
            (-1, 15.0, ((0.27, 0.050, 15), (0.73, 0.052, -18))),
            (-1, 8.5, ((0.39, 0.050, 12),)),
        )
        upper_direction, channel_base, bands = beat_configs[min(beat_index, len(beat_configs) - 1)]
        draw_sliced_title(upper_direction)

        if beat_index in (1, 3, 4):
            draw_dos_title_echo()

        channel_offset = channel_base * split
        for multiplier, alpha in ((1.0, 156), (0.55, 96)):
            painter.setPen(QColor(0, 245, 255, int(alpha * split)))
            painter.drawText(
                title_rect.translated(-channel_offset * multiplier - 3.0 * split, -0.8 * multiplier),
                Qt.AlignCenter,
                self.title,
            )
            painter.setPen(QColor(255, 39, 102, int((alpha - 8) * split)))
            painter.drawText(
                title_rect.translated(channel_offset * multiplier + 3.0 * split, 0.8 * multiplier),
                Qt.AlignCenter,
                self.title,
            )

        line_y = title_rect.top() + title_rect.height() * (0.36 + 0.12 * (beat_index % 3))
        fragment_specs = (
            (0.12, 0.24),
            (0.47, 0.18),
            (0.72, 0.21),
        )
        for index, (dy, color) in enumerate((
            (-3.0, QColor(0, 245, 255, int(98 * split))),
            (0.0, QColor(248, 250, 252, int(62 * split))),
            (3.2, QColor(255, 39, 102, int(88 * split))),
        )):
            x_ratio, width_ratio = fragment_specs[(index + beat_index) % len(fragment_specs)]
            painter.fillRect(
                QRectF(
                    title_rect.left() + title_rect.width() * x_ratio,
                    line_y + dy,
                    title_rect.width() * width_ratio,
                    1.0 + (0.6 if index == 1 else 0.0),
                ),
                color,
            )

        for top_ratio, height_ratio, offset_base in bands:
            offset = offset_base * split
            band_rect = QRectF(
                expanded_rect.left(),
                title_rect.top() + title_rect.height() * top_ratio,
                expanded_rect.width(),
                max(4.0, title_rect.height() * height_ratio),
            )
            band_intensity = min(1.0, abs(offset_base) / 28.0 + 0.16) * split
            painter.save()
            painter.setClipRect(band_rect)
            painter.setCompositionMode(QPainter.CompositionMode_Screen)
            tear_y = band_rect.center().y()
            for index, (dy, width_ratio, color) in enumerate((
                (-1.0, 0.38, QColor(0, 245, 255, int(84 * split))),
                (2.4, 0.24, QColor(255, 39, 102, int(68 * split))),
                (4.8, 0.17, QColor(248, 250, 252, int(32 * split))),
            )):
                x_seed = self._glitch_unit(int(progress * 1000) + beat_index * 53 + index * 19)
                x = title_rect.left() + title_rect.width() * (0.05 + 0.55 * x_seed)
                painter.fillRect(
                    QRectF(x, tear_y + dy, title_rect.width() * width_ratio, 0.8 + (0.6 if index == 0 else 0.0)),
                    color,
                )
            painter.setPen(QColor(248, 250, 252, int(238 * band_intensity)))
            painter.drawText(title_rect.translated(offset, 0), Qt.AlignCenter, self.title)
            painter.setPen(QColor(0, 245, 255, int(232 * band_intensity)))
            painter.drawText(title_rect.translated(offset - channel_offset * 0.55, -1.0 * split), Qt.AlignCenter, self.title)
            painter.setPen(QColor(255, 39, 102, int(220 * band_intensity)))
            painter.drawText(title_rect.translated(offset + channel_offset * 0.55, 1.0 * split), Qt.AlignCenter, self.title)
            if abs(offset) > 16:
                painter.setPen(QColor(248, 250, 252, int(104 * band_intensity)))
                painter.drawText(title_rect.translated(-offset * 0.42, 0), Qt.AlignCenter, self.title)
            painter.restore()

        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        accent_streaks = (
            (0.16, 0.28, 96, QColor(248, 250, 252, int(126 * split))),
            (0.68, 0.62, 132, QColor(0, 245, 255, int(112 * split))),
            (0.43, 0.77, 72, QColor(255, 39, 102, int(104 * split))),
        )
        for x_ratio, y_ratio, width, color in accent_streaks:
            painter.fillRect(
                QRectF(
                    expanded_rect.left() + expanded_rect.width() * x_ratio,
                    expanded_rect.top() + expanded_rect.height() * y_ratio,
                    width,
                    1.2,
                ),
                color,
            )

        painter.restore()

    def _draw_locked_letter_glow(self, painter, font, char, letter_x, baseline_y, slot_width, index, resolve, reveal_opacity):
        glow_opacity = self._lock_glow_opacity * self._fade_opacity * reveal_opacity
        if glow_opacity <= 0.01:
            return

        path = QPainterPath()
        path.addText(letter_x, baseline_y, font, char)

        span = max(float(slot_width), 42.0)
        drift = ((resolve * 2.7) + (self._aurora_motion * 1.6) + (index * 0.11)) % 1.0
        start_x = letter_x - span + (span * 1.6 * drift)
        gradient = QLinearGradient(start_x, baseline_y - 46, start_x + span * 1.6, baseline_y + 34)
        gradient.setColorAt(0.00, QColor(38, 227, 255, int(112 * glow_opacity)))
        gradient.setColorAt(0.34, QColor(123, 97, 255, int(148 * glow_opacity)))
        gradient.setColorAt(0.66, QColor(236, 72, 255, int(132 * glow_opacity)))
        gradient.setColorAt(1.00, QColor(122, 255, 226, int(104 * glow_opacity)))

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(38, 227, 255, int(48 * glow_opacity)), 15, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(path)
        painter.setPen(QPen(QBrush(gradient), 10, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(path)
        painter.setPen(QPen(QColor(248, 250, 252, int(78 * glow_opacity)), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(path)
        painter.fillPath(path, QBrush(gradient))
        painter.restore()


class SearchWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str, object)

    def __init__(self, court_dockets, username, password, headless, renamed_rows=None, directory_copy_target=None):
        super().__init__()
        self.court_dockets = court_dockets
        self.username = username
        self.password = password
        self.headless = headless
        self.renamed_rows = renamed_rows or []
        self.directory_copy_target = directory_copy_target
        self.cancel_event = Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            def callback(percent, text):
                self.progress.emit(percent, text)

            result = run_searches(
                self.court_dockets,
                self.username,
                self.password,
                progress_callback=callback,
                headless=self.headless,
                stop_event=self.cancel_event,
                renamed_rows=self.renamed_rows,
                directory_copy_target=self.directory_copy_target,
            )
            if not isinstance(result, dict):
                result = {
                    "success": False,
                    "message": "The search ended without a result summary.",
                    "found": 0,
                    "saved": 0,
                    "failed": 0,
                    "no_results": 0,
                    "files": [],
                }
            self.finished.emit(bool(result.get("success")), result.get("message", "Done."), result)
        except Exception as exc:
            message = f"{exc}\n{traceback.format_exc()}"
            self.finished.emit(False, message, None)


class CounselCollectorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.worker_thread = None
        self.current_results_dir = RUNS_ROOT
        self.dockets_by_court = {}
        self.output_jobs = []
        self.renamed_rows = []
        self.directory_copy_target = None
        self.refreshing_docket_display = False
        self.loading_saved_settings = False
        self.password_visible = False
        self.pending_success_message = None
        self.pending_success_result = None
        self.pending_failure_message = None
        self.pending_failure_result = None
        self._intro_animation_started = False
        self._intro_animation = None
        self._intro_glitch_animation = None
        self._intro_post_lock_sequence = None
        self._intro_post_lock_glitch_animation = None
        self._intro_window_dissolve_sequence = None
        self._intro_window_dissolve_out_animation = None
        self._intro_window_dissolve_in_animation = None
        self._intro_title_animation = None
        self._intro_fade_animation = None
        self.intro_active = True
        self.settings_popup_hidden_at = 0
        self.idle_visible = False
        self.idle_animation = None
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.setInterval(20000)
        self.idle_timer.timeout.connect(self._show_idle_screen)
        self._reset_run_context()
        self.header_font_family = self._load_header_font()
        self.intro_glitch_font_families = self._load_intro_glitch_fonts()
        self.setWindowTitle(APP_NAME)
        self.app_icon = QIcon(str(asset_path("shopee.ico")))
        if not self.app_icon.isNull():
            self.setWindowIcon(self.app_icon)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(660, 440)
        self._build_ui()
        self._apply_theme()
        self.docket_popup.setStyleSheet(self.styleSheet())
        self._load_saved_user_id()
        self._on_court_selection_changed()
        self._set_status("Ready to collect counsel.", "idle")
        self._update_run_button()
        QApplication.instance().installEventFilter(self)
        self._restart_idle_timer()

    def _load_header_font(self):
        return self._load_font_family("Neogen-Black.ttf")

    def _load_font_family(self, filename):
        font_path = asset_path(filename)
        if not font_path.exists():
            return ""

        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            return ""

        families = QFontDatabase.applicationFontFamilies(font_id)
        return families[0] if families else ""

    def _load_intro_glitch_fonts(self):
        font_files = (
            "Orbitron-Variable.ttf",
            "ZenDots-Regular.ttf",
            "Wallpoet-Regular.ttf",
            "Hypik.otf",
        )
        font_families = []
        for filename in font_files:
            family = self._load_font_family(filename)
            if family:
                font_families.append(family)
        return font_families

    def play_intro_animation(self):
        if self._intro_animation_started:
            return
        self._intro_animation_started = True
        self.intro_active = True
        self._pause_idle_timer()
        self._show_intro_shell_after_glitch()
        self._position_intro_overlay()
        self._sync_intro_title_target()
        self.title_label.hide()
        self.intro_overlay.set_title_progress(0.0)
        self.intro_overlay.set_glitch_progress(0.0)
        self.intro_overlay.set_lock_glow_opacity(0.42)
        self.intro_overlay.set_aurora_bloom(0.0)
        self.intro_overlay.set_aurora_motion(0.0)
        self.intro_overlay.set_post_lock_glitch_progress(0.0)
        self.intro_overlay.set_window_dissolve(0.0)
        self.intro_overlay.set_fade_opacity(1.0)
        self.intro_overlay.show()
        self.intro_overlay.raise_()

        self.setWindowOpacity(0.0)

        opacity_animation = QPropertyAnimation(self, b"windowOpacity", self)
        opacity_animation.setDuration(520)
        opacity_animation.setStartValue(0.0)
        opacity_animation.setEndValue(1.0)
        opacity_animation.setEasingCurve(QEasingCurve.OutCubic)

        self._intro_animation = QParallelAnimationGroup(self)
        self._intro_animation.addAnimation(opacity_animation)
        self._intro_animation.finished.connect(self._start_intro_glitch_hold)
        self._intro_animation.start()

    def _start_intro_glitch_hold(self):
        if not self.intro_active:
            return
        self.intro_overlay.set_title_progress(0.0)
        self.intro_overlay.set_glitch_progress(0.0)
        self.intro_overlay.set_post_lock_glitch_progress(0.0)
        self._intro_glitch_animation = QPropertyAnimation(self.intro_overlay, b"glitchProgress", self)
        self._intro_glitch_animation.setDuration(4500)
        self._intro_glitch_animation.setStartValue(0.0)
        self._intro_glitch_animation.setEndValue(1.0)
        self._intro_glitch_animation.setEasingCurve(QEasingCurve.Linear)
        self._intro_glitch_animation.finished.connect(self._start_intro_post_lock_glitch)
        self._intro_glitch_animation.start()

    def _start_intro_post_lock_glitch(self):
        if not self.intro_active:
            return

        self.intro_overlay.set_lock_glow_opacity(0.0)
        self.intro_overlay.set_aurora_bloom(0.0)
        self.intro_overlay.set_aurora_motion(1.0)
        self.intro_overlay.set_post_lock_glitch_progress(0.0)
        self.intro_overlay.set_window_dissolve(0.0)
        self._intro_post_lock_glitch_animation = QPropertyAnimation(
            self.intro_overlay,
            b"postLockGlitchProgress",
            self,
        )
        self._intro_post_lock_glitch_animation.setDuration(2000)
        self._intro_post_lock_glitch_animation.setStartValue(0.0)
        self._intro_post_lock_glitch_animation.setEndValue(1.0)
        self._intro_post_lock_glitch_animation.setEasingCurve(QEasingCurve.Linear)

        self._intro_window_dissolve_out_animation = QPropertyAnimation(self.intro_overlay, b"windowDissolve", self)
        self._intro_window_dissolve_out_animation.setDuration(260)
        self._intro_window_dissolve_out_animation.setStartValue(0.0)
        self._intro_window_dissolve_out_animation.setEndValue(1.0)
        self._intro_window_dissolve_out_animation.setEasingCurve(QEasingCurve.OutCubic)

        self._intro_window_dissolve_in_animation = QPropertyAnimation(self.intro_overlay, b"windowDissolve", self)
        self._intro_window_dissolve_in_animation.setDuration(360)
        self._intro_window_dissolve_in_animation.setStartValue(1.0)
        self._intro_window_dissolve_in_animation.setEndValue(0.0)
        self._intro_window_dissolve_in_animation.setEasingCurve(QEasingCurve.InOutCubic)

        self._intro_window_dissolve_sequence = QSequentialAnimationGroup(self)
        self._intro_window_dissolve_sequence.addAnimation(self._intro_window_dissolve_out_animation)
        self._intro_window_dissolve_sequence.addPause(1380)
        self._intro_window_dissolve_sequence.addAnimation(self._intro_window_dissolve_in_animation)
        QTimer.singleShot(0, self._hide_intro_shell_for_glitch)
        QTimer.singleShot(2000, self._show_intro_shell_after_glitch)

        self._position_intro_overlay()
        self._sync_intro_title_target()
        self.intro_overlay.raise_()
        self._intro_title_animation = QPropertyAnimation(self.intro_overlay, b"titleProgress", self)
        self._intro_title_animation.setDuration(900)
        self._intro_title_animation.setStartValue(0.0)
        self._intro_title_animation.setEndValue(1.0)
        self._intro_title_animation.setEasingCurve(QEasingCurve.Linear)

        glitch_group = QParallelAnimationGroup(self)
        glitch_group.addAnimation(self._intro_post_lock_glitch_animation)
        glitch_group.addAnimation(self._intro_window_dissolve_sequence)

        self._intro_post_lock_sequence = QSequentialAnimationGroup(self)
        self._intro_post_lock_sequence.addAnimation(glitch_group)
        self._intro_post_lock_sequence.addAnimation(self._intro_title_animation)
        self._intro_post_lock_sequence.finished.connect(self._start_intro_reveal_animation)
        self._intro_post_lock_sequence.start()

    def _start_intro_title_animation(self):
        if not self.intro_active:
            return
        self._position_intro_overlay()
        self._sync_intro_title_target()
        self.intro_overlay.raise_()
        self.intro_overlay.set_lock_glow_opacity(0.0)
        self.intro_overlay.set_aurora_bloom(0.0)
        self.intro_overlay.set_aurora_motion(1.0)
        self.intro_overlay.set_post_lock_glitch_progress(0.0)
        self.intro_overlay.set_window_dissolve(0.0)
        self._intro_title_animation = QPropertyAnimation(self.intro_overlay, b"titleProgress", self)
        self._intro_title_animation.setDuration(900)
        self._intro_title_animation.setStartValue(0.0)
        self._intro_title_animation.setEndValue(1.0)
        self._intro_title_animation.setEasingCurve(QEasingCurve.Linear)
        self._intro_title_animation.finished.connect(self._start_intro_reveal_animation)
        self._intro_title_animation.start()

    def _start_intro_reveal_animation(self):
        if not self.intro_active:
            return
        self._sync_intro_title_target()
        self.intro_overlay.set_title_progress(1.0)
        self.intro_overlay.set_post_lock_glitch_progress(0.0)
        self.intro_overlay.set_window_dissolve(0.0)
        self.title_label.show()
        self._intro_fade_animation = QPropertyAnimation(self.intro_overlay, b"fadeOpacity", self)
        self._intro_fade_animation.setDuration(620)
        self._intro_fade_animation.setStartValue(1.0)
        self._intro_fade_animation.setEndValue(0.0)
        self._intro_fade_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._intro_fade_animation.finished.connect(self._finish_intro_animation)
        self._intro_fade_animation.start()

    def _finish_intro_animation(self):
        self.intro_active = False
        self._show_intro_shell_after_glitch()
        self.intro_overlay.hide()
        self.intro_overlay.set_fade_opacity(0.0)
        self.intro_overlay.set_title_progress(1.0)
        self.intro_overlay.set_glitch_progress(1.0)
        self.intro_overlay.set_lock_glow_opacity(0.0)
        self.intro_overlay.set_aurora_bloom(0.0)
        self.intro_overlay.set_aurora_motion(1.0)
        self.intro_overlay.set_post_lock_glitch_progress(0.0)
        self.intro_overlay.set_window_dissolve(0.0)
        self._restart_idle_timer()

    def _hide_intro_shell_for_glitch(self):
        if not self.intro_active or not hasattr(self, "shell"):
            return
        self.shell.hide()
        self.intro_overlay.raise_()

    def _show_intro_shell_after_glitch(self):
        if not hasattr(self, "shell"):
            return
        self.shell.show()
        if hasattr(self, "intro_overlay") and self.intro_active:
            self._position_intro_overlay()
            self.intro_overlay.raise_()

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        self.root = root
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(0)

        loading_gif = asset_path("loading_sea.gif")
        self.shell = AnimatedWatermarkFrame(loading_gif, opacity=0.22, radius=18)
        self.shell.setObjectName("Shell")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 128))
        self.shell.setGraphicsEffect(shadow)
        root_layout.addWidget(self.shell)

        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(18, 16, 18, 18)
        shell_layout.setSpacing(14)

        self.title_bar = CustomTitleBar(self)
        title_row = QHBoxLayout(self.title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)

        left_balance = QWidget()
        left_balance.setFixedWidth(78)
        title_row.addWidget(left_balance)

        title_host = QWidget()
        title_host.setObjectName("TitleHost")
        title_block = QVBoxLayout()
        title_block.setContentsMargins(0, 0, 0, 0)
        title_block.setSpacing(0)
        self.title_label = QLabel(APP_NAME)
        self.title_label.setObjectName("Title")
        self.title_label.setAlignment(Qt.AlignCenter)
        if self.header_font_family:
            self.title_label.setFont(QFont(self.header_font_family))
        title_block.addWidget(self.title_label)
        title_host.setLayout(title_block)
        title_row.addWidget(title_host, 1)

        controls_host = QWidget()
        controls_host.setFixedWidth(78)
        controls_row = QHBoxLayout(controls_host)
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(10)
        self.minimize_btn = QPushButton("_")
        self.minimize_btn.setObjectName("WindowButton")
        self.minimize_btn.setToolTip("Minimize")
        self.minimize_btn.clicked.connect(self.showMinimized)
        controls_row.addWidget(self.minimize_btn)

        self.close_btn = QPushButton("X")
        self.close_btn.setObjectName("CloseButton")
        self.close_btn.setToolTip("Close")
        self.close_btn.clicked.connect(self.close)
        controls_row.addWidget(self.close_btn)
        title_row.addWidget(controls_host)
        shell_layout.addWidget(self.title_bar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("PhaseStack")
        shell_layout.addWidget(self.stack, 1)

        self._build_setup_page()
        self._build_run_page()
        self.intro_overlay = IntroOverlay(APP_NAME, self.header_font_family, root)
        self.intro_overlay.set_glitch_fonts(self.intro_glitch_font_families)
        self.intro_overlay.setGeometry(self.shell.geometry())
        self.intro_overlay.show()
        self.intro_overlay.raise_()
        self.idle_overlay = IdleScreen(asset_path("idle_screen.gif"), self.shell)
        self.idle_overlay.clicked.connect(self._hide_idle_screen)
        self.idle_overlay.wakeup.connect(self._hide_idle_screen)
        self.idle_overlay.set_fade_opacity(0.0)
        self._position_idle_overlay()
        self.docket_popup = DocketListPopup(self)
        self.docket_popup.closed.connect(self._apply_docket_popup_values)

    def _build_setup_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        card = SetupCard()
        card.setObjectName("Card")
        self.setup_card = card
        card.resized.connect(self._position_settings_button)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 18)
        card_layout.setSpacing(14)

        self.settings_btn = QPushButton(card)
        self.settings_btn.setObjectName("SettingsButton")
        self.settings_btn.setIcon(QIcon(str(asset_path("settings_cog.svg"))))
        self.settings_btn.setToolTip("Options")
        self.settings_btn.setFixedSize(24, 24)
        self.settings_btn.clicked.connect(self._toggle_settings_popup)
        self.settings_btn.raise_()

        form_grid = QGridLayout()
        form_grid.setContentsMargins(0, 34, 0, 0)
        form_grid.setHorizontalSpacing(14)
        form_grid.setVerticalSpacing(9)
        form_grid.addWidget(self._label("Lexis ID"), 0, 0)
        form_grid.addWidget(self._label("Password"), 0, 1)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("ID")
        self.username_input.textChanged.connect(self._on_username_changed)
        form_grid.addWidget(self.username_input, 1, 0)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.textChanged.connect(self._update_run_button)
        self.password_toggle_action = QAction(self)
        self.password_toggle_action.triggered.connect(self._toggle_password_visibility)
        self.password_input.addAction(self.password_toggle_action, QLineEdit.TrailingPosition)
        self._sync_password_visibility_action()
        form_grid.addWidget(self.password_input, 1, 1)

        form_grid.addWidget(self._label("Circuit Court"), 2, 0)

        docket_label_row = QHBoxLayout()
        docket_label_row.setContentsMargins(0, 0, 0, 0)
        docket_label_row.setSpacing(6)
        docket_label_row.addWidget(self._label("Docket Numbers"))
        self.directory_btn = QPushButton()
        self.directory_btn.setObjectName("DirectoryButton")
        self.directory_btn.setIcon(QIcon(str(asset_path("triangle_down.svg"))))
        self.directory_btn.setToolTip("Load docket numbers from a PDF folder")
        self.directory_btn.clicked.connect(self._select_docket_directory)
        docket_label_row.addWidget(self.directory_btn)
        docket_label_row.addStretch(1)
        form_grid.addLayout(docket_label_row, 2, 1)

        self.court_combo = CourtMultiSelectCombo(list(CIRCUIT_COURTS.keys()))
        self.court_combo.selection_changed.connect(self._on_court_selection_changed)
        form_grid.addWidget(self.court_combo, 3, 0)

        self.docket_input = DocketLineEdit()
        self.docket_input.setPlaceholderText("25-11129, 25-22233")
        self.docket_input.setToolTip("Double-click to open the docket list panel.")
        self.docket_input.textChanged.connect(self._on_docket_text_changed)
        self.docket_input.double_clicked.connect(self._show_docket_popup)
        form_grid.addWidget(self.docket_input, 3, 1)
        form_grid.setColumnStretch(0, 1)
        form_grid.setColumnStretch(1, 1)
        card_layout.addLayout(form_grid)

        self.settings_popup = SettingsPopup(self)
        self.settings_popup.closed.connect(self._mark_settings_popup_hidden)
        self.save_id_checkbox = QCheckBox("Save ID")
        self.save_id_checkbox.stateChanged.connect(self._toggle_save_id)
        self.headless_checkbox = QCheckBox("Headless Mode")
        self.headless_checkbox.setChecked(True)
        self.settings_popup.set_options([
            self.save_id_checkbox,
            self.headless_checkbox,
        ])
        card_layout.addStretch(1)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.results_btn = QPushButton("View Folder")
        self.results_btn.setObjectName("ViewButton")
        self.results_btn.setFocusPolicy(Qt.NoFocus)
        self.results_btn.clicked.connect(self._open_results_folder)
        button_row.addWidget(self.results_btn, 2)
        self.run_btn = QPushButton("Collect")
        self.run_btn.setObjectName("PrimaryButton")
        self.run_btn.setFocusPolicy(Qt.NoFocus)
        self.run_btn.clicked.connect(self._start_collection)
        button_row.addWidget(self.run_btn, 3)
        card_layout.addLayout(button_row)

        page_layout.addWidget(card)
        self.setup_page = page
        self.stack.addWidget(page)
        self._position_settings_button()

    def _build_run_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        card = QFrame()
        card.setObjectName("RunCard")
        self.run_card = card
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(14)

        self.progress = AnimatedProgressBar()
        self.progress.setObjectName("RunProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.progress)

        self.status_card = QFrame()
        self.status_card.setObjectName("StatusCard")
        self.status_layout = QVBoxLayout(self.status_card)
        self.status_layout.setContentsMargins(16, 10, 16, 10)
        self.status_layout.setSpacing(0)
        self.status_layout.setAlignment(Qt.AlignVCenter)
        self.status_label = QLabel()
        self.status_label.setObjectName("StatusText")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(False)
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.status_layout.addWidget(self.status_label)
        card_layout.addWidget(self.status_card)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.run_view_btn = QPushButton("View Folder")
        self.run_view_btn.setObjectName("ViewButton")
        self.run_view_btn.setFocusPolicy(Qt.NoFocus)
        self.run_view_btn.clicked.connect(self._open_results_folder)
        button_row.addWidget(self.run_view_btn, 2)
        self.cancel_run_btn = QPushButton("Cancel Run")
        self.cancel_run_btn.setObjectName("CancelButton")
        self.cancel_run_btn.setFocusPolicy(Qt.NoFocus)
        self.cancel_run_btn.clicked.connect(self._cancel_collection)
        button_row.addWidget(self.cancel_run_btn, 3)
        card_layout.addLayout(button_row)

        page_layout.addWidget(card)
        self.run_page = page
        self.stack.addWidget(page)

    def _apply_theme(self):
        checkbox_check_icon = str(asset_path("checkbox_check.svg")).replace("\\", "/")
        combo_arrow_icon = str(asset_path("triangle_down.svg")).replace("\\", "/")
        settings_cog_icon = str(asset_path("settings_cog.svg")).replace("\\", "/")
        stylesheet = """
            QWidget#Root {
                background-color: transparent;
                color: #f8fafc;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QFrame#TitleBar {
                background-color: transparent;
                border: none;
                min-height: 56px;
                max-height: 56px;
            }
            QFrame#Shell {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #101826, stop:1 #172033);
                border: 1px solid #22304a;
                border-radius: 18px;
            }
            QFrame#Card, QFrame#DocketPanel, QFrame#SettingsPanel {
                background-color: #101722;
                border: 1px solid #1d293d;
                border-radius: 14px;
            }
            QFrame#RunCard {
                background-color: rgba(16, 23, 34, 142);
                border: 1px solid rgba(29, 41, 61, 205);
                border-radius: 14px;
            }
            QFrame#IdleScreen {
                background-color: #0b1220;
                border: 1px solid rgba(34, 211, 238, 95);
                border-radius: 14px;
            }
            QLabel#Title {
                color: #f8fafc;
                font-size: 33px;
                font-weight: 900;
            }
            QLabel#FieldLabel {
                color: #93a8c3;
                font-size: 10px;
                font-weight: 700;
            }
            QLabel#PanelHint {
                color: #8aa0bd;
                font-size: 11px;
            }
            QLabel#SettingsPanelTitle {
                color: #67e8f9;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0px;
            }
            QLineEdit, QComboBox, QPlainTextEdit#DocketPanelEditor {
                background-color: #0b1220;
                color: #f8fafc;
                border: 1px solid #22304a;
                border-radius: 10px;
                padding: 9px 11px;
                font-size: 13px;
                min-height: 24px;
                selection-background-color: #22d3ee;
            }
            QComboBox {
                padding: 9px 38px 9px 11px;
            }
            QComboBox QLineEdit {
                background-color: transparent;
                color: #f8fafc;
                border: none;
                border-radius: 0;
                padding: 0;
                min-height: 0;
                selection-background-color: #22d3ee;
            }
            QComboBox QLineEdit:focus {
                background-color: transparent;
                border: none;
            }
            QComboBox::drop-down {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 34px;
                border: none;
                border-top-right-radius: 10px;
                border-bottom-right-radius: 10px;
                background-color: transparent;
                margin: 1px;
            }
            QComboBox::drop-down:hover {
                background-color: #102638;
            }
            QComboBox::down-arrow {
                image: url("__COMBO_ARROW_ICON__");
                width: 12px;
                height: 12px;
            }
            QLineEdit:focus, QComboBox:focus, QPlainTextEdit#DocketPanelEditor:focus {
                background-color: #0d1525;
                border: 1px solid #22d3ee;
            }
            QComboBox QAbstractItemView {
                background-color: #101722;
                color: #f8fafc;
                border: 1px solid #22304a;
                selection-background-color: #1d4ed8;
            }
            QCheckBox {
                color: #cbd5e1;
                font-size: 12px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1px solid #334155;
                background-color: #0b1220;
            }
            QCheckBox::indicator:checked {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6ff7ff,
                    stop:0.45 #22d3ee,
                    stop:1 #5b7cff
                );
                border: 1px solid #7dd3fc;
                image: url("__CHECKBOX_CHECK_ICON__");
            }
            QCheckBox::indicator:checked:hover {
                border: 1px solid #f0ff5f;
            }
            QPushButton {
                min-height: 36px;
                border-radius: 10px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: 700;
                border: 1px solid #243248;
            }
            QPushButton#PrimaryButton {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6ff7ff,
                    stop:0.28 #22d3ee,
                    stop:0.58 #5b7cff,
                    stop:1 #f0f9ff
                );
                color: #03131a;
                border-color: #7dd3fc;
            }
            QPushButton#PrimaryButton:hover {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00f5ff,
                    stop:0.32 #38bdf8,
                    stop:0.68 #d946ef,
                    stop:1 #f0ff5f
                );
                color: #020617;
                border-color: #f0ff5f;
            }
            QPushButton#PrimaryButton:pressed {
                background: qlineargradient(
                    x1:0, y1:1, x2:1, y2:0,
                    stop:0 #5b21b6,
                    stop:0.35 #2563eb,
                    stop:0.72 #06b6d4,
                    stop:1 #ff2bd6
                );
                color: #ffffff;
                border-color: #ff2bd6;
            }
            QPushButton#DirectoryButton {
                background-color: #0b1220;
                color: #67e8f9;
                border: 1px solid #22304a;
                border-radius: 6px;
                min-width: 22px;
                max-width: 22px;
                min-height: 18px;
                max-height: 18px;
                padding: 0;
            }
            QPushButton#DirectoryButton:hover {
                background-color: #102638;
                border-color: #22d3ee;
            }
            QPushButton#DirectoryButton:pressed {
                background-color: #1d1440;
                border-color: #ff2bd6;
            }
            QPushButton#SettingsButton {
                background-color: #0b1220;
                image: url("__SETTINGS_COG_ICON__");
                border: 1px solid #22304a;
                border-radius: 8px;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
                padding: 0;
            }
            QPushButton#SettingsButton:hover {
                background-color: #102638;
                border-color: #22d3ee;
            }
            QPushButton#SettingsButton:pressed {
                background-color: #1d1440;
                border-color: #ff2bd6;
            }
            QPushButton#ViewButton {
                background-color: #131c2b;
                color: #dbe7f5;
            }
            QPushButton#ViewButton:hover {
                background-color: #182335;
            }
            QPushButton#CancelButton {
                background-color: #7f1d1d;
                color: #ffffff;
                border-color: #b91c1c;
            }
            QPushButton#CancelButton:hover {
                background-color: #991b1b;
                border-color: #dc2626;
            }
            QPushButton#CancelButton:pressed {
                background-color: #5f1515;
                border-color: #ef4444;
                color: #ffffff;
            }
            QPushButton:disabled {
                background-color: #16202f;
                color: #5e7088;
                border-color: #16202f;
            }
            QPushButton#WindowButton, QPushButton#CloseButton {
                background-color: #121b2a;
                color: #cbd5e1;
                border: 1px solid #233149;
                border-radius: 8px;
                min-width: 32px;
                max-width: 32px;
                min-height: 30px;
                max-height: 30px;
                padding: 0;
                font-size: 12px;
                font-weight: 800;
            }
            QPushButton#WindowButton:hover {
                background-color: #1d2a3e;
                color: #f8fafc;
                border-color: #2f4261;
            }
            QPushButton#CloseButton:hover {
                background-color: #7f1d1d;
                color: #ffffff;
                border-color: #b91c1c;
            }
            QProgressBar#RunProgress {
                height: 24px;
                border-radius: 12px;
                text-align: center;
                color: #e2e8f0;
                border: 1px solid #22304a;
                background-color: #0b1220;
                font-size: 11px;
                font-weight: 800;
            }
            QProgressBar#RunProgress::chunk {
                border-radius: 11px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #22d3ee, stop:1 #2563eb);
            }
            QFrame#StatusCard {
                background-color: #0b1220;
                border: 1px solid #22304a;
                border-radius: 11px;
                min-height: 66px;
                max-height: 66px;
            }
            QLabel#StatusText {
                color: #f8fafc;
                font-size: 15px;
                font-weight: 600;
                border: none;
                background-color: transparent;
            }
            QFrame#StatusCard[state="running"] {
                border-color: #1f6f8b;
                background-color: #102638;
            }
            QFrame#StatusCard[state="success"] {
                border-color: #166534;
                background-color: #0f2f25;
            }
            QLabel#StatusText[state="success"] {
                color: #86efac;
            }
            QFrame#StatusCard[state="error"] {
                border-color: #7f1d1d;
                background-color: #3a1016;
            }
            QLabel#StatusText[state="error"] {
                color: #fda4af;
            }
            """
        self.setStyleSheet(
            stylesheet
            .replace("__CHECKBOX_CHECK_ICON__", checkbox_check_icon)
            .replace("__COMBO_ARROW_ICON__", combo_arrow_icon)
            .replace("__SETTINGS_COG_ICON__", settings_cog_icon)
        )

    def _label(self, text):
        label = QLabel(text.upper())
        label.setObjectName("FieldLabel")
        return label

    def _toggle_password_visibility(self):
        self.password_visible = not self.password_visible
        self.password_input.setEchoMode(QLineEdit.Normal if self.password_visible else QLineEdit.Password)
        self._sync_password_visibility_action()

    def _sync_password_visibility_action(self):
        icon_name = "eye_open.svg" if self.password_visible else "eye_closed.svg"
        tooltip = "Hide password" if self.password_visible else "Show password"
        self.password_toggle_action.setIcon(QIcon(str(asset_path(icon_name))))
        self.password_toggle_action.setToolTip(tooltip)

    def _load_saved_user_id(self):
        saved_user_id = load_saved_user_id()
        if not saved_user_id:
            return
        self.loading_saved_settings = True
        try:
            self.username_input.setText(saved_user_id)
            self.save_id_checkbox.setChecked(True)
        finally:
            self.loading_saved_settings = False

    def _on_username_changed(self):
        if self.save_id_checkbox.isChecked() and not self.loading_saved_settings:
            save_user_id(self.username_input.text())
        self._update_run_button()

    def _toggle_save_id(self, state):
        if self.loading_saved_settings:
            return
        if state == Qt.Checked:
            save_user_id(self.username_input.text())
        else:
            clear_saved_user_id()

    def _selected_courts(self):
        return self.court_combo.checked_items()

    def _show_prompt(self, title, message, kind="info"):
        self._pause_idle_timer()
        dialog = ThemedMessageDialog(self, title, message, kind)
        try:
            dialog.exec_()
        finally:
            self._restart_idle_timer()

    def _watched_widget_is_in_window(self, watched):
        if not isinstance(watched, QWidget):
            return False

        widget = watched
        while widget is not None:
            if widget is self:
                return True
            widget = widget.parentWidget()
        return watched.window() is self

    def eventFilter(self, watched, event):
        if self._watched_widget_is_in_window(watched):
            event_type = event.type()
            wake_events = (
                QEvent.MouseButtonPress,
                QEvent.MouseButtonDblClick,
                QEvent.KeyPress,
                QEvent.Wheel,
                QEvent.TouchBegin,
            )
            reset_events = wake_events + (
                QEvent.FocusIn,
                QEvent.ContextMenu,
            )

            if self.idle_visible:
                if event_type in wake_events:
                    self._hide_idle_screen()
                    return True
            elif event_type in reset_events:
                self._restart_idle_timer()

        return super().eventFilter(watched, event)

    def _idle_is_allowed(self):
        if self.intro_active:
            return False
        if self.stack.currentWidget() is not self.setup_page:
            return False
        if self.worker_thread and self.worker_thread.isRunning():
            return False
        if QApplication.activeModalWidget():
            return False
        if self.docket_popup.isVisible() or self.settings_popup.isVisible():
            return False
        return True

    def _restart_idle_timer(self):
        if not hasattr(self, "idle_timer"):
            return
        self.idle_timer.stop()
        if not self.idle_visible and self._idle_is_allowed():
            self.idle_timer.start()

    def _pause_idle_timer(self):
        if hasattr(self, "idle_timer"):
            self.idle_timer.stop()

    def _position_idle_overlay(self):
        if not hasattr(self, "idle_overlay") or not hasattr(self, "shell"):
            return
        self.idle_overlay.setGeometry(self.shell.rect())
        self.idle_overlay.raise_()

    def _position_intro_overlay(self):
        if not hasattr(self, "intro_overlay") or not hasattr(self, "shell"):
            return
        self.intro_overlay.setGeometry(self.shell.geometry())
        self._sync_intro_title_target()
        if self.intro_active:
            self.intro_overlay.raise_()

    def _sync_intro_title_target(self):
        if not all(hasattr(self, name) for name in ("intro_overlay", "shell", "title_label")):
            return
        if not self.title_label.isVisible():
            return
        target_center = self.title_label.mapTo(self.shell, self.title_label.rect().center())
        self.intro_overlay.set_target(target_center.x(), target_center.y(), 33)

    def _animate_idle(self, show):
        if self.idle_animation:
            self.idle_animation.stop()
            self.idle_animation.deleteLater()
            self.idle_animation = None

        self._position_idle_overlay()
        self.idle_overlay.raise_()
        if show:
            self.idle_overlay.show()
            self.idle_overlay.start()
            idle_start, idle_end = self.idle_overlay.get_fade_opacity(), 1.0
        else:
            idle_start, idle_end = self.idle_overlay.get_fade_opacity(), 0.0

        idle_animation = QPropertyAnimation(self.idle_overlay, b"fadeOpacity", self)
        idle_animation.setDuration(420)
        idle_animation.setStartValue(idle_start)
        idle_animation.setEndValue(idle_end)
        idle_animation.setEasingCurve(QEasingCurve.OutCubic)

        self.idle_animation = QParallelAnimationGroup(self)
        self.idle_animation.addAnimation(idle_animation)
        if not show:
            self.idle_animation.finished.connect(self._idle_hide_finished)
        self.idle_animation.start()

    def _show_idle_screen(self):
        if self.idle_visible:
            return
        if not self._idle_is_allowed():
            self._restart_idle_timer()
            return
        self.idle_visible = True
        self._pause_idle_timer()
        self._animate_idle(True)

    def _hide_idle_screen(self):
        if not self.idle_visible:
            self._restart_idle_timer()
            return
        self.idle_visible = False
        self._animate_idle(False)

    def _hide_idle_screen_immediate(self):
        self.idle_visible = False
        if hasattr(self, "idle_animation") and self.idle_animation:
            self.idle_animation.stop()
        if hasattr(self, "idle_overlay"):
            self.idle_overlay.set_fade_opacity(0.0)
            self.idle_overlay.stop()
            self.idle_overlay.hide()

    def _idle_hide_finished(self):
        self.idle_overlay.stop()
        self.idle_overlay.hide()
        self.idle_overlay.set_fade_opacity(0.0)
        self._restart_idle_timer()

    def _show_docket_popup(self):
        selected_courts = self._selected_courts()
        if not selected_courts:
            self._show_prompt(
                "Missing Courts",
                "Select at least one circuit court, or paste coded dockets like 25-2531_CA8.",
                "warning",
            )
            return
        self._pause_idle_timer()
        self.docket_popup.show_for_field(self.docket_input, selected_courts, self.dockets_by_court)

    def _toggle_settings_popup(self):
        if self.settings_popup.isVisible():
            self.settings_popup.hide()
            return
        if monotonic() - self.settings_popup_hidden_at < 0.25:
            return
        self._pause_idle_timer()
        self.settings_popup.show_near(self.settings_btn)

    def _mark_settings_popup_hidden(self):
        self.settings_popup_hidden_at = monotonic()
        self._restart_idle_timer()

    def _directory_result_tooltip(self, result):
        lines = [
            f"Folder: {result.get('root', '')}",
            f"Queued: {result.get('queued_count', 0)} docket(s)",
            f"PDFs scanned: {result.get('pdf_count', 0)}",
        ]

        sections = (
            ("Skipped because counsel already exists", result.get("skipped_existing", [])),
            ("Skipped because no docket was found", result.get("skipped_no_docket", [])),
            ("Skipped because court could not be inferred", result.get("skipped_unknown_court", [])),
        )
        for title, items in sections:
            if not items:
                continue
            lines.append("")
            lines.append(f"{title}: {len(items)}")
            lines.extend(f"- {item}" for item in items[:10])
            if len(items) > 10:
                lines.append(f"- ...and {len(items) - 10} more")

        return "\n".join(lines)

    def _select_docket_directory(self):
        self._pause_idle_timer()
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select PDF Directory",
            str(Path.home() / "Downloads"),
        )
        if not folder:
            self._restart_idle_timer()
            return

        self.directory_copy_target = None
        result = parse_pdf_directory(folder, selected_courts=self._selected_courts())
        jobs = result.get("jobs", [])
        tooltip = self._directory_result_tooltip(result)
        folder_name = Path(folder).name

        if not jobs:
            reason = "No collectable PDF dockets were found."
            if result.get("skipped_unknown_court"):
                reason += (
                    "\n\nSome PDFs did not have a court marker. Select one court first, "
                    "or use folders/filenames with markers like CA4, USAP06, or USAP6U."
                )
            elif result.get("skipped_existing"):
                reason += "\n\nEvery detected PDF already appears to have a matching counsel file."
            self._set_status(reason, "idle", tooltip=tooltip)
            self._show_prompt("No Dockets Loaded", reason, "warning")
            return

        self.output_jobs = jobs
        self.renamed_rows = result.get("renamed_rows", [])
        self.directory_copy_target = folder
        self.dockets_by_court = result.get("dockets_by_court", {})

        selected_courts = [court for court in CIRCUIT_COURTS if court in self.dockets_by_court]
        self.court_combo.set_checked_items(selected_courts)
        self._refresh_docket_display()
        self._update_run_button()

        skipped_notes = []
        if result.get("skipped_existing"):
            skipped_notes.append(f"{len(result['skipped_existing'])} existing counsel skipped")
        if result.get("skipped_unknown_court"):
            skipped_notes.append(f"{len(result['skipped_unknown_court'])} need a court marker")
        if result.get("skipped_no_docket"):
            skipped_notes.append(f"{len(result['skipped_no_docket'])} had no docket")

        message = (
            f"Loaded {result['queued_count']} docket(s) from {folder_name} "
            f"across {len(selected_courts)} court(s)."
        )
        if skipped_notes:
            message += " " + "; ".join(skipped_notes) + "."
        self._set_status(message, "idle", tooltip=tooltip)
        self._restart_idle_timer()

    def _apply_docket_popup_values(self, docket_map):
        if self._apply_smart_docket_map(docket_map):
            self._restart_idle_timer()
            return
        self.output_jobs = []
        self.renamed_rows = []
        self.directory_copy_target = None
        for court_label, dockets in docket_map.items():
            self.dockets_by_court[court_label] = normalize_dockets(dockets)
        self._refresh_docket_display()
        self._update_run_button()
        self._restart_idle_timer()

    def _apply_smart_docket_map(self, docket_map):
        routed_map = {}
        output_jobs_by_folder = {}
        output_filenames_by_folder = {}
        renamed_rows = []
        found_usap_jobs = False
        found_ca_feedback_jobs = False
        found_coded_dockets = False
        self.directory_copy_target = None

        for source_court, raw_dockets in docket_map.items():
            ca_jobs, ca_renamed_rows = parse_ca_feedback_rows(raw_dockets)
            if ca_jobs:
                found_ca_feedback_jobs = True
                renamed_rows.extend(ca_renamed_rows)
                for job in ca_jobs:
                    court_label = job["court_label"]
                    output_folder = job["output_folder"]
                    dockets = job["docket_numbers"]
                    routed_map[court_label] = merge_dockets(routed_map.get(court_label, ""), dockets)
                    key = (court_label, output_folder)
                    output_jobs_by_folder[key] = merge_dockets(output_jobs_by_folder.get(key, ""), dockets)
                    filename_map = job.get("docket_output_filenames") or {}
                    if filename_map:
                        target_map = output_filenames_by_folder.setdefault(key, {})
                        for docket, filename in filename_map.items():
                            target_map.setdefault(docket, filename)
                continue

            renamed_rows.extend(parse_feedback_renamed_rows(raw_dockets))
            usap_jobs, usap_plain = parse_usap_docket_rows(raw_dockets)
            if usap_jobs:
                found_usap_jobs = True
                for job in usap_jobs:
                    court_label = job["court_label"]
                    output_folder = job["output_folder"]
                    dockets = job["docket_numbers"]
                    routed_map[court_label] = merge_dockets(routed_map.get(court_label, ""), dockets)
                    key = (court_label, output_folder)
                    output_jobs_by_folder[key] = merge_dockets(output_jobs_by_folder.get(key, ""), dockets)
                    filename_map = job.get("docket_output_filenames") or {}
                    if filename_map:
                        target_map = output_filenames_by_folder.setdefault(key, {})
                        for docket, filename in filename_map.items():
                            target_map.setdefault(docket, filename)
                if usap_plain and source_court:
                    routed_map[source_court] = merge_dockets(routed_map.get(source_court, ""), usap_plain)
                    key = (source_court, default_output_folder_for_court(source_court))
                    output_jobs_by_folder[key] = merge_dockets(output_jobs_by_folder.get(key, ""), usap_plain)
                continue

            coded_map, plain_dockets = parse_court_coded_dockets(raw_dockets)
            if coded_map:
                found_coded_dockets = True
                for court_label, dockets in coded_map.items():
                    routed_map[court_label] = merge_dockets(routed_map.get(court_label, ""), dockets)
                if plain_dockets:
                    routed_map[source_court] = merge_dockets(routed_map.get(source_court, ""), plain_dockets)
            else:
                routed_map[source_court] = merge_dockets(routed_map.get(source_court, ""), raw_dockets)

        if found_usap_jobs or found_ca_feedback_jobs:
            selected_courts = [court for court in CIRCUIT_COURTS if court in routed_map]
            self.dockets_by_court = {court: routed_map[court] for court in selected_courts}
            has_generated_renames = any(
                row.get("FEEDBACK FORM FILENAME") != row.get("RENAMED")
                for row in renamed_rows
            )
            self.renamed_rows = renamed_rows if has_generated_renames else []
            self.output_jobs = [
                {
                    "court_label": court_label,
                    "docket_numbers": dockets,
                    "output_folder": output_folder,
                    "docket_output_filenames": output_filenames_by_folder.get((court_label, output_folder), {}),
                }
                for (court_label, output_folder), dockets in output_jobs_by_folder.items()
                if normalize_dockets(dockets)
            ]
            self.court_combo.set_checked_items(selected_courts)
            self._refresh_docket_display()
            self._update_run_button()

            total_dockets = sum(docket_count(job["docket_numbers"]) for job in self.output_jobs)
            input_label = "CA feedback" if found_ca_feedback_jobs and not found_usap_jobs else "USAP"
            self._set_status(
                f"Detected {total_dockets} {input_label} docket(s) across {len(selected_courts)} court(s) and {len(self.output_jobs)} folder(s).",
                "idle",
            )
            return True

        if not found_coded_dockets:
            return False

        self.output_jobs = []
        self.renamed_rows = []
        routed_map = {court: dockets for court, dockets in routed_map.items() if normalize_dockets(dockets)}
        selected_courts = [court for court in CIRCUIT_COURTS if court in routed_map]
        self.dockets_by_court = {court: routed_map[court] for court in selected_courts}
        self.court_combo.set_checked_items(selected_courts)
        self._refresh_docket_display()
        self._update_run_button()

        total_dockets = sum(docket_count(dockets) for dockets in self.dockets_by_court.values())
        self._set_status(f"Detected {total_dockets} coded docket(s) across {len(selected_courts)} court(s).", "idle")
        return True

    def _on_court_selection_changed(self):
        self.output_jobs = []
        self.renamed_rows = []
        self.directory_copy_target = None
        for court_label in self._selected_courts():
            self.dockets_by_court.setdefault(court_label, "")
        self._refresh_docket_display()
        self._update_run_button()

    def _on_docket_text_changed(self):
        if self.refreshing_docket_display:
            return
        selected_courts = self._selected_courts()
        text = self.docket_input.text()
        if len(selected_courts) <= 1:
            source_court = selected_courts[0] if selected_courts else ""
            if self._apply_smart_docket_map({source_court: text}):
                return
        if len(selected_courts) == 1:
            self.output_jobs = []
            self.renamed_rows = []
            self.directory_copy_target = None
            self.dockets_by_court[selected_courts[0]] = normalize_dockets(self.docket_input.text())
        elif not selected_courts and normalize_dockets(text):
            self._set_status("Select a court first, or paste coded dockets like 25-2531_CA8.", "idle")
        self._update_run_button()

    def _refresh_docket_display(self):
        selected_courts = self._selected_courts()
        self.refreshing_docket_display = True
        try:
            if len(selected_courts) == 1:
                court_label = selected_courts[0]
                self.docket_input.setReadOnly(False)
                self.docket_input.setPlaceholderText("25-11129, 25-22233")
                self.docket_input.setText(self.dockets_by_court.get(court_label, ""))
            elif len(selected_courts) > 1:
                completed = sum(1 for court in selected_courts if normalize_dockets(self.dockets_by_court.get(court, "")))
                total_dockets = sum(docket_count(self.dockets_by_court.get(court, "")) for court in selected_courts)
                self.docket_input.setReadOnly(True)
                self.docket_input.setPlaceholderText("Double-click to enter per-court dockets")
                self.docket_input.setText(f"{completed}/{len(selected_courts)} courts have dockets ({total_dockets} total)")
            else:
                self.docket_input.setReadOnly(False)
                self.docket_input.setPlaceholderText("Paste coded dockets/USAP rows, or select a court first")
                self.docket_input.setText("")
        finally:
            self.refreshing_docket_display = False

    def _update_run_button(self):
        selected_courts = self._selected_courts()
        has_dockets = bool(selected_courts) and all(
            normalize_dockets(self.dockets_by_court.get(court_label, "")) for court_label in selected_courts
        )
        ready = (
            bool(self.username_input.text().strip())
            and bool(self.password_input.text())
            and has_dockets
        )
        self.run_btn.setEnabled(ready)

    def _set_status(self, text, state, tooltip=None):
        display_text = " ".join(str(text).split())
        self.status_label.setText(display_text)
        self.status_label.setProperty("state", state)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.update()

        tooltip_text = str(tooltip if tooltip is not None else text).strip()
        self.status_label.setToolTip(tooltip_text)
        self.status_card.setToolTip(tooltip_text)
        self.status_card.setProperty("state", state)
        self.status_card.style().unpolish(self.status_card)
        self.status_card.style().polish(self.status_card)
        self.status_card.update()

    def _reset_run_context(self):
        self.run_total_courts = 0
        self.run_total_dockets = 0
        self.run_dockets_processed = 0
        self.run_current_court = ""
        self.run_current_court_position = 0
        self.run_current_batch_index = 0
        self.run_current_batch_total = 0
        self.run_current_batch_dockets_before = 0
        self.run_current_batch_docket_count = 0
        self.run_current_counsel = ""
        self.run_court_positions = {}
        self.run_batch_lookup = {}

    def _context_jobs(self, collection_jobs):
        if isinstance(collection_jobs, dict):
            return [
                {
                    "label": court_label,
                    "docket_numbers": normalize_dockets(dockets),
                }
                for court_label, dockets in collection_jobs.items()
            ]

        jobs = []
        for item in collection_jobs:
            if isinstance(item, dict):
                court_label = item.get("court_label", "")
                jobs.append(
                    {
                        "label": court_label,
                        "docket_numbers": normalize_dockets(item.get("docket_numbers", "")),
                    }
                )
            else:
                values = tuple(item)
                court_label = str(values[0]).strip() if len(values) > 0 else ""
                dockets = values[1] if len(values) > 1 else ""
                jobs.append(
                    {
                        "label": court_label,
                        "docket_numbers": normalize_dockets(dockets),
                    }
                )
        merged_jobs = []
        by_label = {}
        for job in jobs:
            label = job["label"]
            dockets = job["docket_numbers"]
            if not label or not dockets:
                continue
            if label not in by_label:
                by_label[label] = {"label": label, "docket_numbers": ""}
                merged_jobs.append(by_label[label])
            by_label[label]["docket_numbers"] = merge_dockets(by_label[label]["docket_numbers"], dockets)
        return merged_jobs

    def _configure_run_context(self, collection_jobs):
        self._reset_run_context()
        jobs = self._context_jobs(collection_jobs)
        self.run_total_courts = len(jobs)
        self.run_total_dockets = sum(docket_count(job["docket_numbers"]) for job in jobs)
        self.run_court_positions = {
            job["label"]: index
            for index, job in enumerate(jobs, start=1)
        }

        dockets_before = 0
        for job in jobs:
            court_label = job["label"]
            docket_text = job["docket_numbers"]
            dockets = [part.strip() for part in re.split(r"[,;\n]+", docket_text) if part.strip()]
            batches = [
                dockets[index:index + DOCKET_BATCH_SIZE]
                for index in range(0, len(dockets), DOCKET_BATCH_SIZE)
            ]
            total_batches = len(batches)
            for batch_index, batch_dockets in enumerate(batches, start=1):
                self.run_batch_lookup[(court_label, batch_index)] = {
                    "dockets_before": dockets_before,
                    "docket_count": len(batch_dockets),
                    "batch_total": total_batches,
                }
                dockets_before += len(batch_dockets)

    def _compact_court_label(self, court_label):
        court_label = court_label.strip()
        if court_label.endswith(" Court"):
            return court_label[:-6]
        return court_label

    def _plural(self, count, singular):
        suffix = "" if count == 1 else "s"
        return f"{count} {singular}{suffix}"

    def _apply_progress_prefix(self, prefix):
        batch_match = re.match(r"^(?P<court>.+?) batch (?P<batch>\d+) of (?P<total>\d+)$", prefix, re.IGNORECASE)
        if batch_match:
            court = batch_match.group("court")
            batch_index = int(batch_match.group("batch"))
            batch_total = int(batch_match.group("total"))
        elif prefix in self.run_court_positions:
            court = prefix
            batch_index = 1
            batch_total = 1
        elif prefix in CIRCUIT_COURTS:
            court = prefix
            batch_index = 1
            batch_total = 1
        else:
            return False

        if court != self.run_current_court or batch_index != self.run_current_batch_index:
            self.run_current_counsel = ""
            self.run_dockets_processed = self.run_batch_lookup.get(
                (court, batch_index),
                {"dockets_before": self.run_dockets_processed},
            )["dockets_before"]

        batch_info = self.run_batch_lookup.get((court, batch_index), {})
        self.run_current_court = court
        self.run_current_court_position = self.run_court_positions.get(court, 0)
        self.run_current_batch_index = batch_index
        self.run_current_batch_total = batch_info.get("batch_total", batch_total)
        self.run_current_batch_dockets_before = batch_info.get("dockets_before", self.run_dockets_processed)
        self.run_current_batch_docket_count = batch_info.get("docket_count", 0)
        return True

    def _mark_current_batch_submitted(self, action):
        submitted_markers = (
            "Docket numbers submitted",
            "Waiting for result links",
            "Found ",
            "Opening result",
            "Opening docket",
            "Saved ",
            "No result",
            "No results",
        )
        if not action.startswith(submitted_markers):
            return
        submitted_total = self.run_current_batch_dockets_before + self.run_current_batch_docket_count
        self.run_dockets_processed = max(self.run_dockets_processed, submitted_total)

    def _apply_counsel_progress(self, action):
        action = action.strip()

        opening_match = re.search(r"Opening result\s+(\d+)\s+of\s+(\d+)", action, re.IGNORECASE)
        if not opening_match:
            opening_match = re.search(r"result\s+(\d+)\s+of\s+(\d+)", action, re.IGNORECASE)
        if opening_match:
            self.run_current_counsel = f"Counsel {opening_match.group(1)}/{opening_match.group(2)}"
            return

        saved_match = re.search(r"Saved\s+(\d+)\s+of\s+(\d+)\s+result", action, re.IGNORECASE)
        if saved_match:
            self.run_current_counsel = f"Counsel {saved_match.group(1)}/{saved_match.group(2)}"
            return

        found_match = re.search(r"Found\s+(\d+)\s+result link", action, re.IGNORECASE)
        if found_match:
            self.run_current_counsel = f"Counsel 0/{found_match.group(1)}"
            return

        if "No result links found" in action or "No results message detected" in action:
            self.run_current_counsel = "Counsel 0/0"

    def _parse_irt_progress(self, action):
        action = " ".join(str(action).split())
        start_match = re.match(r"Starting IRT duplicate check for\s+(\d+)\s+docket", action, re.IGNORECASE)
        if start_match:
            return {
                "state": "starting",
                "total": int(start_match.group(1)),
            }

        checking_match = re.match(
            r"Checking IRT duplicate\s+(\d+)\s*/\s*(\d+):\s*(.+)$",
            action,
            re.IGNORECASE,
        )
        if checking_match:
            return {
                "state": "checking",
                "current": int(checking_match.group(1)),
                "total": int(checking_match.group(2)),
                "docket": checking_match.group(3).strip(),
            }

        complete_match = re.match(
            r"IRT duplicate check complete:\s*(\d+)\s+duplicate",
            action,
            re.IGNORECASE,
        )
        if complete_match:
            new_match = re.search(r",\s*(\d+)\s+new docket", action, re.IGNORECASE)
            return {
                "state": "complete",
                "duplicates": int(complete_match.group(1)),
                "new": int(new_match.group(1)) if new_match else 0,
            }
        return None

    def _friendly_action(self, action):
        action = " ".join(str(action).split())
        irt_progress = self._parse_irt_progress(action)
        if irt_progress:
            if irt_progress["state"] == "checking":
                return (
                    f"Checking IRT duplicate {irt_progress['current']}/{irt_progress['total']}: "
                    f"{irt_progress['docket']}"
                )
            if irt_progress["state"] == "complete":
                return "IRT duplicate check complete."
            return "Checking IRT duplicates..."

        replacements = (
            ("Initializing...", "Starting..."),
            ("Starting CourtLink collection...", "Starting CourtLink collection..."),
            ("Selecting court..", "Selecting court..."),
            ("Docket numbers submitted. Opening results...", "Searching dockets..."),
            ("Waiting for result links...", "Waiting for results..."),
            ("No result links found. Saved debug snapshot.", "No results found."),
            ("No result links found.", "No results found."),
            ("No results message detected.", "No results found."),
            ("Closing browser...", "Finishing..."),
            ("Cancellation requested. Closing browser when the current step exits...", "Cancelling run..."),
        )
        for old, new in replacements:
            if action == old:
                return new

        selected_match = re.match(r"Selected Court:\s*(.+)", action)
        if selected_match:
            return "Court selected."

        opening_docket_match = re.match(
            r"Opening docket\s+(.+?)(?:\s+\(result\s+\d+\s+of\s+\d+\))?\.{0,3}$",
            action,
            re.IGNORECASE,
        )
        if opening_docket_match:
            return f"Opening docket {opening_docket_match.group(1)}..."

        saved_docket_match = re.match(r"Saved docket\s+(.+?)\.?$", action, re.IGNORECASE)
        if saved_docket_match:
            return f"Saved docket {saved_docket_match.group(1)}."

        found_match = re.match(r"Found\s+(\d+)\s+result link", action, re.IGNORECASE)
        if found_match:
            return "Results found."

        opening_match = re.match(r"Opening result\s+\d+\s+of\s+\d+", action, re.IGNORECASE)
        if opening_match:
            return "Opening counsel record..."

        saved_match = re.match(r"Saved\s+(\d+)\s+of\s+(\d+)\s+result", action, re.IGNORECASE)
        if saved_match:
            return "Counsel files saved."

        return action

    def _format_run_tooltip(self, action):
        irt_progress = self._parse_irt_progress(action)
        if irt_progress:
            lines = ["Phase: IRT duplicate check"]
            if irt_progress["state"] == "checking":
                lines.append(f"Checked: {irt_progress['current']}/{irt_progress['total']}")
                lines.append(f"Current docket: {irt_progress['docket']}")
            elif irt_progress["state"] == "complete":
                lines.append(f"Duplicates found: {irt_progress['duplicates']}")
                lines.append(f"New dockets: {irt_progress['new']}")
            elif irt_progress.get("total"):
                lines.append(f"Dockets queued: {irt_progress['total']}")
            return lines

        lines = []
        if self.run_total_courts:
            if self.run_current_court:
                position = self.run_current_court_position or 0
                court = self._compact_court_label(self.run_current_court)
                lines.append(f"Courts: {position}/{self.run_total_courts} - {court}")
            else:
                lines.append(f"Courts: 0/{self.run_total_courts}")

        if self.run_total_dockets:
            lines.append(f"Dockets: {self.run_dockets_processed}/{self.run_total_dockets}")
        if self.run_current_batch_total > 1:
            lines.append(f"Batch: {self.run_current_batch_index}/{self.run_current_batch_total}")
        if self.run_current_counsel:
            lines.append(self.run_current_counsel.replace("Counsel ", "Counsel: "))
        if action:
            lines.append(f"Status: {self._friendly_action(action)}")

        return lines

    def _set_run_status(self, action, state="running"):
        self._set_status(
            self._friendly_action(action),
            state,
            tooltip="\n".join(self._format_run_tooltip(action)),
        )

    def _set_final_status(self, message, state, result):
        if isinstance(result, dict):
            found = result.get("found", 0)
            saved = result.get("saved", 0)
            failed = result.get("failed", 0)
            no_results = result.get("no_results", 0)
            parts = [f"Found {found}", f"Saved {saved}", f"Failed {failed}"]
            if no_results:
                parts.append(f"No Links {no_results}")
            tooltip = "\n".join(parts + [message])
            if state == "success":
                display = "Collection complete."
            elif result.get("cancelled"):
                display = "Collection cancelled."
            else:
                display = "Collection failed."
            self._set_status(display, state, tooltip=tooltip)
        else:
            self._set_status(message, state)

    def _update_run_context_from_progress(self, text):
        action = text.strip()
        if ": " in action:
            prefix, possible_action = action.split(": ", 1)
            if self._apply_progress_prefix(prefix):
                action = possible_action
        self._mark_current_batch_submitted(action)
        self._apply_counsel_progress(action)
        return action

    def _show_setup_phase(self):
        if hasattr(self, "shell"):
            self.shell.set_watermark_active(False)
        self.progress.setValue(0)
        self._reset_run_context()
        self._set_status("Ready to collect counsel.", "idle")
        self.stack.setCurrentWidget(self.setup_page)
        self._update_run_button()
        self._restart_idle_timer()

    def _show_run_phase(self):
        self._pause_idle_timer()
        self._hide_idle_screen_immediate()
        if self.intro_active:
            self._finish_intro_animation()
        if hasattr(self, "shell"):
            self.shell.set_watermark_active(True)
        self.stack.setCurrentWidget(self.run_page)

    def _open_results_folder(self):
        self._open_folder(self.current_results_dir)

    def _open_folder(self, folder_path):
        folder_path = Path(folder_path)
        folder_path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder_path))
        except Exception as exc:
            self._show_prompt("View Folder", f"Could not open results folder:\n{exc}", "warning")

    def _start_collection(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()
        selected_courts = self._selected_courts()
        court_dockets = {
            court_label: normalize_dockets(self.dockets_by_court.get(court_label, ""))
            for court_label in selected_courts
        }
        missing_courts = [court_label for court_label, dockets in court_dockets.items() if not dockets]

        if not username or not password:
            self._show_prompt("Missing Credentials", "Please enter both Lexis ID and password.", "warning")
            return
        if not selected_courts:
            self._show_prompt("Missing Courts", "Please select at least one circuit court.", "warning")
            return
        if missing_courts:
            self._show_prompt(
                "Missing Dockets",
                "Please enter docket numbers for each selected court:\n" + "\n".join(missing_courts),
                "warning",
            )
            return

        collection_jobs = self.output_jobs or court_dockets
        self.progress.setValue(0)
        self._configure_run_context(collection_jobs)
        self._set_run_status("Starting collection...", "running")
        self.cancel_run_btn.setText("Cancel Run")
        self.cancel_run_btn.setEnabled(True)
        self.run_view_btn.setEnabled(False)
        self._show_run_phase()

        self.worker = SearchWorker(
            court_dockets=collection_jobs,
            username=username,
            password=password,
            headless=self.headless_checkbox.isChecked(),
            renamed_rows=self.renamed_rows,
            directory_copy_target=self.directory_copy_target,
        )
        self.worker_thread = QThread()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._update_progress)
        self.worker.finished.connect(self._collection_finished)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self._worker_thread_finished)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def _cancel_collection(self):
        if not self.worker_thread or not self.worker_thread.isRunning():
            return
        self.cancel_run_btn.setText("Cancelling...")
        self.cancel_run_btn.setEnabled(False)
        self._set_run_status("Cancellation requested. Closing browser when the current step exits...", "running")
        if self.worker:
            self.worker.cancel()

    def _update_progress(self, value, text):
        self.progress.setValue(value)
        action = self._update_run_context_from_progress(text)
        self._set_run_status(action, "running")

    def _collection_finished(self, success, message, result):
        if isinstance(result, dict):
            run_dir = result.get("run_dir")
            if run_dir:
                self.current_results_dir = Path(run_dir)

        if success:
            self.progress.setValue(100)
            self._set_final_status(message, "success", result)
            self.pending_success_message = message
            self.pending_success_result = result if isinstance(result, dict) else {}
        else:
            self._set_final_status(message, "error", result)
            self.pending_failure_message = message
            self.pending_failure_result = result if isinstance(result, dict) else {}

        self.cancel_run_btn.setEnabled(False)
        self.run_view_btn.setEnabled(True)

    def _worker_thread_finished(self):
        self.worker = None
        self.worker_thread = None
        if self.pending_success_result is not None:
            QTimer.singleShot(0, self._show_success_prompt)
        elif self.pending_failure_result is not None:
            QTimer.singleShot(0, self._show_failure_prompt)

    def _show_success_prompt(self):
        result = self.pending_success_result or {}
        message = self.pending_success_message or "Collection completed successfully."
        self.pending_success_message = None
        self.pending_success_result = None

        run_dir = Path(result.get("run_dir") or self.current_results_dir)
        copy_note = ""
        if result.get("directory_copy_target"):
            if result.get("directory_copy_error"):
                copy_note = f"\n\nCould not copy back to source directory:\n{result['directory_copy_error']}"
            elif result.get("directory_copied_paths"):
                copy_note = f"\n\nCopies were also placed in:\n{result['directory_copy_target']}"
        self._show_prompt(
            "Collection Complete",
            f"{message}{copy_note}\n\nThe run folder will open after you click OK.",
            "success",
        )
        self._open_folder(run_dir)
        self._reset_for_fresh_run()

    def _show_failure_prompt(self):
        result = self.pending_failure_result or {}
        message = self.pending_failure_message or "Collection failed."
        self.pending_failure_message = None
        self.pending_failure_result = None

        run_dir = Path(result.get("run_dir") or self.current_results_dir)
        title = "Collection Cancelled" if result.get("cancelled") else "Collection Failed"
        copy_note = ""
        if result.get("directory_copy_target"):
            if result.get("directory_copy_error"):
                copy_note = f"\n\nCould not copy back to source directory:\n{result['directory_copy_error']}"
            elif result.get("directory_copied_paths"):
                copy_note = f"\n\nAvailable copies were also placed in:\n{result['directory_copy_target']}"
        self._show_prompt(
            title,
            f"{message}{copy_note}\n\nRun folder:\n{run_dir}\n\nThe UI will reset after you click OK.",
            "warning" if result.get("cancelled") else "error",
        )
        self._reset_for_fresh_run()

    def _reset_for_fresh_run(self):
        if self.docket_popup.isVisible():
            self.docket_popup.hide()
        if self.settings_popup.isVisible():
            self.settings_popup.hide()
        self.current_results_dir = RUNS_ROOT
        self.court_combo.set_checked_items([], emit=False)
        self.dockets_by_court = {}
        self.output_jobs = []
        self.renamed_rows = []
        self.directory_copy_target = None
        self.pending_success_message = None
        self.pending_success_result = None
        self.pending_failure_message = None
        self.pending_failure_result = None
        self.password_input.clear()
        self.password_visible = False
        self.password_input.setEchoMode(QLineEdit.Password)
        self._sync_password_visibility_action()
        self.docket_input.setReadOnly(False)
        self._refresh_docket_display()
        self.cancel_run_btn.setText("Cancel Run")
        self.cancel_run_btn.setEnabled(False)
        self.run_view_btn.setEnabled(True)
        self._show_setup_phase()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_settings_button()
        self._position_intro_overlay()
        self._position_idle_overlay()

    def _position_settings_button(self):
        if not hasattr(self, "settings_btn") or not hasattr(self, "setup_card"):
            return
        margin = 16
        self.settings_btn.move(
            self.setup_card.width() - self.settings_btn.width() - margin,
            14,
        )
        self.settings_btn.raise_()

    def closeEvent(self, event):
        if self.worker_thread and self.worker_thread.isRunning():
            self._show_prompt(
                "Collection Running",
                "Counsel collection is still running. Please wait for it to finish before closing.",
                "warning",
            )
            event.ignore()
            return
        QApplication.instance().removeEventFilter(self)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app_icon = QIcon(str(asset_path("shopee.ico")))
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    window = CounselCollectorWindow()
    window.show()
    window.play_intro_animation()
    sys.exit(app.exec_())
