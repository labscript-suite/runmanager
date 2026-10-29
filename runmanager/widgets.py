#####################################################################
#                                                                   #
# __main__.py                                                       #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Widgets for the Runmanager GUI
"""

import functools
import time
import weakref

from qtutils.qt import QT_ENV, QtCore, QtGui, QtWidgets
from qtutils.qt.QtCore import pyqtSignal as Signal
from qtutils.icons import Icon, in_mode

from runmanager.group_manager import history_entry_expansion
from runmanager.tokenizer import find_comments

EXPANSION_NONE_TEXT = 'none'


def mix_colors(color0, color1, fraction):
    """The colour that is the given fraction of the way from color0 to color1 in RGB"""
    return QtGui.QColor.fromRgbF(
        *((1 - fraction) * a + fraction * b for a, b in zip(color0.getRgbF(), color1.getRgbF()))
    )


# Colour that is mixed into the button colour for the engage button, and its fraction:
ENGAGE_RED = '#dc3232'
ENGAGE_RED_FRACTION = 0.6
# Colours that are mixed into the base colour of the globals table for the cells of
# globals that are valid, use deprecated names or cannot be evaluated, and their
# fractions:
CELL_COLORS = {
    'ok': ('#32e678', 0.4),
    'deprecated': ('#e6b432', 0.5),
    'error': ('#e03030', 0.5),
}
# Fraction by which we mix the highlight colour into the colour of selected cells:
SELECTION_FRACTION = 0.25
# Colours that are mixed into the button colour for the buttons of globals that are False
# and True, and their fractions:
BOOL_BUTTON_COLORS = {
    False: ('#053605', 0.85),
    True: ('#20e020', 0.9),
}
# Text colours of the buttons of globals that are False and True:
BOOL_BUTTON_TEXT_COLORS = {
    False: '#ffffff',
    True: '#000000',
}
# Fraction by which we mix the shadow colour into the colour of a bool button for its
# border:
BOOL_BUTTON_BORDER_FRACTION = 0.3


def engage_button_style_sheet(palette):
    """Style sheet for the engage button: the button colour of the palette mixed with
    ENGAGE_RED, lighter on hover and darker when pressed. The palette must not be the
    button's own, which has the background colour of the style sheet as its button
    colour."""
    background = mix_colors(
        palette.color(QtGui.QPalette.ColorRole.Button),
        QtGui.QColor(ENGAGE_RED),
        ENGAGE_RED_FRACTION,
    )
    return f"""
