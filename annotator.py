
#!/usr/bin/env python3
"""
Paste-and-arrange image sheets.

Importable as a module:

    from annotator import AnnotatorWidget, AnnotatorDialog

`AnnotatorWidget` owns the whole document (sheets, tabs, annotations) and can be
embedded in any layout.  It serializes to a plain-dict metadata block plus a
map of {archive_name: png_bytes}, which the host application stores however
it likes -- MonteCarlo_Calculator drops both into a .mcz zip container.

Runnable standalone:

    python annotator.py

Mouse / keys
    Ctrl+V          paste image from clipboard onto the current sheet
    Ctrl+C          copy the selected image to the clipboard (original
                    full-resolution image, whatever its size on the sheet)
    Ctrl+T          add a text label     Ctrl+R  add a double-headed arrow
    drag            move an item
    double-click    edit a text label
    Shift+Wheel     resize the image under the cursor
    Del             delete selected item(s)
    Ctrl+Wheel      zoom the view       Ctrl+0  fit sheet to window
    Ctrl+P          export PDF

Sheet tabs sit along the bottom; "+" adds one, the x on a tab removes it, and
double-clicking a tab renames that sheet.
"""

import sys

from PyQt5.QtCore import (QBuffer, QByteArray, QIODevice, QLineF, QMarginsF,
                          QPointF, QRectF, QSize, Qt, pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QFont, QKeySequence, QPageLayout,
                         QPageSize, QPainter, QPainterPath, QPainterPathStroker,
                         QImage, QPdfWriter, QPen, QPixmap, QPolygonF)
from PyQt5.QtWidgets import (QAction, QApplication, QDialog, QFileDialog,
                             QGraphicsItem, QGraphicsPixmapItem,
                             QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem,
                             QGraphicsView, QHBoxLayout, QInputDialog, QLabel,
                             QMainWindow, QMessageBox, QStackedWidget, QTabBar,
                             QToolBar, QToolButton, QVBoxLayout, QWidget)

# Letter page at 96 dpi, minus 0.5in margins -> the usable canvas.
PAGE_PORTRAIT = (816.0, 1056.0)
MARGIN = 48.0

IMAGE_DIR = "images"

# Appearance of added annotations. Deliberately fixed rather than
# per-item: every style choice exposed in the UI costs a picker, a
# serialized field, and a load-time validation path. Change a value here
# and every annotation of that kind follows.
TEXT_FONT_FAMILY = "Arial"
TEXT_FONT_POINT_SIZE = 11
TEXT_COLOR = QColor(200, 30, 30)
TEXT_PLACEHOLDER = "Text"

ARROW_COLOR = QColor(200, 30, 30)
ARROW_THICKNESS = 2.0
ARROW_HEAD_LENGTH = 14.0
ARROW_HEAD_HALF_WIDTH = 5.0
ARROW_DEFAULT_LENGTH = 180.0

# Half-width of the square endpoint grips, in scene units. Also the radius
# of the grab zone, so a grip is always easier to hit than it looks.
ARROW_GRIP = 4.0
SELECTION_COLOR = QColor(0, 120, 215)

# Each new annotation steps down-right from the last so a fresh one never
# lands exactly on top of the previous one.
CASCADE_STEP = 24
CASCADE_WRAP = 6


def pixmap_to_png(pixmap):
    """Encode a QPixmap as PNG bytes."""
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pixmap.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()
    return data


def png_to_pixmap(data):
    """Decode PNG bytes into a QPixmap. Returns None if the data is bad."""
    pixmap = QPixmap()
    if not pixmap.loadFromData(QByteArray(data), "PNG"):
        return None
    return pixmap


class ImageItem(QGraphicsPixmapItem):
    """A placed image. Carries its own PNG bytes so saving never re-encodes."""

    def __init__(self, pixmap, png_bytes):
        super().__init__(pixmap)
        self.png_bytes = png_bytes
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)
        self.setTransformationMode(Qt.SmoothTransformation)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            pen = QPen(QColor(0, 120, 215), 2 / max(self.scale(), 0.01), Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect())