QPushButton {{
    background-color: {background.name()};
    border: 1px solid {background.darker(130).name()};
    border-radius: 3px;
    padding: 4px;
}}
QPushButton:hover {{
    background-color: {background.lighter(115).name()};
}}
QPushButton:pressed {{
    background-color: {background.darker(115).name()};
}}
"""


def paint_bool_button(painter, rect, palette, font, value):
    """Paint the button that shows and toggles the value of a global that is True or
    False"""
    hue, fraction = BOOL_BUTTON_COLORS[value]
    fill = mix_colors(
        palette.color(QtGui.QPalette.ColorRole.Button), QtGui.QColor(hue), fraction
    )
    border = mix_colors(
        fill, palette.color(QtGui.QPalette.ColorRole.Shadow), BOOL_BUTTON_BORDER_FRACTION
    )
    painter.save()
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setPen(border)
    painter.setBrush(fill)
    painter.drawRoundedRect(QtCore.QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(BOOL_BUTTON_TEXT_COLORS[value]))
    painter.drawText(rect, QtCore.Qt.AlignCenter, str(value))
    painter.restore()


def set_selection_palette(widget):
    """Give the widget the highlight colour of the application palette, partly
    transparent, and the window text colour as the highlighted text colour"""
    source = QtWidgets.QApplication.palette(widget)
    highlight = source.color(QtGui.QPalette.Highlight)
    highlight.setAlphaF(SELECTION_FRACTION)
    text = source.color(QtGui.QPalette.WindowText)
    palette = widget.palette()
    if palette.color(QtGui.QPalette.Highlight) == highlight and palette.color(
        QtGui.QPalette.HighlightedText
    ) == text:
        return
    for group in [QtGui.QPalette.Active, QtGui.QPalette.Inactive]:
        palette.setColor(group, QtGui.QPalette.Highlight, highlight)
        palette.setColor(group, QtGui.QPalette.HighlightedText, text)
    widget.setPalette(palette)


class FingerTabBarWidget(QtWidgets.QTabBar):

    """A TabBar with the tabs on the left and the text horizontal. Credit to
    @LegoStormtroopr, https://gist.github.com/LegoStormtroopr/5075267. We will
    promote the TabBar from the ui file to one of these."""

    def __init__(self, parent=None, minwidth=180, minheight=30, **kwargs):
        QtWidgets.QTabBar.__init__(self, parent, **kwargs)
        self.minwidth = minwidth
        self.minheight = minheight
        self._movable = None
        self.tab_movable = {}
        self.paint_clip = None

    def setMovable(self, movable, index=None):
        """Set tabs movable on an individual basis, or set for all tabs if no
        index specified"""
        if index is None:
            self._movable = movable
            self.tab_movable = {}
            QtWidgets.QTabBar.setMovable(self, movable)
        else:
            self.tab_movable[int(index)] = bool(movable)

    def isMovable(self, index=None):
        if index is None:
            if self._movable is None:
                self._movable = QtWidgets.QTabBar.isMovable(self)
            return self._movable
        return self.tab_movable.get(index, self._movable)

    def setTabIconOnRight(self, index, on_right):
        """Draw the tab's icon at the right of the tab, to the left of its button,
        instead of to the left of its text"""
        self.setTabData(index, bool(on_right))
        self.update()

    def tabIconOnRight(self, index):
        return bool(self.tabData(index))

    def indexAtPos(self, point):
        for index in range(self.count()):
            if self.tabRect(index).contains(point):
                return index

    def mouseEventIndex(self, event):
        if QT_ENV == 'PyQt5':
            return self.indexAtPos(event.pos())
        else:
            # Qt6 position returns QPointF instead of QPoint
            return self.indexAtPos(event.position().toPoint())

    def mousePressEvent(self, event):
        index = self.mouseEventIndex(event)
        if not self.tab_movable.get(index, self.isMovable()):
            QtWidgets.QTabBar.setMovable(self, False)  # disable dragging until they release the mouse
        return QtWidgets.QTabBar.mousePressEvent(self, event)

    def mouseReleaseEvent(self, event):
        if self.isMovable():
            # Restore this in case it was temporarily disabled by mousePressEvent
            QtWidgets.QTabBar.setMovable(self, True)
        return QtWidgets.QTabBar.mouseReleaseEvent(self, event)

    def tabLayoutChange(self):
        total_height = 0
        for index in range(self.count()):
            tabRect = self.tabRect(index)
            total_height += tabRect.height()
        if total_height > self.parent().height():
            # Don't paint over the top of the scroll buttons:
            scroll_buttons_area_height = 2*max(self.style().pixelMetric(QtWidgets.QStyle.PM_TabBarScrollButtonWidth),
                                               self.style().pixelMetric(QtWidgets.QStyle.PM_LayoutHorizontalSpacing))
            self.paint_clip = self.width(), self.parent().height() - scroll_buttons_area_height
        else:
            self.paint_clip = None

    def paintEvent(self, event):
        painter = QtWidgets.QStylePainter(self)
        if self.paint_clip is not None:
            painter.setClipRect(0, 0, *self.paint_clip)

        option = QtWidgets.QStyleOptionTab()
        for index in range(self.count()):
            tabRect = self.tabRect(index)
            self.initStyleOption(option, index)
            painter.drawControl(QtWidgets.QStyle.CE_TabBarTabShape, option)
            text_left = tabRect.left() + 10
            text_right = tabRect.right() - 10
            button = self.tabButton(index, QtWidgets.QTabBar.RightSide)
            if button is not None:
                text_right = button.x() - 5
            if not self.tabIcon(index).isNull():
                icon_rect = QtCore.QRect(QtCore.QPoint(0, 0), self.iconSize())
                icon_rect.moveCenter(tabRect.center())
                if self.tabIconOnRight(index):
                    icon_rect.moveRight(text_right)
                    text_right = icon_rect.left() - 5
                else:
                    icon_rect.moveLeft(text_left)
                    text_left = icon_rect.right() + 6
                self.tabIcon(index).paint(painter, icon_rect)
            textRect = QtCore.QRect(tabRect)
            textRect.setLeft(text_left)
            textRect.setRight(text_right)
            text = painter.fontMetrics().elidedText(
                self.tabText(index), QtCore.Qt.ElideRight, textRect.width()
            )
            painter.drawText(textRect, QtCore.Qt.AlignVCenter, text)
        if self.paint_clip is not None:
            x_clip, y_clip = self.paint_clip
            painter.setClipping(False)
            palette = self.palette()
            mid_color = palette.color(QtGui.QPalette.Mid)
            painter.setPen(mid_color)
            painter.drawLine(0, y_clip, x_clip, y_clip)
        painter.end()


    def tabSizeHint(self, index):
        fontmetrics = QtGui.QFontMetrics(self.font())
        text_size = fontmetrics.size(QtCore.Qt.TextSingleLine, self.tabText(index))
        text_width = text_size.width()
        text_height = text_size.height()
        height = text_height + 15
        height = max(self.minheight, height)
        width = text_width + 15
        if not self.tabIcon(index).isNull():
            width += self.iconSize().width() + 5

        button = self.tabButton(index, QtWidgets.QTabBar.RightSide)
        if button is not None:
            height = max(height, button.height() + 7)
            # Same amount of space around the button horizontally as it has vertically:
            width += button.width() + height - button.height()
        width = max(self.minwidth, width)
        return QtCore.QSize(width, height)

    def setTabButton(self, index, geometry, button):
        if not isinstance(button, TabToolButton):
            raise TypeError('Not a TabToolButton, won\'t paint correctly. Use a TabToolButton')
        result = QtWidgets.QTabBar.setTabButton(self, index, geometry, button)
        button.move(*button.get_correct_position())
        return result


class TabToolButton(QtWidgets.QToolButton):
    def __init__(self, *args, **kwargs):
        QtWidgets.QToolButton.__init__(self, *args, **kwargs)
        self.setFocusPolicy(QtCore.Qt.NoFocus)

    def paintEvent(self, event):
        painter = QtWidgets.QStylePainter(self)
        paint_clip = self.parent().paint_clip
        if paint_clip is not None:
            point = QtCore.QPoint(*paint_clip)
            global_point = self.parent().mapToGlobal(point)
            local_point = self.mapFromGlobal(global_point)
            painter.setClipRect(0, 0, local_point.x(), local_point.y())
        option = QtWidgets.QStyleOptionToolButton()
        self.initStyleOption(option)
        painter.drawComplexControl(QtWidgets.QStyle.CC_ToolButton, option)

    def get_correct_position(self):
        parent = self.parent()
        for index in range(parent.count()):
            if parent.tabButton(index, QtWidgets.QTabBar.RightSide) is self:
                break
        else:
            raise LookupError('Tab not found')
        tabRect = parent.tabRect(index)
        tab_x, tab_y, tab_width, tab_height = tabRect.x(), tabRect.y(), tabRect.width(), tabRect.height()
        size = self.sizeHint()
        width = size.width()
        height = size.height()
        padding = int((tab_height - height) / 2)
        correct_x = tab_x + tab_width - width - padding
        correct_y = tab_y + padding
        return correct_x, correct_y

    def moveEvent(self, event):
        try:
            correct_x, correct_y = self.get_correct_position()
        except LookupError:
            return # Things aren't initialised yet
        if self.x() != correct_x or self.y() != correct_y:
            # Move back! I shall not be moved!
            self.move(correct_x, correct_y)
        return QtWidgets.QToolButton.moveEvent(self, event)


class FingerTabWidget(QtWidgets.QTabWidget):

    """A QTabWidget equivalent which uses our FingerTabBarWidget"""

    def __init__(self, parent, *args):
        QtWidgets.QTabWidget.__init__(self, parent, *args)
        self.setTabBar(FingerTabBarWidget(self))

    def addTab(self, *args, **kwargs):
        closeable = kwargs.pop('closable', False)
        icon_on_right = kwargs.pop('icon_on_right', False)
        index = QtWidgets.QTabWidget.addTab(self, *args, **kwargs)
        self.setTabClosable(index, closeable)
        self.tabBar().setTabIconOnRight(index, icon_on_right)
        return index

    def setTabClosable(self, index, closable):
        right_button = self.tabBar().tabButton(index, QtWidgets.QTabBar.RightSide)
        if closable:
            if not right_button:
                # Make one:
                close_button = TabToolButton(self.parent())
                close_button.setIcon(Icon(':/qtutils/fugue/cross'))
                self.tabBar().setTabButton(index, QtWidgets.QTabBar.RightSide, close_button)
                close_button.clicked.connect(lambda: self._on_close_button_clicked(close_button))
        else:
            if right_button:
                # Get rid of it:
                self.tabBar().setTabButton(index, QtWidgets.QTabBar.RightSide, None)

    def _on_close_button_clicked(self, button):
        for index in range(self.tabBar().count()):
            if self.tabBar().tabButton(index, QtWidgets.QTabBar.RightSide) is button:
                self.tabCloseRequested.emit(index)
                break


class ClickOffFilter(QtCore.QObject):
    """Application event filter that clears the current index and selection of each
    registered ItemView when the mouse is pressed elsewhere in the view's window"""

    _instance = None

    def __init__(self):
        QtCore.QObject.__init__(self)
        self.views = weakref.WeakSet()

    @classmethod
    def register(cls, view):
        if cls._instance is None:
            cls._instance = cls()
            QtWidgets.QApplication.instance().installEventFilter(cls._instance)
        cls._instance.views.add(view)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.MouseButtonPress and isinstance(obj, QtWidgets.QWidget):
            if QT_ENV == 'PyQt5':
                global_pos = event.globalPos()
            else:
                global_pos = event.globalPosition().toPoint()
            # The event filter also receives the event when it propagates to the
            # parents of the widget under the mouse, so we use the widget under the
            # mouse and not obj:
            target = QtWidgets.QApplication.widgetAt(global_pos) or obj
            for view in list(self.views):
                if view.isVisible() and view.is_click_off(target):
                    view.clear_current()
        return False


class ItemView:
    """Mixin for QTableView and QTreeView that emits a custom signal leftClicked(index)
    after a left click on a valid index, and doubleLeftClicked(index) (in addition) on
    double click. Also has modified tab and arrow key behaviour and custom selection
    highlighting. The current index and selection are cleared when the user tabs out
    of the view or clicks elsewhere in its window, including on an empty area of the
    view, except on the widgets passed to set_selection_controls()."""
    leftClicked = Signal(QtCore.QModelIndex)
    doubleLeftClicked = Signal(QtCore.QModelIndex)


    def __init__(self, *args):
        super().__init__(*args)
        self._pressed_index = None
        self._double_click = False
        self._selection_controls = []
        self._clearing = False
        # Whether the last edit was committed while the editor had the keyboard focus,
        # that is, with the keyboard:
        self._keyboard_commit = False
        self._pending_edit = None
        self.setAutoScroll(False)
        set_selection_palette(self)
        ClickOffFilter.register(self)

    def set_selection_controls(self, widgets):
        """Set the widgets that act on the selection, clicks on which do not clear it"""
        self._selection_controls = list(widgets)

    def is_click_off(self, widget):
        """Whether a mouse press on the widget clears the current index and selection"""
        if widget.window() is not self.window():
            return False
        for w in [self] + self._selection_controls:
            if w is widget or w.isAncestorOf(widget):
                return False
        return True

    def clear_current(self):
        """Commit any edit in progress and clear the current index and selection"""
        if not self.currentIndex().isValid() and not self.selectionModel().hasSelection():
            return
        self._clearing = True
        try:
            self.selectionModel().clear()
        finally:
            self._clearing = False

    def edit_after_commit(self, index):
        """Edit the index if the last edit was committed with the Enter key. If that
        edit is still open, edit the index once it is closed."""
        if not self._keyboard_commit:
            return
        if self.state() == QtWidgets.QAbstractItemView.EditingState:
            self._pending_edit = QtCore.QPersistentModelIndex(index)
        else:
            self._keyboard_commit = False
            self.setCurrentIndex(index)
            self.edit(index)

    def commitData(self, editor):
        focus = QtWidgets.QApplication.focusWidget()
        self._keyboard_commit = (
            not self._clearing
            and focus is not None
            and (focus is editor or editor.isAncestorOf(focus))
        )
        super().commitData(editor)

    def closeEditor(self, editor, hint):
        pending_edit, self._pending_edit = self._pending_edit, None
        if hint in [
            QtWidgets.QAbstractItemDelegate.EditNextItem,
            QtWidgets.QAbstractItemDelegate.EditPreviousItem,
            QtWidgets.QAbstractItemDelegate.RevertModelCache,
        ]:
            self._keyboard_commit = False
        super().closeEditor(editor, hint)
        if pending_edit is not None and pending_edit.isValid() and self._keyboard_commit:
            self.edit_after_commit(QtCore.QModelIndex(pending_edit))

    def focusOutEvent(self, event):
        if event.reason() in [QtCore.Qt.TabFocusReason, QtCore.Qt.BacktabFocusReason]:
            self.clear_current()
        return super().focusOutEvent(event)

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.PaletteChange:
            set_selection_palette(self)
        super().changeEvent(event)

    def mouseEventIndex(self, event):
        if QT_ENV == 'PyQt5':
            return self.indexAt(event.pos())
        else:
            # Qt6 returns QPointF instead of QPoint
            return self.indexAt(event.position().toPoint())

    def viewItemOption(self):
        """The style option with which the view paints its items, without the
        properties of any particular item"""
        if QT_ENV == 'PyQt5':
            return self.viewOptions()
        option = QtWidgets.QStyleOptionViewItem()
        self.initViewItemOption(option)
        return option

    def mousePressEvent(self, event):
        self._keyboard_commit = False
        index = self.mouseEventIndex(event)
        if not index.isValid():
            self.clear_current()
        result = super().mousePressEvent(event)
        if event.button() == QtCore.Qt.LeftButton and index.isValid():
            self._pressed_index = index
        return result

    def leaveEvent(self, event):
        result = super().leaveEvent(event)
        self._pressed_index = None
        self._double_click = False
        return result

    def mouseDoubleClickEvent(self, event):
        # Ensure our left click event occurs regardless of whether it is the
        # second click in a double click or not
        result = super().mouseDoubleClickEvent(event)
        index = self.mouseEventIndex(event)
        if event.button() == QtCore.Qt.LeftButton and index.isValid():
            self._pressed_index = self.mouseEventIndex(event)
            self._double_click = True
        return result

    def mouseReleaseEvent(self, event):
        result = super().mouseReleaseEvent(event)
        index = self.mouseEventIndex(event)
        if event.button() == QtCore.Qt.LeftButton and index.isValid() and index == self._pressed_index:
            self.leftClicked.emit(index)
            if self._double_click:
                self.doubleLeftClicked.emit(index)
        self._pressed_index = None
        self._double_click = False
        return result

    def keyPressEvent(self, event):
        self._keyboard_commit = False
        if event.key() in [QtCore.Qt.Key_Space, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return]:
            index = self.currentIndex()
            if index.isValid():
                item = self.model().itemFromIndex(index)
                if item.isEditable():
                    # Space/enter edits editable items:
                    self.edit(index)
                else:
                    # Space/enter on non-editable items simulates a left click:
                    self.leftClicked.emit(index)
        return super().keyPressEvent(event)

    def moveCursor(self, cursor_action, keyboard_modifiers):
        current_index = self.currentIndex()
        current_row, current_column = current_index.row(), current_index.column()
        if cursor_action == QtWidgets.QAbstractItemView.MoveUp:
            return current_index.sibling(current_row - 1, current_column)
        elif cursor_action == QtWidgets.QAbstractItemView.MoveDown:
            return current_index.sibling(current_row + 1, current_column)
        elif cursor_action == QtWidgets.QAbstractItemView.MoveLeft:
            return current_index.sibling(current_row, current_column - 1)
        elif cursor_action == QtWidgets.QAbstractItemView.MoveRight:
            return current_index.sibling(current_row, current_column + 1)
        elif cursor_action == QtWidgets.QAbstractItemView.MovePrevious:
            return current_index.sibling(current_row, current_column - 1)
        elif cursor_action == QtWidgets.QAbstractItemView.MoveNext:
            return current_index.sibling(current_row, current_column + 1)
        else:
            return super().moveCursor(cursor_action, keyboard_modifiers)