class TextItem(QGraphicsTextItem):
    """A text label. Font and colour come from the module constants."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setFont(QFont(TEXT_FONT_FAMILY, TEXT_FONT_POINT_SIZE))
        self.setDefaultTextColor(TEXT_COLOR)
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)

    def begin_editing(self):
        """Arm the caret and take focus, as if double-clicked."""
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus(Qt.MouseFocusReason)

    def mouseDoubleClickEvent(self, event):
        # Dragging and text selection both want a mouse drag, so editing is
        # only armed on an explicit double-click. Until then a drag moves
        # the label, which is what you want the other 95% of the time.
        self.begin_editing()
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event):
        # Clicking away commits and re-arms dragging.
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        super().focusOutEvent(event)

    def contextMenuEvent(self, event):
        # QGraphicsTextItem's built-in handler builds and exec_()s a
        # rich-text editing menu (undo/redo/cut/copy/paste/select-all) owned
        # by the viewport. That menu is unreliable here and offers nothing
        # useful anyway, since font and colour are fixed by the module
        # constants and there is no formatting to change. Swallowing the
        # event removes the whole path rather than patching around it.
        event.accept()


class ArrowItem(QGraphicsItem):
    """A double-headed arrow whose two ends are dragged independently.

    Endpoints are held in item coordinates, so pos() moves the arrow as a
    whole and the endpoints stay relative to it -- the same pos()-plus-
    geometry split that lets a saved sheet reload exactly where it was
    left. The grips are hit-tested here rather than being child items,
    which keeps this to one class with no parent/child bookkeeping.
    """

    def __init__(self, p1, p2):
        super().__init__()
        self._p1 = QPointF(p1)
        self._p2 = QPointF(p2)
        self._dragging = None      # None, or which endpoint is being moved
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)

    # -- geometry ---------------------------------------------------------- #
    def endpoints(self):
        return QPointF(self._p1), QPointF(self._p2)

    def set_endpoints(self, p1, p2):
        self.prepareGeometryChange()
        self._p1 = QPointF(p1)
        self._p2 = QPointF(p2)
        self.update()

    def boundingRect(self):
        pad = ARROW_THICKNESS + ARROW_HEAD_HALF_WIDTH + ARROW_GRIP
        return QRectF(self._p1, self._p2).normalized().adjusted(-pad, -pad, pad, pad)

    def shape(self):
        # A 2px line is nearly impossible to click, so the clickable region
        # is a fattened stroke plus a disc around each grip.
        path = QPainterPath()
        path.moveTo(self._p1)
        path.lineTo(self._p2)
        stroker = QPainterPathStroker()
        stroker.setWidth(max(ARROW_THICKNESS * 4.0, ARROW_GRIP * 2.0))
        hit = stroker.createStroke(path)
        for point in (self._p1, self._p2):
            hit.addEllipse(point, ARROW_GRIP * 2.0, ARROW_GRIP * 2.0)
        return hit

    @staticmethod
    def _head(tip, tail):
        """Filled triangle at `tip`, pointing away from `tail`."""
        line = QLineF(tail, tip)
        length = line.length()
        if length == 0:
            return QPolygonF()
        ux, uy = line.dx() / length, line.dy() / length
        base = QPointF(tip.x() - ux * ARROW_HEAD_LENGTH,
                       tip.y() - uy * ARROW_HEAD_LENGTH)
        # Perpendicular to the shaft, scaled to half the head width.
        px, py = -uy * ARROW_HEAD_HALF_WIDTH, ux * ARROW_HEAD_HALF_WIDTH
        return QPolygonF([
            QPointF(tip),
            QPointF(base.x() + px, base.y() + py),
            QPointF(base.x() - px, base.y() - py),
        ])

    def paint(self, painter, option, widget=None):
        if QLineF(self._p1, self._p2).length() <= 0:
            return

        pen = QPen(ARROW_COLOR, ARROW_THICKNESS)
        pen.setCapStyle(Qt.FlatCap)
        painter.setPen(pen)
        painter.setBrush(QBrush(ARROW_COLOR))
        painter.drawLine(self._p1, self._p2)
        painter.drawPolygon(self._head(self._p1, self._p2))
        painter.drawPolygon(self._head(self._p2, self._p1))

        if self.isSelected():
            painter.setPen(QPen(SELECTION_COLOR, 1))
            painter.setBrush(QBrush(Qt.white))
            for point in (self._p1, self._p2):
                painter.drawRect(QRectF(point.x() - ARROW_GRIP,
                                        point.y() - ARROW_GRIP,
                                        ARROW_GRIP * 2, ARROW_GRIP * 2))

    # -- endpoint dragging ------------------------------------------------- #
    def _grip_at(self, pos):
        """Which endpoint `pos` grabs, or None for the shaft."""
        for name, point in (("p1", self._p1), ("p2", self._p2)):
            if QLineF(pos, point).length() <= ARROW_GRIP * 2.0:
                return name
        return None

    def mousePressEvent(self, event):
        # Grips only respond once the arrow is selected, so the first click
        # on an unselected arrow can never accidentally reshape it.
        self._dragging = self._grip_at(event.pos()) if self.isSelected() else None
        if self._dragging:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging == "p1":
            self.set_endpoints(event.pos(), self._p2)
        elif self._dragging == "p2":
            self.set_endpoints(self._p1, event.pos())
        else:
            super().mouseMoveEvent(event)
            return
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


# Everything a sheet can hold. Kept as one tuple so adding a kind means
# touching this line rather than hunting isinstance checks.
ANNOTATION_TYPES = (ImageItem, TextItem, ArrowItem)

# Stacking bands. Images live in the lower band and labels/arrows in the
# upper one, so an annotation can never end up behind the image it points
# at -- clicking an image raises it only within its own band. The gap is
# far larger than any realistic item count, so the bands cannot meet.
Z_BAND_IMAGE = 0.0
Z_BAND_OVERLAY = 1_000_000.0


class SheetScene(QGraphicsScene):
    def __init__(self, landscape=False):
        super().__init__()
        self.setBackgroundBrush(QBrush(QColor(120, 120, 120)))
        self.page = QGraphicsRectItem()
        self.page.setBrush(QBrush(Qt.white))
        self.page.setPen(QPen(QColor(60, 60, 60), 1))
        self.page.setZValue(-1000)
        self.addItem(self.page)
        self._z = 0.0
        self.landscape = False
        self.set_landscape(landscape)

    def set_landscape(self, landscape):
        """Orientation is per sheet, so each scene owns its own flag."""
        self.landscape = bool(landscape)
        w, h = PAGE_PORTRAIT
        if self.landscape:
            w, h = h, w
        self.page.setRect(0, 0, w, h)
        self.setSceneRect(-20, -20, w + 40, h + 40)

    def page_rect(self):
        return self.page.rect()

    def annotation_items(self):
        """Everything placed on this sheet, in stacking order (bottom first)."""
        return sorted((i for i in self.items() if isinstance(i, ANNOTATION_TYPES)),
                      key=lambda i: i.zValue())

    def image_items(self):
        """Placed images only, in stacking order (bottom first)."""
        return [i for i in self.annotation_items() if isinstance(i, ImageItem)]

    # -- stacking ---------------------------------------------------------- #
    @staticmethod
    def _z_band(item):
        return Z_BAND_IMAGE if isinstance(item, ImageItem) else Z_BAND_OVERLAY

    def _next_z(self, item):
        """Next z for `item`, on top of its own band but never another's."""
        self._z += 1
        return self._z_band(item) + self._z

    # -- placement --------------------------------------------------------- #
    def _add_new(self, item):
        """Place a newly created item: cascade it, put it on top, select it."""
        step = (len(self.annotation_items()) % CASCADE_WRAP) * CASCADE_STEP
        item.setPos(MARGIN + step, MARGIN + step)
        item.setZValue(self._next_z(item))
        self.addItem(item)
        self.clearSelection()
        item.setSelected(True)
        return item

    def _add_restored(self, item, x, y):
        """Re-place a saved item at its stored position.

        Stacking comes from the order entries were saved in, not a stored
        z value: serialize() writes items bottom-first, so replaying that
        order through _next_z reproduces the same relative stacking while
        re-deriving the band. A sheet saved before the bands existed is
        corrected on load rather than reloading with a label buried.
        """
        item.setPos(x, y)
        item.setZValue(self._next_z(item))
        self.addItem(item)
        return item

    # -- creation ---------------------------------------------------------- #
    def add_image(self, pixmap, png_bytes=None):
        """Paste a new image, auto-fitting it to the page."""
        if png_bytes is None:
            png_bytes = pixmap_to_png(pixmap)
        item = ImageItem(pixmap, png_bytes)
        page = self.page_rect()
        fit = min(1.0,
                  (page.width() - 2 * MARGIN) / pixmap.width(),
                  (page.height() - 2 * MARGIN) / pixmap.height())
        if fit < 1.0:
            item.setScale(fit)
        return self._add_new(item)

    def add_text(self, text=TEXT_PLACEHOLDER):
        return self._add_new(TextItem(text))

    def add_arrow(self):
        # Horizontal by default; both ends are draggable from there.
        return self._add_new(ArrowItem(QPointF(0, 0),
                                       QPointF(ARROW_DEFAULT_LENGTH, 0)))

    # -- restoration ------------------------------------------------------- #
    def restore_image(self, pixmap, png_bytes, x, y, scale):
        item = ImageItem(pixmap, png_bytes)
        item.setScale(scale)
        return self._add_restored(item, x, y)

    def restore_text(self, text, x, y):
        return self._add_restored(TextItem(text), x, y)

    def restore_arrow(self, x, y, p1, p2):
        return self._add_restored(ArrowItem(p1, p2), x, y)

    def mousePressEvent(self, event):
        item = self.itemAt(event.scenePos(), self.views()[0].transform())
        if isinstance(item, ANNOTATION_TYPES):
            # Raises within the item's band only, so selecting an image
            # cannot lift it over the labels and arrows on top of it.
            item.setZValue(self._next_z(item))
        super().mousePressEvent(event)