class TreeView(ItemView, QtWidgets.QTreeView):
    """Treeview version of our customised ItemView"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # Set columns to their minimum size, disabling resizing. Caller may still
        # configure a specific section to stretch:
        self.header().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        self.setItemDelegate(ItemDelegate(self))


class TableView(ItemView, QtWidgets.QTableView):
    """TableView version of our customised ItemView"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # Set rows and columns to the minimum size, disabling interactive resizing.
        # Caller may still configure a specific column to stretch:
        self.verticalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        self.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        self.horizontalHeader().sectionResized.connect(self.on_column_resized)
        self.setItemDelegate(ItemDelegate(self))
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.horizontalHeader().setHighlightSections(False)

    def on_column_resized(self, col):
        for row in range(self.model().rowCount()):
            self.resizeRowToContents(row)


class AlternatingColorModel(QtGui.QStandardItemModel):
    """Model that gives each cell a background colour made from the palette of the view.
    The colour is the base or alternate base colour of the cell's row, mixed with the
    colour in CELL_COLORS that is named by the cell's ROLE_CELL_COLOR data, if it has
    any. In a QTableView, the colour of a selected cell is also mixed with the highlight
    colour."""

    ROLE_CELL_COLOR = QtCore.Qt.UserRole + 100

    def __init__(self, view):
        QtGui.QStandardItemModel.__init__(self)
        self.view = view
        self.brushes = {}

    def background_brush(self, cell_color, alternate, selected):
        palette = QtWidgets.QApplication.palette(self.view)
        key = palette.cacheKey(), cell_color, alternate, selected
        try:
            return self.brushes[key]
        except KeyError:
            pass
        role = QtGui.QPalette.AlternateBase if alternate else QtGui.QPalette.Base
        color = palette.color(QtGui.QPalette.Disabled, role)
        if cell_color is not None:
            hue, fraction = CELL_COLORS[cell_color]
            color = mix_colors(color, QtGui.QColor(hue), fraction)
        if selected:
            color = mix_colors(color, palette.color(QtGui.QPalette.Highlight), SELECTION_FRACTION)
        brush = QtGui.QBrush(color)
        self.brushes[key] = brush
        return brush

    def data(self, index, role):
        if role == QtCore.Qt.BackgroundRole:
            return self.background_brush(
                QtGui.QStandardItemModel.data(self, index, self.ROLE_CELL_COLOR),
                index.row() % 2,
                isinstance(self.view, QtWidgets.QTableView)
                and index in self.view.selectedIndexes(),
            )
        return QtGui.QStandardItemModel.data(self, index, role)


class Editor(QtWidgets.QTextEdit):
    """Popup editor with word wrapping and automatic resizing."""

    def __init__(self, parent):
        QtWidgets.QTextEdit.__init__(self, parent)
        self.setWordWrapMode(QtGui.QTextOption.WordWrap)
        self.setAcceptRichText(False)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.textChanged.connect(self.update_size)
        self.initial_height = None
        set_selection_palette(self)

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.PaletteChange:
            set_selection_palette(self)
        QtWidgets.QTextEdit.changeEvent(self, event)

    def update_size(self):
        if self.initial_height is not None:
            # Temporarily shrink back to the initial height, just so that the document
            # size below returns the preferred size rather than the current size.
            # QTextDocument doesn't have a sizeHint of minimumSizeHint method, so this
            # is the best we can do to get its minimum size.
            self.setFixedHeight(self.initial_height)
        preferred_height = self.document().size().toSize().height()
        # Do not shrink smaller than the initial height:
        if self.initial_height is not None and preferred_height >= self.initial_height:
            self.setFixedHeight(preferred_height)

    def resizeEvent(self, event):
        result = QtWidgets.QTextEdit.resizeEvent(self, event)
        # Record the initial height after it is first set:
        if self.initial_height is None:
            self.initial_height = self.height()
        return result
        


class ItemDelegate(QtWidgets.QStyledItemDelegate):

    """An item delegate with a larger row height and column width, faint grey vertical
    lines between columns, and a custom editor for handling multi-line data"""
    MIN_ROW_HEIGHT = 22
    EXTRA_ROW_HEIGHT = 6
    EXTRA_COL_WIDTH = 20

    def __init__(self, *args, **kwargs):
        QtWidgets.QStyledItemDelegate.__init__(self, *args, **kwargs)
        self._pen = QtGui.QPen()
        self._pen.setWidth(1)
        self._pen.setColor(QtGui.QColor.fromRgb(128, 128, 128, 64))

    def sizeHint(self, *args):
        size = QtWidgets.QStyledItemDelegate.sizeHint(self, *args)
        if size.height() <= self.MIN_ROW_HEIGHT:
            height = self.MIN_ROW_HEIGHT
        else:
            # Esnure cells with multiple lines of text still have some padding:
            height = size.height() + self.EXTRA_ROW_HEIGHT
        return QtCore.QSize(size.width() + self.EXTRA_COL_WIDTH, height)

    def paint(self, painter, option, index):
        if isinstance(self.parent(), QtWidgets.QTableView):
            # Disable rendering of selection highlight for TableViews, they handle
            # it themselves with the background colour data:
            option.state &= ~(QtWidgets.QStyle.State_Selected)
        QtWidgets.QStyledItemDelegate.paint(self, painter, option, index)
        if index.column() > 0:
            painter.setPen(self._pen)
            painter.drawLine(option.rect.topLeft(), option.rect.bottomLeft())

    def eventFilter(self, obj, event):
        """Filter events before they get to the editor, so that editing is ended when
        the user presses tab, shift-tab or enter (which otherwise would not end editing
        in a QTextEdit)."""
        if event.type() == QtCore.QEvent.KeyPress:
            if event.key() in [QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return]:
                # Allow shift-enter
                if not event.modifiers() & QtCore.Qt.ShiftModifier:
                    self.commitData.emit(obj)
                    self.closeEditor.emit(obj)
                    return True
            elif event.key() == QtCore.Qt.Key_Tab:
                self.commitData.emit(obj)
                self.closeEditor.emit(obj, QtWidgets.QStyledItemDelegate.EditNextItem)
                return True
            elif event.key() == QtCore.Qt.Key_Backtab:
                self.commitData.emit(obj)
                self.closeEditor.emit(obj, QtWidgets.QStyledItemDelegate.EditPreviousItem)
                return True
        return QtWidgets.QStyledItemDelegate.eventFilter(self, obj, event)

    def createEditor(self, parent, option, index):
        return Editor(parent)

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data())
        font = index.data(QtCore.Qt.FontRole)
        if font is None:
            font = QtWidgets.QApplication.instance().font(self.parent())
        editor.setFont(font)
        font_height = QtGui.QFontMetrics(font).height()
        padding = (self.MIN_ROW_HEIGHT - font_height) / 2 - 1
        editor.document().setDocumentMargin(padding)
        editor.selectAll()
        
    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText())