class SheetView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    def wheelEvent(self, event):
        mods = event.modifiers()
        if mods & Qt.ShiftModifier:
            scene_pos = self.mapToScene(event.pos())
            item = self.scene().itemAt(scene_pos, self.transform())
            if isinstance(item, ImageItem):
                factor = 1.08 if event.angleDelta().y() > 0 else 1 / 1.08
                old_scale = item.scale()
                new_scale = max(0.02, old_scale * factor)
                # Keep the point under the cursor pinned by moving the item
                # rather than shifting its transform origin. The origin stays
                # at (0, 0), so pos + scale still fully describe the item and
                # a saved sheet reloads exactly where it was left.
                ratio = new_scale / old_scale
                item.setPos(scene_pos - (scene_pos - item.pos()) * ratio)
                item.setScale(new_scale)
                event.accept()
                return
        if mods & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    def fit(self):
        self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)


class SheetTabBar(QTabBar):
    """Bottom tab bar; double-click a tab to rename that sheet."""

    def mouseDoubleClickEvent(self, event):
        index = self.tabAt(event.pos())
        if index < 0:
            return
        name, ok = QInputDialog.getText(self, "Rename sheet", "Sheet name:",
                                        text=self.tabText(index))
        name = name.strip()
        if ok and name:
            self.setTabText(index, name)
            self.setTabData(index, True)   # marks the name as user-chosen