@functools.lru_cache(maxsize=4096)
def comment_ranges(text):
    """The start and end indices of the comments in a Python expression"""
    return find_comments(text)


def comment_color(palette):
    """The text colour, partly transparent, so that comments are grey on any background"""
    color = QtGui.QColor(palette.color(QtGui.QPalette.Text))
    color.setAlpha(140)
    return color


class CommentHighlighter(QtGui.QSyntaxHighlighter):
    """Highlighter that shows the comments in a Python expression in the given colour"""

    def __init__(self, document, color):
        QtGui.QSyntaxHighlighter.__init__(self, document)
        self.color = color

    def highlightBlock(self, text):
        document_text = self.document().toPlainText()
        offset = self.currentBlock().position()
        char_format = QtGui.QTextCharFormat()
        char_format.setForeground(self.color)
        for start, end in comment_ranges(document_text):
            start = max(start - offset, 0)
            end = min(end - offset, len(text))
            if start < end:
                self.setFormat(start, end - start, char_format)
        # An edit to one line can change which text in the following lines is a
        # comment. A block state that differs from its previous value makes Qt
        # highlight the following block too:
        self.setCurrentBlockState(hash(document_text) & 0x7FFFFFFF)


class CommentDelegate(ItemDelegate):
    """An ItemDelegate for Python expressions, which draws comments in grey, both in
    the cell and in the editor"""

    def __init__(self, *args, **kwargs):
        ItemDelegate.__init__(self, *args, **kwargs)
        self._painting = False

    def reserved_width(self, index):
        """Width in pixels at the right of the cell in which no text is drawn"""
        return 0

    def reserved_left_width(self, index):
        """Width in pixels at the left of the text in which no text is drawn"""
        return 0

    def display_text(self, index):
        """The text drawn in the cell"""
        return index.data() or ''

    def text_rect(self, option):
        """The rectangle in which the text of the cell and the reserved widths are, for
        a style option that has been initialised for the cell"""
        widget = option.widget
        style = widget.style() if widget is not None else QtWidgets.QApplication.style()
        rect = style.subElementRect(QtWidgets.QStyle.SE_ItemViewItemText, option, widget)
        margin = style.pixelMetric(QtWidgets.QStyle.PM_FocusFrameHMargin, None, widget) + 1
        return rect.adjusted(margin, 0, -margin, 0)

    def initStyleOption(self, option, index):
        ItemDelegate.initStyleOption(self, option, index)
        if self._painting:
            # We draw the text ourselves
            option.text = ''

    def sizeHint(self, option, index):
        reserved_width = self.reserved_width(index) + self.reserved_left_width(index)
        option = QtWidgets.QStyleOptionViewItem(option)
        option.rect.setWidth(option.rect.width() - reserved_width)
        size = ItemDelegate.sizeHint(self, option, index)
        return QtCore.QSize(size.width() + reserved_width, size.height())

    def paint(self, painter, option, index):
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text_rect = self.text_rect(opt)
        text_rect.adjust(self.reserved_left_width(index), 0, -self.reserved_width(index), 0)
        self._painting = True
        try:
            ItemDelegate.paint(self, painter, option, index)
        finally:
            self._painting = False
        text = self.display_text(index)
        # Table views draw the selection as the background colour, with normal text:
        selected = (opt.state & QtWidgets.QStyle.State_Selected
                    and not isinstance(self.parent(), QtWidgets.QTableView))
        text_role = QtGui.QPalette.HighlightedText if selected else QtGui.QPalette.Text
        layout = QtGui.QTextLayout(text.replace('\n', '\u2028'), opt.font)
        text_option = QtGui.QTextOption()
        if opt.features & QtWidgets.QStyleOptionViewItem.WrapText:
            text_option.setWrapMode(QtGui.QTextOption.WordWrap)
        else:
            text_option.setWrapMode(QtGui.QTextOption.ManualWrap)
        layout.setTextOption(text_option)
        formats = []
        for start, end in comment_ranges(text):
            format_range = QtGui.QTextLayout.FormatRange()
            format_range.start = start
            format_range.length = end - start
            char_format = QtGui.QTextCharFormat()
            char_format.setForeground(comment_color(opt.palette))
            format_range.format = char_format
            formats.append(format_range)
        layout.setFormats(formats)
        height = 0
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(text_rect.width())
            line.setPosition(QtCore.QPointF(0, height))
            height += line.height()
        layout.endLayout()
        # Centred vertically, or aligned to the top if the text does not fit:
        top = text_rect.top() + max(0, (text_rect.height() - height) / 2)
        painter.save()
        painter.setClipRect(text_rect)
        painter.setPen(opt.palette.color(text_role))
        layout.draw(painter, QtCore.QPointF(text_rect.left(), top))
        painter.restore()

    def createEditor(self, parent, option, index):
        editor = ItemDelegate.createEditor(self, parent, option, index)
        editor.highlighter = CommentHighlighter(editor.document(), comment_color(editor.palette()))
        return editor


def expansion_text(expansion):
    """The text of an expansion setting as shown in the globals table"""
    return expansion or EXPANSION_NONE_TEXT


@functools.lru_cache(maxsize=1)
def blank_icon():
    """A transparent icon, which aligns the text of an item with that of items that
    have icons"""
    # A QIcon does not scale a pixmap up, so the pixmap is larger than any icon size:
    pixmap = QtGui.QPixmap(64, 64)
    pixmap.fill(QtCore.Qt.transparent)
    return QtGui.QIcon(pixmap)