class AnnotatorWidget(QWidget):
    """The whole annotator document: toolbar, sheet tabs, and image sheets."""

    statusChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pages = QStackedWidget()

        self.tabs = SheetTabBar()
        self.tabs.setShape(QTabBar.RoundedSouth)
        self.tabs.setTabsClosable(True)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabCloseRequested.connect(self.delete_sheet)

        add_btn = QToolButton()
        add_btn.setText("+")
        add_btn.setAutoRaise(True)
        add_btn.setToolTip("Add sheet")
        add_btn.setFixedSize(QSize(24, 24))
        add_btn.clicked.connect(self.add_sheet)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(4, 0, 4, 0)
        tab_row.setSpacing(2)
        tab_row.addWidget(self.tabs)
        tab_row.addWidget(add_btn)
        tab_row.addStretch(1)

        self.status_label = QLabel(
            "Ctrl+C copy image  |  Ctrl+V paste  |  Ctrl+T text  |  Ctrl+R arrow  |  "
            "Shift+Wheel resize image  |  drag to move")
        self.status_label.setStyleSheet("color: gray;")
        self.statusChanged.connect(self.status_label.setText)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 2)
        root.setSpacing(2)
        root.addWidget(self._build_toolbar())
        root.addWidget(self.pages, 1)
        root.addLayout(tab_row)
        root.addWidget(self.status_label)

        self.add_sheet()

    def _build_toolbar(self):
        tb = QToolBar("Annotator")
        tb.setMovable(False)

        def act(text, shortcut, slot, checkable=False):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            a.setCheckable(checkable)
            a.triggered.connect(slot)
            tb.addAction(a)
            self.addAction(a)
            return a

        act("Copy", QKeySequence.Copy, self.copy_image)
        act("Paste", QKeySequence.Paste, self.paste_image)
        act("Text", "Ctrl+T", self.add_text)
        act("Arrow", "Ctrl+R", self.add_arrow)
        tb.addSeparator()
        act("Delete", QKeySequence.Delete, self.delete_selection)
        act("Fit", "Ctrl+0", self.fit_current)
        self.landscape_action = act("Landscape", None, self.toggle_landscape, True)
        tb.addSeparator()
        act("Export PDF\u2026", "Ctrl+P", self.export_pdf)
        return tb

    # -- sheets ------------------------------------------------------------ #
    def _on_tab_changed(self, index):
        if 0 <= index < self.pages.count():
            self.pages.setCurrentIndex(index)
        self._sync_landscape_action()

    def _sync_landscape_action(self):
        """The toggle reflects the current sheet, not the document."""
        scene = self.current_scene()
        self.landscape_action.blockSignals(True)
        self.landscape_action.setEnabled(scene is not None)
        self.landscape_action.setChecked(bool(scene and scene.landscape))
        self.landscape_action.blockSignals(False)

    def current_landscape(self):
        scene = self.current_scene()
        return bool(scene and scene.landscape)

    def add_sheet(self):
        # A new sheet inherits the orientation of the one you were on, which
        # is nearly always what you want when adding several in a row.
        view = self._new_sheet(landscape=self.current_landscape())
        self.renumber()
        self.tabs.setCurrentIndex(self.pages.count() - 1)
        view.fit()
        return view

    def _new_sheet(self, name=None, custom_name=False, landscape=False):
        """Append a sheet without touching the current selection."""
        view = SheetView(SheetScene(landscape))
        self.pages.addWidget(view)
        index = self.tabs.addTab(name or "")
        if custom_name:
            self.tabs.setTabData(index, True)
        return view

    def delete_sheet(self, index):
        if self.pages.count() <= 1:
            return
        view = self.pages.widget(index)
        self.pages.removeWidget(view)
        view.deleteLater()
        self.tabs.removeTab(index)
        self.renumber()
        self._sync_landscape_action()

    def clear_sheets(self):
        """Tear every sheet down. Leaves the document with zero sheets."""
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self.tabs.blockSignals(False)
        while self.pages.count():
            view = self.pages.widget(0)
            self.pages.removeWidget(view)
            view.deleteLater()

    def renumber(self):
        """Re-label only the tabs the user hasn't named."""
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) is not True:
                self.tabs.setTabText(i, "Sheet %d" % (i + 1))

    def current_view(self):
        return self.pages.currentWidget()

    def current_scene(self):
        v = self.current_view()
        return v.scene() if v else None

    # -- images ------------------------------------------------------------ #
    def copy_image(self):
        """Put the selected image on the clipboard at its original
        resolution -- the stored PNG, not the scaled copy on the sheet.

        While a text label is being edited, its own editor takes Ctrl+C
        first (it claims the shortcut), so this never runs then and
        copying text works as usual.
        """
        scene = self.current_scene()
        if scene is None:
            return
        images = [item for item in scene.selectedItems() if isinstance(item, ImageItem)]
        if len(images) != 1:
            QMessageBox.information(
                self, "Copy",
                "Select one image to copy." if not images else
                "Several images are selected. Select just one to copy it.")
            return
        image = QImage.fromData(images[0].png_bytes, "PNG")
        QApplication.clipboard().setImage(image)
        self.statusChanged.emit(
            f"Copied image ({image.width()} \u00d7 {image.height()} px) to the clipboard.")

    def paste_image(self):
        img = QApplication.clipboard().image()
        if img.isNull():
            QMessageBox.information(self, "Paste", "No image on the clipboard.")
            return
        scene = self.current_scene()
        if scene is None:
            return
        scene.add_image(QPixmap.fromImage(img))

    def add_text(self):
        scene = self.current_scene()
        if scene is None:
            return
        # Placeholder text starts out selected for editing, so typing
        # replaces it immediately instead of appending to it.
        item = scene.add_text()
        item.begin_editing()
        item.setSelected(True)
        cursor = item.textCursor()
        cursor.select(cursor.Document)
        item.setTextCursor(cursor)

    def add_arrow(self):
        scene = self.current_scene()
        if scene is None:
            return
        scene.add_arrow()

    def delete_selection(self):
        scene = self.current_scene()
        if scene is None:
            return
        for item in scene.selectedItems():
            scene.removeItem(item)

    def fit_current(self):
        view = self.current_view()
        if view:
            view.fit()

    def toggle_landscape(self, checked):
        view = self.current_view()
        if view is None:
            return
        view.scene().set_landscape(checked)
        view.fit()

    def has_content(self):
        """True if any sheet holds an image, label or arrow."""
        return any(self.pages.widget(i).scene().annotation_items()
                   for i in range(self.pages.count()))

    # -- persistence ------------------------------------------------------- #
    def serialize(self):
        """Return (metadata_dict, {archive_name: png_bytes}).

        Each sheet stores a single "items" list in stacking order, with a
        "kind" tag per entry. Order carries the stacking, so there is no
        stored z value that could disagree with it -- load replays the list
        bottom-first and re-derives each item's band.
        """
        images = {}
        sheets = []
        counter = 0
        for i in range(self.pages.count()):
            scene = self.pages.widget(i).scene()
            entries = []
            for item in scene.annotation_items():
                entry = {"x": item.pos().x(), "y": item.pos().y()}
                if isinstance(item, ImageItem):
                    counter += 1
                    arcname = "%s/img_%04d.png" % (IMAGE_DIR, counter)
                    images[arcname] = item.png_bytes
                    entry.update(kind="image", file=arcname, scale=item.scale())
                elif isinstance(item, TextItem):
                    entry.update(kind="text", text=item.toPlainText())
                elif isinstance(item, ArrowItem):
                    p1, p2 = item.endpoints()
                    entry.update(kind="arrow",
                                 x1=p1.x(), y1=p1.y(), x2=p2.x(), y2=p2.y())
                else:
                    continue
                entries.append(entry)
            sheets.append({
                "name": self.tabs.tabText(i),
                "custom_name": self.tabs.tabData(i) is True,
                "landscape": scene.landscape,
                "items": entries,
            })
        return {"sheets": sheets}, images

    def load_state(self, meta, images):
        """Replace the document with saved state.

        `meta` is what serialize() produced (None or {} resets to one empty
        sheet); `images` maps archive name -> png bytes.  Returns a list of
        human-readable warnings for anything that could not be restored.
        """
        warnings = []
        meta = meta or {}
        images = images or {}

        self.clear_sheets()

        for sheet in meta.get("sheets", []) or []:
            view = self._new_sheet(sheet.get("name") or "",
                                   bool(sheet.get("custom_name")),
                                   bool(sheet.get("landscape")))
            scene = view.scene()
            for entry in sheet.get("items", []) or []:
                kind = entry.get("kind")
                x = float(entry.get("x", MARGIN))
                y = float(entry.get("y", MARGIN))

                if kind == "image":
                    arcname = entry.get("file")
                    blob = images.get(arcname)
                    if blob is None:
                        warnings.append("Missing image data: %s" % arcname)
                        continue
                    pixmap = png_to_pixmap(blob)
                    if pixmap is None or pixmap.isNull():
                        warnings.append("Unreadable image data: %s" % arcname)
                        continue
                    scene.restore_image(pixmap, blob, x, y,
                                        float(entry.get("scale", 1.0)))
                elif kind == "text":
                    scene.restore_text(str(entry.get("text", "")), x, y)
                elif kind == "arrow":
                    scene.restore_arrow(
                        x, y,
                        QPointF(float(entry.get("x1", 0.0)),
                                float(entry.get("y1", 0.0))),
                        QPointF(float(entry.get("x2", ARROW_DEFAULT_LENGTH)),
                                float(entry.get("y2", 0.0))),
                    )
                else:
                    warnings.append("Unknown annotation type: %r" % (kind,))

        if self.pages.count() == 0:
            self._new_sheet()

        self.renumber()
        self.tabs.setCurrentIndex(0)
        self.pages.setCurrentIndex(0)
        self._sync_landscape_action()
        self.fit_current()
        return warnings

    # -- export ------------------------------------------------------------ #
    def export_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", "sheets.pdf",
                                              "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        scenes = [self.pages.widget(i).scene() for i in range(self.pages.count())]

        def orientation(scene):
            return (QPageLayout.Landscape if scene.landscape
                    else QPageLayout.Portrait)

        writer = QPdfWriter(path)
        writer.setResolution(300)
        writer.setPageSize(QPageSize(QPageSize.Letter))
        writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)
        # The first page's orientation has to be set before the painter opens;
        # every later one is set just before its newPage() call.
        writer.setPageOrientation(orientation(scenes[0]))

        painter = QPainter(writer)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)

        for i, scene in enumerate(scenes):
            scene.clearSelection()
            border = scene.page.pen()
            scene.page.setPen(QPen(Qt.NoPen))
            if i > 0:
                writer.setPageOrientation(orientation(scene))
                writer.newPage()
            # Recomputed per page: the paint rect changes with orientation.
            target = QRectF(QPointF(0, 0),
                            QRectF(writer.pageLayout().paintRectPixels(
                                writer.resolution())).size())
            scene.render(painter, target, scene.page_rect(), Qt.KeepAspectRatio)
            scene.page.setPen(border)
        painter.end()
        self.statusChanged.emit("Exported %d sheet(s) to %s"
                                % (self.pages.count(), path))


class AnnotatorDialog(QDialog):
    """Non-modal window around a long-lived AnnotatorWidget.

    The host keeps the dialog alive so sheet state survives closing it.
    """

    def __init__(self, annotator, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Annotations")
        self.resize(950, 850)
        self.annotator = annotator
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(annotator)


def main():
    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("Image Sheets")
    win.resize(950, 850)
    widget = AnnotatorWidget()
    win.setCentralWidget(widget)
    widget.statusChanged.connect(win.statusBar().showMessage)
    win.show()
    widget.fit_current()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