def style_expansion_item(item, expansion, custom, palette):
    """Give an item the icon of an expansion setting, and grey italics if the setting
    is automatic rather than chosen by the user"""
    if expansion == 'outer':
        item.setIcon(Icon(':qtutils/custom/outer'))
    elif expansion:
        item.setIcon(Icon(':qtutils/custom/zip'))
    else:
        item.setIcon(blank_icon())
    font = item.font()
    font.setItalic(not custom)
    item.setFont(font)
    if custom:
        item.setData(None, QtCore.Qt.ForegroundRole)
    else:
        item.setForeground(comment_color(palette))


def format_time_ago(t, now):
    """A short description of the time t relative to the time now, both in seconds since
    the epoch, such as '5 min ago' or '12 Oct'"""
    interval = now - t
    if interval < 60:
        return 'just now'
    elif interval < 3600:
        return f'{int(interval // 60)} min ago'
    elif interval < 24 * 3600:
        return f'{int(interval // 3600)} h ago'
    elif interval < 7 * 24 * 3600:
        days = int(interval // (24 * 3600))
        return '1 day ago' if days == 1 else f'{days} days ago'
    elif time.localtime(t).tm_year == time.localtime(now).tm_year:
        return time.strftime('%d %b', time.localtime(t)).lstrip('0')
    else:
        return time.strftime('%d %b %Y', time.localtime(t)).lstrip('0')


class HistoryPopup(QtWidgets.QFrame):
    """A popup list of the previous values of a global, with a box to filter them.
    entries are history entries as returned by runmanager.group_manager.get_history(),
    in the order in which to show them, and current_key is the history key of the
    current value of the global. Each value is listed with the expansion setting that
    the global had when the value was used; the current value is listed with
    current_expansion and current_custom, the global's current setting and whether the
    user chose it. Emits activated(entry) when the user chooses an entry,
    pin_toggled(entry) after the user pins or unpins one, and removed(entry) when the
    user removes one."""

    activated = Signal(object)
    pin_toggled = Signal(object)
    removed = Signal(object)

    COL_PIN = 0
    COL_VALUE = 1
    COL_EXPANSION = 2
    COL_TIME = 3

    ROLE_ENTRY = QtCore.Qt.UserRole + 1

    MAX_VISIBLE_ROWS = 15

    def __init__(self, parent, entries, current_key, font, current_expansion, current_custom):
        QtWidgets.QFrame.__init__(self, parent, QtCore.Qt.Popup)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.current_key = current_key
        now = time.time()

        self.filter_edit = QtWidgets.QLineEdit(self)
        self.filter_edit.setPlaceholderText('Filter')
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.installEventFilter(self)

        self.model = QtGui.QStandardItemModel(self)
        self.view = QtWidgets.QTreeView(self)
        self.view.setModel(self.model)
        self.view.setHeaderHidden(True)
        self.view.setRootIsDecorated(False)
        self.view.setAllColumnsShowFocus(True)
        self.view.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.view.setFocusPolicy(QtCore.Qt.NoFocus)
        self.view.setItemDelegateForColumn(self.COL_VALUE, CommentDelegate(self.view))
        self.view.clicked.connect(self.on_clicked)
        self.view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.on_context_menu_requested)

        bold_font = QtGui.QFont(font)
        bold_font.setBold(True)
        for entry in entries:
            current = entry['key'] == current_key
            pin_item = QtGui.QStandardItem()
            value_item = QtGui.QStandardItem(entry['text'])
            value_item.setFont(bold_font if current else font)
            value_item.setData(entry, self.ROLE_ENTRY)
            value_item.setToolTip('Current value' if current else 'Click to use this value')
            if current:
                expansion_item = self.make_expansion_item(current_expansion, current_custom, current)
            else:
                expansion_item = self.make_expansion_item(*history_entry_expansion(entry), current)
            time_item = QtGui.QStandardItem(format_time_ago(entry['last_used'], now))
            time_item.setToolTip(
                'Last used ' + time.strftime('%Y-%m-%d %H:%M', time.localtime(entry['last_used']))
            )
            time_item.setForeground(comment_color(self.view.palette()))
            self.model.appendRow([pin_item, value_item, expansion_item, time_item])
            self.update_pin_item(pin_item, entry)
        if not entries:
            item = QtGui.QStandardItem('No shots have been compiled with this global')
            item.setFlags(QtCore.Qt.NoItemFlags)
            self.model.appendRow(
                [QtGui.QStandardItem(), item, QtGui.QStandardItem(), QtGui.QStandardItem()]
            )

        hint = QtWidgets.QLabel('Enter: use value    Ctrl+P: pin    Delete: remove', self)
        hint_palette = hint.palette()
        hint_palette.setColor(QtGui.QPalette.WindowText, comment_color(hint_palette))
        hint.setPalette(hint_palette)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.view)
        layout.addWidget(hint)

        for column in range(self.model.columnCount()):
            self.view.resizeColumnToContents(column)
        header = self.view.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(self.COL_VALUE, QtWidgets.QHeaderView.Stretch)
        self.filter_edit.textChanged.connect(self.on_filter_changed)
        self.select_first_row()

    def make_expansion_item(self, expansion, custom, current):
        """An item that shows an expansion setting as the globals table does. expansion
        is None if the setting was not recorded with the value."""
        item = QtGui.QStandardItem()
        if expansion is None and not custom:
            item.setToolTip('The expansion setting was not recorded with this value')
            return item
        item.setText(expansion_text(expansion))
        style_expansion_item(item, expansion, custom, self.view.palette())
        if current:
            origin = 'chosen by you' if custom else 'set automatically'
            item.setToolTip(f'The current expansion setting, {origin}')
        elif custom:
            item.setToolTip('Chosen by you when this value was used. Choosing the value restores it.')
        else:
            item.setToolTip('Set automatically when this value was used. '
                            'Runmanager sets it again when you choose the value.')
        return item

    def update_pin_item(self, pin_item, entry):
        icon = Icon(':qtutils/fugue/pin')
        if entry['pinned']:
            pin_item.setIcon(icon)
            pin_item.setToolTip('Pinned. Click to unpin')
        else:
            pin_item.setIcon(in_mode(icon, QtGui.QIcon.Disabled))
            pin_item.setToolTip('Click to pin')

    def entry(self, row):
        return self.model.item(row, self.COL_VALUE).data(self.ROLE_ENTRY)

    def visible_rows(self):
        return [row for row in range(self.model.rowCount())
                if not self.view.isRowHidden(row, QtCore.QModelIndex())
                and self.entry(row) is not None]

    def select_row(self, row):
        self.view.setCurrentIndex(self.model.index(row, self.COL_VALUE))

    def select_first_row(self):
        rows = self.visible_rows()
        # Select the first entry that is not the current value:
        for row in rows:
            if self.entry(row)['key'] != self.current_key:
                self.select_row(row)
                return
        if rows:
            self.select_row(rows[0])

    def current_row(self):
        index = self.view.currentIndex()
        if index.isValid() and index.row() in self.visible_rows():
            return index.row()
        return None

    def on_filter_changed(self, text):
        text = text.lower()
        for row in range(self.model.rowCount()):
            entry = self.entry(row)
            hidden = entry is not None and text not in entry['text'].lower()
            self.view.setRowHidden(row, QtCore.QModelIndex(), hidden)
        self.select_first_row()

    def on_clicked(self, index):
        if self.entry(index.row()) is None:
            return
        if index.column() == self.COL_PIN:
            self.toggle_pin(index.row())
        else:
            self.activate(index.row())

    def activate(self, row):
        entry = self.entry(row)
        self.close()
        if entry['key'] != self.current_key:
            self.activated.emit(entry)

    def toggle_pin(self, row):
        entry = self.entry(row)
        entry['pinned'] = not entry['pinned']
        self.update_pin_item(self.model.item(row, self.COL_PIN), entry)
        self.pin_toggled.emit(entry)

    def remove(self, row):
        entry = self.entry(row)
        self.model.removeRow(row)
        self.removed.emit(entry)
        self.select_first_row()

    def on_context_menu_requested(self, point):
        index = self.view.indexAt(point)
        if not index.isValid() or self.entry(index.row()) is None:
            return
        row = index.row()
        menu = QtWidgets.QMenu(self)
        use_action = menu.addAction('Use this value')
        use_action.setEnabled(self.entry(row)['key'] != self.current_key)
        pin_action = menu.addAction(
            Icon(':qtutils/fugue/pin'), 'Unpin' if self.entry(row)['pinned'] else 'Pin'
        )
        remove_action = menu.addAction(Icon(':qtutils/fugue/cross'), 'Remove from history')
        action = menu.exec(self.view.viewport().mapToGlobal(point))
        if action is use_action:
            self.activate(row)
        elif action is pin_action:
            self.toggle_pin(row)
        elif action is remove_action:
            self.remove(row)

    def eventFilter(self, obj, event):
        """Keys pressed in the filter box move the selection and act on the selected
        entry"""
        if obj is self.filter_edit and event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            rows = self.visible_rows()
            row = self.current_row()
            if key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down) and rows:
                if row is None:
                    position = 0
                elif key == QtCore.Qt.Key_Up:
                    position = max(rows.index(row) - 1, 0)
                else:
                    position = min(rows.index(row) + 1, len(rows) - 1)
                self.select_row(rows[position])
                return True
            elif key in (QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return):
                if row is not None:
                    self.activate(row)
                return True
            elif key == QtCore.Qt.Key_P and event.modifiers() & QtCore.Qt.ControlModifier:
                if row is not None:
                    self.toggle_pin(row)
                return True
            elif key == QtCore.Qt.Key_Delete and not self.filter_edit.text():
                if row is not None:
                    self.remove(row)
                return True
        return QtWidgets.QFrame.eventFilter(self, obj, event)

    def show_below(self, rect):
        """Show the popup below the given rectangle in global coordinates, or above it if
        there is not enough space below"""
        n_rows = min(max(self.model.rowCount(), 1), self.MAX_VISIBLE_ROWS)
        rows_height = sum(self.view.sizeHintForRow(row) for row in range(n_rows))
        self.view.setFixedHeight(rows_height + 2 * self.view.frameWidth())
        width = sum(self.view.columnWidth(col) for col in range(self.model.columnCount()))
        self.view.setMinimumWidth(width + 2 * self.view.frameWidth())
        self.adjustSize()
        screen = QtWidgets.QApplication.screenAt(rect.center())
        available = screen.availableGeometry() if screen is not None else None
        width = max(self.width(), rect.width())
        if available is not None:
            width = min(width, available.width())
        self.resize(width, self.height())
        position = rect.bottomLeft()
        if available is not None:
            if position.y() + self.height() > available.bottom():
                position.setY(rect.top() - self.height())
            position.setX(min(position.x(), available.right() - width))
        self.move(position)
        self.show()
        self.filter_edit.setFocus()
