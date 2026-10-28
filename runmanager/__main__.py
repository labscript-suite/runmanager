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
"""Runmanager GUI and supporting code
"""

import queue
import os
import sys
import importlib.metadata
import labscript_utils.excepthook
labscript_utils.excepthook.install()

# Associate app windows with OS menu shortcuts:
import desktop_app
desktop_app.set_process_appid('runmanager')

# Splash screen
from labscript_utils.splash import Splash
splash = Splash(os.path.join(os.path.dirname(__file__), 'runmanager.svg'))
splash.show()

splash.update_text('importing standard library modules')
import time
import contextlib
import html
import subprocess
import threading
import logging
import traceback
import signal
from pathlib import Path

splash.update_text('importing matplotlib')
# Evaluation of globals happens in a thread with the pylab module imported.
# Although we don't care about plotting, importing pylab makes Qt calls. We
# can't have that from a non main thread, so we'll just disable matplotlib's
# GUI integration:
import matplotlib
matplotlib.use('Agg')
import numpy as np

from qtutils.qt import QtCore, QtGui, QtWidgets, QT_ENV
from qtutils.qt.QtCore import pyqtSignal as Signal
import qtutils.icons # noqa: F401 (registers the icon resources)
from qtutils.icons import Icon
PYQT_VERSION_STR = importlib.metadata.version(QT_ENV)

splash.update_text('importing labscript suite modules')
from labscript_utils.ls_zprocess import zmq_get, ProcessTree, ZMQServer
from labscript_utils.labconfig import LabConfig, save_appconfig, load_appconfig
from labscript_utils.setup_logging import setup_logging
from labscript_utils.programs import open_file
from labscript_utils import shared_drive
from zprocess import raise_exception_in_thread, port_in_use
import runmanager
import runmanager.remote
from runmanager import compiler
from runmanager import differ
from runmanager import evaluator
from runmanager import expander
from runmanager import tokenizer
from runmanager import group_manager
from runmanager.exceptions import ExpansionError
from runmanager.widgets import (
    FingerTabWidget,
    TableView,
    TreeView,
    TabToolButton,
    AlternatingColorModel,
    engage_button_style_sheet,
    ItemDelegate,
    CommentDelegate,
    HistoryPopup,
    EXPANSION_NONE_TEXT,
    blank_icon,
    expansion_text,
    style_expansion_item,
)

from qtutils import (
    inmain,
    inmain_decorator,
    UiLoader,
    inthread,
    DisconnectContextManager,
    qtlock,
)
from labscript_utils.qtwidgets.outputbox import OutputBox

GLOBAL_MONOSPACE_FONT = "Consolas" if os.name == 'nt' else "Ubuntu Mono"

runmanager_dir = Path(__file__).absolute().parent

process_tree = ProcessTree.instance()

# Set a meaningful name for zprocess.locking's client id:
process_tree.zlock_client.set_process_name('runmanager')


@inmain_decorator()
def error_dialog(message):
    QtWidgets.QMessageBox.warning(app.ui, 'runmanager', message)


@inmain_decorator()
def question_dialog(message):
    reply = QtWidgets.QMessageBox.question(app.ui, 'runmanager', message,
                                       QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
    return (reply == QtWidgets.QMessageBox.Yes)


@contextlib.contextmanager
def nested(*contextmanagers):
    if contextmanagers:
        with contextmanagers[0], nested(*contextmanagers[1:]):
            yield
    else:
        yield


def scroll_view_to_row_if_current(view, item):
    """Checks to see if the item is in the row of the current item. If it is, scrolls
    the treeview/tableview vertically to ensure that row is visible. This is done by
    recording the horizontal scroll position, then using view.scrollTo(), and then
    restoring the horizontal position"""
    horizontal_scrollbar = view.horizontalScrollBar()
    existing_horizontal_position = horizontal_scrollbar.value()
    index = item.index()
    current_row = view.currentIndex().row()
    if index.row() == current_row:
        view.scrollTo(index)
        horizontal_scrollbar.setValue(existing_horizontal_position)


class DropDownButtonDelegate:
    """Mixin for an item delegate that draws a drop-down button at the right of the
    hovered or current cell"""

    BUTTON_WIDTH = 16

    def has_button(self, index):
        return not index.model().data(index, GroupTab.GLOBALS_ROLE_IS_DUMMY_ROW)

    def button_rect(self, rect):
        return QtCore.QRect(rect.right() + 1 - self.BUTTON_WIDTH, rect.top(), self.BUTTON_WIDTH, rect.height())

    def button_contains(self, index, global_pos):
        """Whether the given point in global coordinates is on the button of the cell"""
        view = self.parent()
        pos = view.viewport().mapFromGlobal(global_pos)
        return self.has_button(index) and self.button_rect(view.visualRect(index)).contains(pos)

    def reserved_width(self, index):
        return self.BUTTON_WIDTH if self.has_button(index) else 0

    def paint_button(self, painter, option, index):
        if not self.has_button(index):
            return
        view = self.parent()
        if not (option.state & QtWidgets.QStyle.State_MouseOver or view.currentIndex() == index):
            return
        arrow_option = QtWidgets.QStyleOption()
        arrow_option.initFrom(view)
        arrow_option.rect = self.button_rect(option.rect)
        arrow_option.rect.setHeight(self.MIN_ROW_HEIGHT)
        arrow_option.rect.moveCenter(self.button_rect(option.rect).center())
        view.style().drawPrimitive(QtWidgets.QStyle.PE_IndicatorArrowDown, arrow_option, painter, view)


class ExpansionDelegate(DropDownButtonDelegate, ItemDelegate):
    """Delegate for the expansion column of a group tab. The hovered or current cell has
    a drop-down button. The editor is an editable combo box that offers 'Auto', 'None',
    'outer' and the zip groups in use, and takes a typed zip group name. An empty entry
    means 'Auto'."""

    AUTO = 'Auto'

    def __init__(self, group_tab):
        ItemDelegate.__init__(self, group_tab.ui.tableView_globals)
        self.group_tab = group_tab

    def createEditor(self, parent, option, index):
        combo = QtWidgets.QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        combo.addItem(blank_icon(), self.AUTO)
        combo.addItem(blank_icon(), EXPANSION_NONE_TEXT)
        combo.addItem(Icon(':qtutils/custom/outer'), 'outer')
        for name in app.zip_group_names():
            combo.addItem(Icon(':qtutils/custom/zip'), name)
        # Key presses go to the line edit, so filter them there too:
        combo.lineEdit().installEventFilter(self)
        # Choosing an entry from the list commits it straight away:
        combo.activated.connect(lambda *args: (self.commitData.emit(combo), self.closeEditor.emit(combo)))
        return combo

    def sizeHint(self, option, index):
        size = ItemDelegate.sizeHint(self, option, index)
        return QtCore.QSize(size.width() + self.reserved_width(index), size.height())

    def paint(self, painter, option, index):
        ItemDelegate.paint(self, painter, option, index)
        self.paint_button(painter, option, index)

    def eventFilter(self, obj, event):
        if isinstance(obj, QtWidgets.QLineEdit):
            return ItemDelegate.eventFilter(self, obj.parent(), event)
        return ItemDelegate.eventFilter(self, obj, event)

    def updateEditorGeometry(self, combo, option, index):
        # The column is only as wide as its contents, so the editor extends to the
        # left to the width it needs:
        rect = QtCore.QRect(option.rect)
        rect.setLeft(rect.right() + 1 - max(rect.width(), combo.sizeHint().width()))
        combo.setGeometry(rect)

    def setEditorData(self, combo, index):
        item = self.group_tab.globals_model.itemFromIndex(index)
        text = item.text() if item.data(GroupTab.GLOBALS_ROLE_EXPANSION_CUSTOM) else self.AUTO
        # The current entry must be the one with this text, or the combo box emits
        # activated for that entry when the drop-down list takes the focus:
        index = combo.findText(text)
        if index == -1:
            combo.setEditText(text)
        else:
            combo.setCurrentIndex(index)
        combo.lineEdit().selectAll()

    def setModelData(self, combo, model, index):
        text = combo.currentText().strip()
        item = model.itemFromIndex(index)
        if not text or text.lower() == self.AUTO.lower():
            custom = False
            text = item.text()
        else:
            custom = True
        # Set the setting and whether it is custom together, so that the change
        # handler sees both:
        with self.group_tab.globals_model_item_changed_disconnected:
            item.setData(custom, GroupTab.GLOBALS_ROLE_EXPANSION_CUSTOM)
            item.setText(text)
        self.group_tab.on_globals_model_expansion_changed(item)


class ValueDelegate(DropDownButtonDelegate, CommentDelegate):
    """Delegate for the value column of a group tab. Comments are drawn in grey, and
    the hovered or current cell has a drop-down button that shows the history of the
    global's values."""

    def __init__(self, group_tab):
        CommentDelegate.__init__(self, group_tab.ui.tableView_globals)
        self.group_tab = group_tab

    def has_button(self, index):
        if not DropDownButtonDelegate.has_button(self, index):
            return False
        units_index = index.sibling(index.row(), GroupTab.GLOBALS_COL_UNITS)
        return not index.model().data(units_index, GroupTab.GLOBALS_ROLE_IS_BOOL)

    def paint(self, painter, option, index):
        CommentDelegate.paint(self, painter, option, index)
        self.paint_button(painter, option, index)


class GroupTab:
    GLOBALS_COL_DELETE = 0
    GLOBALS_COL_NAME = 1
    GLOBALS_COL_VALUE = 2
    GLOBALS_COL_UNITS = 3
    GLOBALS_COL_EXPANSION = 4

    GLOBALS_ROLE_IS_DUMMY_ROW = QtCore.Qt.UserRole + 1
    GLOBALS_ROLE_SORT_DATA = QtCore.Qt.UserRole + 2
    GLOBALS_ROLE_PREVIOUS_TEXT = QtCore.Qt.UserRole + 3
    GLOBALS_ROLE_IS_BOOL = QtCore.Qt.UserRole + 4
    GLOBALS_ROLE_EXPANSION_CUSTOM = QtCore.Qt.UserRole + 5
    GLOBALS_ROLE_PREVIOUS_CUSTOM = QtCore.Qt.UserRole + 6

    GLOBALS_DUMMY_ROW_TEXT = '<Click to add global>'

    def __init__(self, tabWidget, globals_file, group_name):

        self.logger = setup_logging('runmanager.groupTab')

        self.tabWidget = tabWidget

        loader = UiLoader()
        loader.registerCustomWidget(TableView)
        self.ui = loader.load(os.path.join(runmanager_dir, 'group.ui'))

        # Add the ui to the parent tabWidget:
        self.tabWidget.addTab(self.ui, group_name, closable=True)

        # Descriptions of the globals of this group that are scanned over, for the tab
        # tooltip:
        self.scanned_descriptions = []
        self.set_file_and_group_name(globals_file, group_name)

        self.globals_model = AlternatingColorModel(view=self.ui.tableView_globals)
        self.globals_model.setHorizontalHeaderLabels(['Delete', 'Name', 'Value', 'Units', 'Expansion'])
        self.globals_model.setSortRole(self.GLOBALS_ROLE_SORT_DATA)

        self.ui.tableView_globals.setModel(self.globals_model)
        self.ui.tableView_globals.setSelectionBehavior(QtWidgets.QTableView.SelectRows)
        self.ui.tableView_globals.setSelectionMode(QtWidgets.QTableView.ExtendedSelection)
        self.ui.tableView_globals.setSortingEnabled(True)
        # Make it so the user can just start typing on an item to edit:
        self.ui.tableView_globals.setEditTriggers(QtWidgets.QTableView.AnyKeyPressed |
                                                  QtWidgets.QTableView.EditKeyPressed)
        self.expansion_delegate = ExpansionDelegate(self)
        self.ui.tableView_globals.setItemDelegateForColumn(self.GLOBALS_COL_EXPANSION, self.expansion_delegate)
        self.value_delegate = ValueDelegate(self)
        self.ui.tableView_globals.setItemDelegateForColumn(self.GLOBALS_COL_VALUE, self.value_delegate)
        # Repaint cells when the mouse moves over them, for the drop-down buttons:
        self.ui.tableView_globals.viewport().setAttribute(QtCore.Qt.WA_Hover)
        # Ensure the clickable region of the delete button doesn't extend forever:
        self.ui.tableView_globals.horizontalHeader().setStretchLastSection(False)
        # Stretch the value column to fill available space:
        self.ui.tableView_globals.horizontalHeader().setSectionResizeMode(
            self.GLOBALS_COL_VALUE, QtWidgets.QHeaderView.Stretch
        )
        # Setup stuff for a custom context menu:
        self.ui.tableView_globals.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        # Make the actions for the context menu:
        self.action_globals_delete_selected = QtWidgets.QAction(
            Icon(':qtutils/fugue/minus'), 'Delete selected global(s)',  self.ui)
        self.action_globals_set_selected_true = QtWidgets.QAction(
            Icon(':qtutils/fugue/ui-check-box'), 'Set selected Booleans True',  self.ui)
        self.action_globals_set_selected_false = QtWidgets.QAction(
            Icon(':qtutils/fugue/ui-check-box-uncheck'), 'Set selected Booleans False',  self.ui)
        self.action_globals_reset_expansion = QtWidgets.QAction(
            Icon(':qtutils/fugue/arrow-circle'), 'Reset expansion of selected global(s) to Auto', self.ui)
        self.action_globals_previous_value = QtWidgets.QAction(
            Icon(':qtutils/fugue/arrow-circle-double'), 'Switch selected global(s) to previous value', self.ui)
        self.action_globals_value_history = QtWidgets.QAction(
            Icon(':qtutils/fugue/clock-history'), 'Value history...', self.ui)
        self.action_globals_value_history.setShortcuts(
            [QtGui.QKeySequence('Alt+Down'), QtGui.QKeySequence('F4')])
        self.action_globals_value_history.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
        self.ui.tableView_globals.addAction(self.action_globals_value_history)

        self.connect_signals()

        # Populate the model with globals from the h5 file:
        self.populate_model()
        self.logger.info('Initial population of %s', self.group_name)
        # Set sensible column widths:
        for col in range(self.globals_model.columnCount()):
            if col != self.GLOBALS_COL_VALUE:
                self.ui.tableView_globals.resizeColumnToContents(col)
        if self.ui.tableView_globals.columnWidth(self.GLOBALS_COL_NAME) < 200:
            self.ui.tableView_globals.setColumnWidth(self.GLOBALS_COL_NAME, 200)
        if self.ui.tableView_globals.columnWidth(self.GLOBALS_COL_VALUE) < 200:
            self.ui.tableView_globals.setColumnWidth(self.GLOBALS_COL_VALUE, 200)
        if self.ui.tableView_globals.columnWidth(self.GLOBALS_COL_UNITS) < 100:
            self.ui.tableView_globals.setColumnWidth(self.GLOBALS_COL_UNITS, 100)
        if self.ui.tableView_globals.columnWidth(self.GLOBALS_COL_EXPANSION) < 100:
            self.ui.tableView_globals.setColumnWidth(self.GLOBALS_COL_EXPANSION, 100)
        self.ui.tableView_globals.resizeColumnToContents(self.GLOBALS_COL_DELETE)

        # Error state of tab
        self.tab_contains_errors = False

    def connect_signals(self):
        self.ui.tableView_globals.leftClicked.connect(self.on_tableView_globals_leftClicked)
        self.ui.tableView_globals.customContextMenuRequested.connect(self.on_tableView_globals_context_menu_requested)
        self.action_globals_set_selected_true.triggered.connect(
            lambda: self.on_globals_set_selected_bools_triggered('True'))
        self.action_globals_set_selected_false.triggered.connect(
            lambda: self.on_globals_set_selected_bools_triggered('False'))
        self.action_globals_delete_selected.triggered.connect(self.on_globals_delete_selected_triggered)
        self.action_globals_reset_expansion.triggered.connect(self.on_globals_reset_expansion_triggered)
        self.action_globals_previous_value.triggered.connect(self.on_globals_previous_value_triggered)
        self.action_globals_value_history.triggered.connect(self.on_globals_value_history_triggered)
        self.globals_model.itemChanged.connect(self.on_globals_model_item_changed)
        # A context manager with which we can temporarily disconnect the above connection.
        self.globals_model_item_changed_disconnected = DisconnectContextManager(
            self.globals_model.itemChanged, self.on_globals_model_item_changed)

    def set_file_and_group_name(self, globals_file, group_name):
        """Provided as a separate method so the main app can call it if the
        group gets renamed"""
        self.globals_file = globals_file
        self.group_name = group_name
        self.ui.label_globals_file.setText(globals_file)
        self.ui.label_group_name.setText(group_name)
        index = self.tabWidget.indexOf(self.ui)
        self.tabWidget.setTabText(index, group_name)
        self.update_tab_tooltip()

    def update_tab_tooltip(self):
        tooltip = f'{self.group_name}\n({self.globals_file})'
        if self.scanned_descriptions:
            tooltip += '\n\nScanned over:\n' + '\n'.join(self.scanned_descriptions)
        index = self.tabWidget.indexOf(self.ui)
        if self.tabWidget.tabToolTip(index) != tooltip:
            self.tabWidget.setTabToolTip(index, tooltip)

    def set_tab_icon(self, icon_string):
        index = self.tabWidget.indexOf(self.ui)
        if icon_string is not None:
            icon = Icon(icon_string)
        else:
            icon = QtGui.QIcon()
        if self.tabWidget.tabIcon(index).cacheKey() != icon.cacheKey():
            self.tabWidget.setTabIcon(index, icon)

    def populate_model(self):
        globals = group_manager.get_globals({self.group_name: self.globals_file})[self.group_name]
        custom = group_manager.get_custom_expansions({self.group_name: self.globals_file})[self.group_name]
        for name, (value, units, expansion) in globals.items():
            row = self.make_global_row(name, value, units, expansion, custom[name])
            self.globals_model.appendRow(row)
            value_item = row[self.GLOBALS_COL_VALUE]
            self.check_for_boolean_values(value_item)
            expansion_item = row[self.GLOBALS_COL_EXPANSION]
            self.on_globals_model_expansion_changed(expansion_item)

        # Add the dummy item at the end:
        dummy_delete_item = QtGui.QStandardItem()
        # This lets later code know that this row does not correspond to an
        # actual global:
        dummy_delete_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_delete_item.setFlags(QtCore.Qt.ItemIsEnabled)
        dummy_delete_item.setToolTip('Click to add global')

        dummy_name_item = QtGui.QStandardItem(self.GLOBALS_DUMMY_ROW_TEXT)
        dummy_name_item.setFont(QtGui.QFont(GLOBAL_MONOSPACE_FONT))
        dummy_name_item.setToolTip('Click to add global')
        dummy_name_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_name_item.setData(self.GLOBALS_DUMMY_ROW_TEXT, self.GLOBALS_ROLE_PREVIOUS_TEXT)
        dummy_name_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsEditable)  # Clears the 'selectable' flag

        dummy_value_item = QtGui.QStandardItem()
        dummy_value_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_value_item.setFlags(QtCore.Qt.ItemIsEnabled)
        dummy_value_item.setToolTip('Click to add global')

        dummy_units_item = QtGui.QStandardItem()
        dummy_units_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_units_item.setFlags(QtCore.Qt.ItemIsEnabled)
        dummy_units_item.setToolTip('Click to add global')

        dummy_expansion_item = QtGui.QStandardItem()
        dummy_expansion_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_expansion_item.setFlags(QtCore.Qt.ItemIsEnabled)
        dummy_expansion_item.setToolTip('Click to add global')

        self.globals_model.appendRow(
            [dummy_delete_item, dummy_name_item, dummy_value_item, dummy_units_item, dummy_expansion_item])

        # Sort by name:
        self.ui.tableView_globals.sortByColumn(self.GLOBALS_COL_NAME, QtCore.Qt.AscendingOrder)

    def make_global_row(self, name, value='', units='', expansion='', custom=False):
        self.logger.debug('%s:%s - make global row: %s ', self.globals_file, self.group_name, name)
        # We just set some data here, other stuff is set in
        # self.update_parse_indication after runmanager has a chance to parse
        # everything and get back to us about what that data should be.

        delete_item = QtGui.QStandardItem()
        delete_item.setIcon(Icon(':qtutils/fugue/minus'))
        # Must be set to something so that the dummy row doesn't get sorted first:
        delete_item.setData(False, self.GLOBALS_ROLE_SORT_DATA)
        delete_item.setEditable(False)
        delete_item.setToolTip('Delete global from group.')

        name_item = QtGui.QStandardItem(name)
        name_item.setData(name, self.GLOBALS_ROLE_SORT_DATA)
        name_item.setData(name, self.GLOBALS_ROLE_PREVIOUS_TEXT)
        name_item.setToolTip(name)
        name_item.setFont(QtGui.QFont(GLOBAL_MONOSPACE_FONT))

        value_item = QtGui.QStandardItem(value)
        value_item.setData(value, self.GLOBALS_ROLE_SORT_DATA)
        value_item.setData(str(value), self.GLOBALS_ROLE_PREVIOUS_TEXT)
        value_item.setToolTip('Evaluating...')
        value_item.setFont(QtGui.QFont(GLOBAL_MONOSPACE_FONT))

        units_item = QtGui.QStandardItem(units)
        units_item.setData(units, self.GLOBALS_ROLE_SORT_DATA)
        units_item.setData(units, self.GLOBALS_ROLE_PREVIOUS_TEXT)
        units_item.setData(False, self.GLOBALS_ROLE_IS_BOOL)
        units_item.setToolTip('')

        expansion_item = QtGui.QStandardItem(expansion_text(expansion))
        expansion_item.setData(expansion, self.GLOBALS_ROLE_SORT_DATA)
        expansion_item.setData(expansion, self.GLOBALS_ROLE_PREVIOUS_TEXT)
        expansion_item.setData(bool(custom), self.GLOBALS_ROLE_EXPANSION_CUSTOM)
        expansion_item.setData(bool(custom), self.GLOBALS_ROLE_PREVIOUS_CUSTOM)
        expansion_item.setToolTip('')

        row = [delete_item, name_item, value_item, units_item, expansion_item]
        return row

    def on_tableView_globals_leftClicked(self, index):
        if qapplication.keyboardModifiers() != QtCore.Qt.NoModifier:
            # Only handle mouseclicks with no keyboard modifiers.
            return
        item = self.globals_model.itemFromIndex(index)
        # The 'name' item in the same row:
        name_index = index.sibling(index.row(), self.GLOBALS_COL_NAME)
        name_item = self.globals_model.itemFromIndex(name_index)
        global_name = name_item.text()
        if item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
            # They clicked on an 'add new global' row. Enter editing mode on
            # the name item so they can enter a name for the new global:
            self.ui.tableView_globals.setCurrentIndex(name_index)
            self.ui.tableView_globals.edit(name_index)
        elif item.data(self.GLOBALS_ROLE_IS_BOOL):
            # It's a bool indicator. Toggle it
            value_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_VALUE)
            if value_item.text() == 'True':
                value_item.setText('False')
            elif value_item.text() == 'False':
                value_item.setText('True')
            else:
                raise AssertionError('expected boolean value')
        elif item.column() == self.GLOBALS_COL_DELETE:
            # They clicked a delete button.
            self.delete_global(global_name)
        elif item.column() == self.GLOBALS_COL_VALUE and self.value_delegate.button_contains(
                index, QtGui.QCursor.pos()):
            self.ui.tableView_globals.setCurrentIndex(index)
            self.show_value_history(global_name)
        elif item.column() == self.GLOBALS_COL_EXPANSION and self.expansion_delegate.button_contains(
                index, QtGui.QCursor.pos()):
            self.ui.tableView_globals.setCurrentIndex(index)
            self.ui.tableView_globals.edit(index)
            self.ui.tableView_globals.indexWidget(index).showPopup()
        elif not item.data(self.GLOBALS_ROLE_IS_BOOL):
            # Edit whatever it is:
            if (self.ui.tableView_globals.currentIndex() != index
                    or self.ui.tableView_globals.state() != QtWidgets.QTreeView.EditingState):
                self.ui.tableView_globals.setCurrentIndex(index)
                self.ui.tableView_globals.edit(index)

    def on_globals_model_item_changed(self, item):
        if item.column() == self.GLOBALS_COL_NAME:
            self.on_globals_model_name_changed(item)
        elif item.column() == self.GLOBALS_COL_VALUE:
            self.on_globals_model_value_changed(item)
        elif item.column() == self.GLOBALS_COL_UNITS:
            self.on_globals_model_units_changed(item)
        elif item.column() == self.GLOBALS_COL_EXPANSION:
            self.on_globals_model_expansion_changed(item)

    def on_globals_model_name_changed(self, item):
        """Handles global renaming and creation of new globals due to the user
        editing the <click to add global> item"""
        item_text = item.text()
        if item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
            if item_text != self.GLOBALS_DUMMY_ROW_TEXT:
                # The user has made a new global by editing the <click to add
                # global> item
                global_name = item_text
                self.new_global(global_name)
        else:
            # User has renamed a global.
            new_global_name = item_text
            previous_global_name = item.data(self.GLOBALS_ROLE_PREVIOUS_TEXT)
            # Ensure the name actually changed, rather than something else
            # about the item:
            if new_global_name != previous_global_name:
                self.rename_global(previous_global_name, new_global_name)

    def on_globals_model_value_changed(self, item):
        index = item.index()
        new_value = item.text()
        previous_value = item.data(self.GLOBALS_ROLE_PREVIOUS_TEXT)
        name_index = index.sibling(index.row(), self.GLOBALS_COL_NAME)
        name_item = self.globals_model.itemFromIndex(name_index)
        global_name = name_item.text()
        # Ensure the value actually changed, rather than something else about
        # the item:
        if new_value != previous_value:
            self.change_global_value(global_name, previous_value, new_value)

    def on_globals_model_units_changed(self, item):
        index = item.index()
        new_units = item.text()
        previous_units = item.data(self.GLOBALS_ROLE_PREVIOUS_TEXT)
        name_index = index.sibling(index.row(), self.GLOBALS_COL_NAME)
        name_item = self.globals_model.itemFromIndex(name_index)
        global_name = name_item.text()
        # If it's a boolean value, ensure the check state matches the bool state:
        if item.data(self.GLOBALS_ROLE_IS_BOOL):
            value_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_VALUE)
            if value_item.text() == 'True':
                item.setCheckState(QtCore.Qt.Checked)
            elif value_item.text() == 'False':
                item.setCheckState(QtCore.Qt.Unchecked)
            else:
                raise AssertionError('expected boolean value')
        # Ensure the value actually changed, rather than something else about
        # the item:
        if new_units != previous_units:
            self.change_global_units(global_name, previous_units, new_units)

    @classmethod
    def expansion_from_text(cls, text):
        """The expansion setting that the text of an expansion item means"""
        return '' if text == EXPANSION_NONE_TEXT else text

    def on_globals_model_expansion_changed(self, item):
        index = item.index()
        new_expansion = self.expansion_from_text(item.text())
        previous_expansion = item.data(self.GLOBALS_ROLE_PREVIOUS_TEXT)
        custom = bool(item.data(self.GLOBALS_ROLE_EXPANSION_CUSTOM))
        previous_custom = bool(item.data(self.GLOBALS_ROLE_PREVIOUS_CUSTOM))
        name_index = index.sibling(index.row(), self.GLOBALS_COL_NAME)
        name_item = self.globals_model.itemFromIndex(name_index)
        global_name = name_item.text()
        if new_expansion == 'outer':
            effect = ('This global will be interpreted as a list of values, and will be outer '
                      'producted with other lists to form a larger parameter space.')
            reason = 'the value is a list, array or range'
        elif new_expansion:
            effect = ('This global will be interpreted as a list of values, and will be iterated '
                      f"over in lock-step with other globals in the '{new_expansion}' zip group.")
            if new_expansion == global_name:
                reason = 'other globals are derived from it'
            else:
                reason = f"it is derived from a global in the '{new_expansion}' zip group"
        else:
            effect = 'This global will be interpreted as a single value and passed to compilation as-is.'
            reason = 'the value is not a list, array or range'
        if custom:
            origin = "Chosen by you. Choose 'Auto' to let runmanager set the expansion."
        else:
            origin = f'Set automatically because {reason}.'
        # Don't want icon changing to recurse - which happens even if it is
        # the same icon. So disconnect the signal temporarily:
        with self.globals_model_item_changed_disconnected:
            style_expansion_item(item, new_expansion, custom, self.ui.tableView_globals.palette())
            # Rich text, so that the tooltip wraps:
            item.setToolTip(f'<p>{html.escape(effect)}</p><p>{html.escape(origin)}</p>')
        # Ensure the setting actually changed, rather than something else about
        # the item:
        if new_expansion != previous_expansion or custom != previous_custom:
            self.change_global_expansion(global_name, previous_expansion, new_expansion, custom)

    def on_tableView_globals_context_menu_requested(self, point):
        menu = QtWidgets.QMenu(self.ui)
        menu.addAction(self.action_globals_set_selected_true)
        menu.addAction(self.action_globals_set_selected_false)
        menu.addAction(self.action_globals_reset_expansion)
        menu.addAction(self.action_globals_previous_value)
        menu.addAction(self.action_globals_value_history)
        menu.addAction(self.action_globals_delete_selected)
        menu.exec(QtGui.QCursor.pos())

    def on_globals_delete_selected_triggered(self):
        selected_indexes = self.ui.tableView_globals.selectedIndexes()
        selected_items = (self.globals_model.itemFromIndex(index) for index in selected_indexes)
        name_items = [item for item in selected_items if item.column() == self.GLOBALS_COL_NAME]
        # If multiple selected, show 'delete n groups?' message. Otherwise,
        # pass confirm=True to self.delete_global so it can show the regular
        # message.
        confirm_multiple = (len(name_items) > 1)
        if confirm_multiple and not question_dialog(f"Delete {len(name_items)} globals?"):
            return
        for item in name_items:
            global_name = item.text()
            self.delete_global(global_name, confirm=not confirm_multiple)

    def on_globals_set_selected_bools_triggered(self, state):
        selected_indexes = self.ui.tableView_globals.selectedIndexes()
        selected_items = [self.globals_model.itemFromIndex(index) for index in selected_indexes]
        value_items = [item for item in selected_items if item.column() == self.GLOBALS_COL_VALUE]
        units_items = [item for item in selected_items if item.column() == self.GLOBALS_COL_UNITS]
        for value_item, units_item in zip(value_items, units_items):
            if units_item.data(self.GLOBALS_ROLE_IS_BOOL):
                value_item.setText(state)

    def on_globals_reset_expansion_triggered(self):
        selected_indexes = self.ui.tableView_globals.selectedIndexes()
        selected_items = [self.globals_model.itemFromIndex(index) for index in selected_indexes]
        for item in selected_items:
            if item.column() == self.GLOBALS_COL_EXPANSION and not item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
                with self.globals_model_item_changed_disconnected:
                    item.setData(False, self.GLOBALS_ROLE_EXPANSION_CUSTOM)
                self.on_globals_model_expansion_changed(item)

    def on_globals_previous_value_triggered(self):
        selected_indexes = self.ui.tableView_globals.selectedIndexes()
        selected_items = [self.globals_model.itemFromIndex(index) for index in selected_indexes]
        for item in selected_items:
            if item.column() == self.GLOBALS_COL_NAME and not item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
                global_name = item.text()
                value_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_VALUE)
                entries = group_manager.get_history(self.globals_file, self.group_name, global_name)
                entry = group_manager.previous_history_entry(entries, value_item.text())
                if entry is not None:
                    self.set_value_from_history(global_name, entry)

    def on_globals_value_history_triggered(self):
        view = self.ui.tableView_globals
        index = view.currentIndex()
        if not index.isValid():
            return
        index = index.sibling(index.row(), self.GLOBALS_COL_VALUE)
        if not self.value_delegate.has_button(index):
            return
        editor = view.indexWidget(index)
        if editor is not None:
            # Discard the edit in progress:
            self.value_delegate.closeEditor.emit(editor, QtWidgets.QAbstractItemDelegate.NoHint)
        view.setCurrentIndex(index)
        name_item = self.globals_model.itemFromIndex(index.sibling(index.row(), self.GLOBALS_COL_NAME))
        self.show_value_history(name_item.text())

    def show_value_history(self, global_name):
        value_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_VALUE)
        try:
            entries = group_manager.get_history(self.globals_file, self.group_name, global_name)
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(str(e))
            return
        current_value = value_item.text()
        expansion_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_EXPANSION)
        popup = HistoryPopup(
            self.ui,
            group_manager.ordered_history(entries, current_value),
            group_manager.history_key(current_value),
            QtGui.QFont(GLOBAL_MONOSPACE_FONT),
            self.expansion_from_text(expansion_item.text()),
            bool(expansion_item.data(self.GLOBALS_ROLE_EXPANSION_CUSTOM)),
        )
        popup.activated.connect(lambda entry: self.set_value_from_history(global_name, entry))
        popup.pin_toggled.connect(lambda entry: self.set_history_pinned(global_name, entry))
        popup.removed.connect(lambda entry: self.delete_history_entry(global_name, entry))
        view = self.ui.tableView_globals
        rect = view.visualRect(value_item.index())
        rect.moveTopLeft(view.viewport().mapToGlobal(rect.topLeft()))
        popup.show_below(rect)

    def set_value_from_history(self, global_name, entry):
        """Set the value of the global and its expansion setting to those in the history
        entry. An automatic setting is set again by the parse that follows."""
        value_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_VALUE)
        previous_value = value_item.text()
        if entry['text'] != previous_value:
            try:
                self.change_global_value(global_name, previous_value, entry['text'], interactive=False)
            except Exception as e: # noqa: BLE001 send all errors to dialog box
                error_dialog(str(e))
                return
        expansion_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_EXPANSION)
        expansion, custom = group_manager.history_entry_expansion(entry)
        previous_custom = bool(expansion_item.data(self.GLOBALS_ROLE_EXPANSION_CUSTOM))
        previous_expansion = self.expansion_from_text(expansion_item.text())
        if custom != previous_custom or (expansion is not None and expansion != previous_expansion):
            with self.globals_model_item_changed_disconnected:
                expansion_item.setData(custom, self.GLOBALS_ROLE_EXPANSION_CUSTOM)
                if expansion is not None:
                    expansion_item.setText(expansion_text(expansion))
            self.on_globals_model_expansion_changed(expansion_item)

    def set_history_pinned(self, global_name, entry):
        try:
            group_manager.set_history_pinned(
                self.globals_file, self.group_name, global_name, entry['key'], entry['pinned']
            )
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(str(e))

    def delete_history_entry(self, global_name, entry):
        try:
            group_manager.delete_history_entry(self.globals_file, self.group_name, global_name, entry['key'])
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(str(e))

    def close(self):
        # It is up to the main runmanager class to drop references to this
        # instance before or after calling this method, so that after the
        # tabWidget no longer owns our widgets, both the widgets and the
        # instance will be garbage collected.
        index = self.tabWidget.indexOf(self.ui)
        self.tabWidget.removeTab(index)

    def get_global_item_by_name(self, global_name, column, previous_name=None):
        """Returns an item from the row representing a global in the globals model.
        Which item is returned is set by the column argument."""
        possible_name_items = self.globals_model.findItems(global_name, column=self.GLOBALS_COL_NAME)
        if previous_name is not None:
            # Filter by previous name, useful for telling rows apart when a
            # rename is in progress and two rows may temporarily contain the
            # same name (though the rename code with throw an error and revert
            # it).
            possible_name_items = [item for item in possible_name_items
                                   if item.data(self.GLOBALS_ROLE_PREVIOUS_TEXT) == previous_name]
        elif global_name != self.GLOBALS_DUMMY_ROW_TEXT:
            # Don't return the dummy item unless they asked for it explicitly
            # - if a new global is being created, its name might be
            # simultaneously present in its own row and the dummy row too.
            possible_name_items = [item for item in possible_name_items
                                   if not item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW)]
        if len(possible_name_items) > 1:
            raise LookupError('Multiple items found')
        elif not possible_name_items:
            raise LookupError('No item found')
        name_item = possible_name_items[0]
        name_index = name_item.index()
        # Found the name item, get the sibling item for the column requested:
        item_index = name_index.sibling(name_index.row(), column)
        item = self.globals_model.itemFromIndex(item_index)
        return item

    def do_model_sort(self):
        header = self.ui.tableView_globals.horizontalHeader()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        self.ui.tableView_globals.sortByColumn(sort_column, sort_order)

    def new_global(self, global_name):
        self.logger.info('%s:%s - new global: %s', self.globals_file, self.group_name, global_name)
        item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_NAME,
                                            previous_name=self.GLOBALS_DUMMY_ROW_TEXT)
        try:
            group_manager.new_global(self.globals_file, self.group_name, global_name)
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(str(e))
        else:
            # Insert the newly created global into the model:
            global_row = self.make_global_row(global_name)
            last_index = self.globals_model.rowCount()
            # Insert it as the row before the last (dummy) row:
            self.globals_model.insertRow(last_index - 1, global_row)
            self.do_model_sort()
            # Go into edit mode on the 'value' item:
            value_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_VALUE,
                                                      previous_name=global_name)
            value_item_index = value_item.index()
            self.ui.tableView_globals.setCurrentIndex(value_item_index)
            self.ui.tableView_globals.edit(value_item_index)
            self.globals_changed()
        finally:
            # Set the dummy row's text back ready for another group to be created:
            item.setText(self.GLOBALS_DUMMY_ROW_TEXT)

    def rename_global(self, previous_global_name, new_global_name):
        self.logger.info('%s:%s - rename global: %s -> %s',
                    self.globals_file, self.group_name, previous_global_name, new_global_name)
        item = self.get_global_item_by_name(new_global_name, self.GLOBALS_COL_NAME,
                                            previous_name=previous_global_name)
        try:
            group_manager.rename_global(self.globals_file, self.group_name, previous_global_name, new_global_name)
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(str(e))
            # Set the item text back to the old name, since the rename failed:
            item.setText(previous_global_name)
        else:
            item.setData(new_global_name, self.GLOBALS_ROLE_PREVIOUS_TEXT)
            item.setData(new_global_name, self.GLOBALS_ROLE_SORT_DATA)
            self.do_model_sort()
            item.setToolTip(new_global_name)
            self.globals_changed()
            value_item = self.get_global_item_by_name(new_global_name, self.GLOBALS_COL_VALUE)
            value = value_item.text()
            if not value and self.ui.tableView_globals.state() != QtWidgets.QAbstractItemView.EditingState:
                # Go into editing the value item automatically if not already in edit mode:
                value_item_index = value_item.index()
                self.ui.tableView_globals.setCurrentIndex(value_item_index)
                self.ui.tableView_globals.edit(value_item_index)
            else:
                # If this changed the sort order, ensure the item is still visible:
                scroll_view_to_row_if_current(self.ui.tableView_globals, item)

    def change_global_value(self, global_name, previous_value, new_value, interactive=True):
        self.logger.info('%s:%s - change global value: %s = %s -> %s', self.globals_file, self.group_name, global_name, previous_value, new_value)
        item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_VALUE)
        if not interactive:
            # Value was not set interactively by the user, it is up to us to set it:
            with self.globals_model_item_changed_disconnected:
                item.setText(new_value)
        previous_cell_color = item.data(AlternatingColorModel.ROLE_CELL_COLOR)
        previous_icon = item.icon()
        item.setData(new_value, self.GLOBALS_ROLE_PREVIOUS_TEXT)
        item.setData(new_value, self.GLOBALS_ROLE_SORT_DATA)
        item.setData(None, AlternatingColorModel.ROLE_CELL_COLOR)
        item.setIcon(Icon(':qtutils/fugue/hourglass'))
        args = global_name, previous_value, new_value, item, previous_cell_color, previous_icon
        if interactive:
            QtCore.QTimer.singleShot(1, lambda: self.complete_change_global_value(*args))
        else:
            self.complete_change_global_value(*args, interactive=False)

    def complete_change_global_value(self, global_name, previous_value, new_value, item, previous_cell_color, previous_icon, interactive=True):
        try:
            group_manager.set_value(self.globals_file, self.group_name, global_name, new_value)
        except Exception as e:
            if interactive:
                error_dialog(str(e))
            # Set the item text back to the old name, since the change failed:
            with self.globals_model_item_changed_disconnected:
                item.setText(previous_value)
                item.setData(previous_value, self.GLOBALS_ROLE_PREVIOUS_TEXT)
                item.setData(previous_value, self.GLOBALS_ROLE_SORT_DATA)
                item.setData(previous_cell_color, AlternatingColorModel.ROLE_CELL_COLOR)
                item.setIcon(previous_icon)
            if not interactive:
                raise
        else:
            self.check_for_boolean_values(item)
            self.do_model_sort()
            item.setToolTip('Evaluating...')
            self.globals_changed()
            if not interactive:
                return
            units_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_UNITS)
            units = units_item.text()
            if not units and self.ui.tableView_globals.state() != QtWidgets.QAbstractItemView.EditingState:
                # Go into editing the units item automatically if not already in edit mode:
                units_item_index = units_item.index()
                self.ui.tableView_globals.setCurrentIndex(units_item_index)
                self.ui.tableView_globals.edit(units_item_index)
            else:
                # If this changed the sort order, ensure the item is still visible:
                scroll_view_to_row_if_current(self.ui.tableView_globals, item)

    def change_global_units(self, global_name, previous_units, new_units):
        self.logger.info('%s:%s - change units: %s = %s -> %s', self.globals_file, self.group_name, global_name, previous_units, new_units)
        item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_UNITS)
        try:
            group_manager.set_units(self.globals_file, self.group_name, global_name, new_units)
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(str(e))
            # Set the item text back to the old units, since the change failed:
            item.setText(previous_units)
        else:
            item.setData(new_units, self.GLOBALS_ROLE_PREVIOUS_TEXT)
            item.setData(new_units, self.GLOBALS_ROLE_SORT_DATA)
            self.do_model_sort()
            # If this changed the sort order, ensure the item is still visible:
            scroll_view_to_row_if_current(self.ui.tableView_globals, item)

    def change_global_expansion(self, global_name, previous_expansion, new_expansion, custom):
        self.logger.info('%s:%s - change expansion: %s = %s -> %s (%s)', self.globals_file, self.group_name,
                         global_name, previous_expansion, new_expansion, "custom" if custom else "automatic")
        item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_EXPANSION)
        try:
            group_manager.set_expansion(self.globals_file, self.group_name, global_name, new_expansion, custom=custom)
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(str(e))
            # Set the item back to the old setting, since the change failed:
            with self.globals_model_item_changed_disconnected:
                item.setData(item.data(self.GLOBALS_ROLE_PREVIOUS_CUSTOM), self.GLOBALS_ROLE_EXPANSION_CUSTOM)
            item.setText(expansion_text(previous_expansion))
        else:
            with self.globals_model_item_changed_disconnected:
                item.setData(new_expansion, self.GLOBALS_ROLE_PREVIOUS_TEXT)
                item.setData(new_expansion, self.GLOBALS_ROLE_SORT_DATA)
                item.setData(custom, self.GLOBALS_ROLE_PREVIOUS_CUSTOM)
            self.do_model_sort()
            self.globals_changed()
            # If this changed the sort order, ensure the item is still visible:
            scroll_view_to_row_if_current(self.ui.tableView_globals, item)

    def check_for_boolean_values(self, item):
        """Checks if the value is 'True' or 'False'. If either, makes the
        units cell checkable, uneditable, and coloured to indicate the state.
        The units cell can then be clicked to toggle the value."""
        index = item.index()
        value = item.text()
        name_index = index.sibling(index.row(), self.GLOBALS_COL_NAME)
        units_index = index.sibling(index.row(), self.GLOBALS_COL_UNITS)
        name_item = self.globals_model.itemFromIndex(name_index)
        units_item = self.globals_model.itemFromIndex(units_index)
        global_name = name_item.text()
        self.logger.debug('%s:%s - check for boolean values: %s', self.globals_file, self.group_name, global_name)
        if value == 'True':
            units_item.setData(True, self.GLOBALS_ROLE_IS_BOOL)
            units_item.setText('Bool')
            units_item.setData('!1', self.GLOBALS_ROLE_SORT_DATA)
            units_item.setEditable(False)
            units_item.setCheckState(QtCore.Qt.Checked)
            units_item.setData('true', AlternatingColorModel.ROLE_CELL_COLOR)
        elif value == 'False':
            units_item.setData(True, self.GLOBALS_ROLE_IS_BOOL)
            units_item.setText('Bool')
            units_item.setData('!0', self.GLOBALS_ROLE_SORT_DATA)
            units_item.setEditable(False)
            units_item.setCheckState(QtCore.Qt.Unchecked)
            units_item.setData('false', AlternatingColorModel.ROLE_CELL_COLOR)
        else:
            was_bool = units_item.data(self.GLOBALS_ROLE_IS_BOOL)
            units_item.setData(False, self.GLOBALS_ROLE_IS_BOOL)
            units_item.setEditable(True)
            # Checkbox still visible unless we do the following:
            units_item.setData(None, QtCore.Qt.CheckStateRole)
            units_item.setData(None, AlternatingColorModel.ROLE_CELL_COLOR)
            if was_bool:
                # If the item was a bool and now isn't, clear the
                # units and go into editing so the user can enter a
                # new units string:
                units_item.setText('')
                self.ui.tableView_globals.setCurrentIndex(units_item.index())
                self.ui.tableView_globals.edit(units_item.index())

    def globals_changed(self):
        """Called whenever something about a global has changed. call
        app.globals_changed to inform the main application that it needs to
        parse globals again. self.update_parse_indication will be called by
        the main app when parsing is done, and will set the colours and
        tooltips appropriately"""
        # Tell the main app about it:
        app.globals_changed()

    def delete_global(self, global_name, confirm=True):
        self.logger.info('%s:%s - delete global: %s', self.globals_file, self.group_name, global_name)
        if confirm and not question_dialog(f"Delete the global '{global_name}'?"):
            return
        group_manager.delete_global(self.globals_file, self.group_name, global_name)
        # Find the entry for this global in self.globals_model and remove it:
        name_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_NAME)
        self.globals_model.removeRow(name_item.row())
        self.globals_changed()

    def update_parse_indication(
        self, active_groups, sequence_globals, evaled_globals, deprecations, custom_expansions, scanned_globals,
        dimensions
    ):
        # Check that we are an active group:
        if self.group_name in active_groups and active_groups[self.group_name] == self.globals_file:
            self.tab_contains_errors = False
            tab_contains_deprecations = False
            # for global_name, value in evaled_globals[self.group_name].items():
            for i in range(self.globals_model.rowCount()):
                name_item = self.globals_model.item(i, self.GLOBALS_COL_NAME)
                if name_item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
                    continue
                value_item = self.globals_model.item(i, self.GLOBALS_COL_VALUE)
                expansion_item = self.globals_model.item(i, self.GLOBALS_COL_EXPANSION)
                # value_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_VALUE)
                # expansion_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_EXPANSION)
                global_name = name_item.text()
                if global_name not in evaled_globals[self.group_name]:
                    # Created or renamed after this parse started; the parse that
                    # follows the change will update it.
                    continue
                value = evaled_globals[self.group_name][global_name]

                _, _, expansion = sequence_globals[self.group_name][global_name]
                custom = bool(custom_expansions[self.group_name][global_name])
                # Temporarily disconnect the item_changed signal on the model
                # so that we can set the expansion setting without triggering
                # another preparse - the parsing has already been done with
                # the new setting.
                expansion_changed = False
                with self.globals_model_item_changed_disconnected:
                    for role in [self.GLOBALS_ROLE_PREVIOUS_TEXT, self.GLOBALS_ROLE_SORT_DATA]:
                        if expansion_item.data(role) != expansion:
                            expansion_item.setData(expansion, role)
                            expansion_changed = True
                    for role in [self.GLOBALS_ROLE_PREVIOUS_CUSTOM, self.GLOBALS_ROLE_EXPANSION_CUSTOM]:
                        if expansion_item.data(role) != custom:
                            expansion_item.setData(custom, role)
                            expansion_changed = True
                    if expansion_item.text() != expansion_text(expansion):
                        expansion_item.setText(expansion_text(expansion))
                        expansion_changed = True
                if expansion_changed:
                    # Update the icon, tooltip and text style. This is not detected as
                    # a change to the setting, because the previous roles match.
                    self.on_globals_model_expansion_changed(expansion_item)
                if isinstance(value, Exception):
                    value_item.setData('error', AlternatingColorModel.ROLE_CELL_COLOR)
                    value_item.setIcon(Icon(':qtutils/fugue/exclamation'))
                    tooltip = f'{value.__class__.__name__}: {value!s}'
                    self.tab_contains_errors = True
                elif global_name in deprecations:
                    value_item.setData('deprecated', AlternatingColorModel.ROLE_CELL_COLOR)
                    value_item.setIcon(Icon(':qtutils/fugue/exclamation-diamond'))
                    # Rich text, so that the tooltip wraps:
                    tooltip = (
                        f'<p style="white-space:pre">{html.escape(repr(value))}</p>'
                        + f'<p>Warning: {html.escape(deprecations[global_name])}</p>'
                        + f'<p>{html.escape(evaluator.PYLAB_NAMESPACE_NOTE)}</p>'
                    )
                    tab_contains_deprecations = True
                else:
                    if value_item.data(AlternatingColorModel.ROLE_CELL_COLOR) != 'ok':
                        value_item.setData('ok', AlternatingColorModel.ROLE_CELL_COLOR)
                    if not value_item.icon().isNull():
                        # self.logger.info('clearing icon')
                        value_item.setData(None, QtCore.Qt.DecorationRole)
                    tooltip = repr(value)
                if value_item.toolTip() != tooltip:
                    # self.logger.info('tooltip_changed')
                    value_item.setToolTip(tooltip)
            axes = {name: scanned_globals[name] for name in sequence_globals[self.group_name]
                    if name in scanned_globals}
            self.scanned_descriptions = []
            for global_name, axis_name in sorted(axes.items()):
                expansion_type, zip_key = axis_name.split(' ', 1)
                n_values = dimensions[axis_name]
                values = '1 value' if n_values == 1 else f'{n_values} values'
                if expansion_type == 'outer':
                    self.scanned_descriptions.append(f'{global_name} (outer, {values})')
                else:
                    self.scanned_descriptions.append(f"{global_name} (zip group '{zip_key}', {values})")
            self.update_tab_tooltip()
            if self.tab_contains_errors:
                self.set_tab_icon(':qtutils/fugue/exclamation')
            elif tab_contains_deprecations:
                self.set_tab_icon(':qtutils/fugue/exclamation-diamond')
            elif any(axis_name.startswith('outer ') for axis_name in axes.values()):
                self.set_tab_icon(':qtutils/custom/outer')
            elif axes:
                self.set_tab_icon(':qtutils/custom/zip')
            else:
                self.set_tab_icon(None)
        else:
            # Clear everything:
            self.set_tab_icon(None)
            self.scanned_descriptions = []
            self.update_tab_tooltip()
            for row in range(self.globals_model.rowCount()):
                item = self.globals_model.item(row, self.GLOBALS_COL_VALUE)
                if item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
                    continue
                item.setData(None, QtCore.Qt.DecorationRole)
                item.setToolTip('Group inactive')
                item.setData(None, AlternatingColorModel.ROLE_CELL_COLOR)


class RunmanagerMainWindow(QtWidgets.QMainWindow):
    # A signal to show that the window is shown and painted.
    firstPaint = Signal()

    def __init__(self, *args, **kwargs):
        QtWidgets.QMainWindow.__init__(self, *args, **kwargs)
        self._previously_painted = False

    def closeEvent(self, event):
        if app.on_close_event():
            return QtWidgets.QMainWindow.closeEvent(self, event)
        else:
            event.ignore()

    def paintEvent(self, event):
        result = QtWidgets.QMainWindow.paintEvent(self, event)
        if not self._previously_painted:
            self._previously_painted = True
            self.firstPaint.emit()
        return result
    
    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.Type.PaletteChange:
            # Widgets under a style sheet keep their palette until their style sheet
            # is set again, even if it is empty:
            for widget in self.findChildren(QtWidgets.QWidget):
                widget.setStyleSheet(widget.styleSheet())
            self.pushButton_engage.setStyleSheet(
                engage_button_style_sheet(QtWidgets.QApplication.palette(self.pushButton_engage))
            )
        return super().changeEvent(event)


class PoppedOutOutputBoxWindow(QtWidgets.QDialog):

    def closeEvent(self, event):
        app.on_output_popout_button_clicked()


class RunManager:

    # Duration in seconds for which Restart subprocess waits for the compiler to exit
    # before killing it. The compiler exits within batch_compiler.ABORT_TIMEOUT +
    # batch_compiler.KILL_LOCK_TIMEOUT = 65 seconds unless it is unresponsive.
    QUIT_TIMEOUT = 70

    # Constants for the model in the axes tab:
    AXES_COL_NAME = 0
    AXES_COL_LENGTH = 1
    AXES_COL_SHUFFLE = 2
    AXES_ROLE_NAME = QtCore.Qt.UserRole + 1

    # Constants for the model in the groups tab:
    GROUPS_COL_NAME = 0
    GROUPS_COL_ACTIVE = 1
    GROUPS_COL_DELETE = 2
    GROUPS_COL_OPENCLOSE = 3
    GROUPS_ROLE_IS_DUMMY_ROW = QtCore.Qt.UserRole + 1
    GROUPS_ROLE_PREVIOUS_NAME = QtCore.Qt.UserRole + 2
    GROUPS_ROLE_SORT_DATA = QtCore.Qt.UserRole + 3
    # Sort data for the 'Active' column, by check state:
    GROUPS_ACTIVE_SORT_DATA = {
        QtCore.Qt.Unchecked: 0,
        QtCore.Qt.PartiallyChecked: 1,
        QtCore.Qt.Checked: 2,
    }
    GROUPS_ROLE_GROUP_IS_OPEN = QtCore.Qt.UserRole + 4
    GROUPS_DUMMY_ROW_TEXT = '<Click to add group>'

    def __init__(self):
        self.logger = logging.getLogger('runmanager')
        # log dependency versions
        self.logger.info('Python version: %s', sys.version)
        self.logger.info('Platform: %s', sys.platform)
        self.logger.info('Qt environment: %s', QT_ENV)
        self.logger.info('PySide/PyQt version: %s', PYQT_VERSION_STR)
        self.logger.info('Qt version: %s', QtCore.qVersion())
        self.logger.info("qtutils version: %s", importlib.metadata.version('qtutils'))
        self.logger.info("runmanager version: %s", runmanager.__version__)

        splash.update_text('loading graphical interface')
        loader = UiLoader()
        loader.registerCustomWidget(FingerTabWidget)
        loader.registerCustomWidget(TreeView)
        self.ui = loader.load(
            os.path.join(runmanager_dir, 'main.ui'), RunmanagerMainWindow()
        )

        self.ui.label_non_default_folder.setIcon(Icon(':/qtutils/fugue/exclamation-white'))
        self.ui.pushButton_engage.setStyleSheet(
            engage_button_style_sheet(QtWidgets.QApplication.palette(self.ui.pushButton_engage))
        )

        self.output_box = OutputBox(self.ui.verticalLayout_output_tab)

        # Add a 'pop-out' button to the output tab:
        output_tab_index = self.ui.tabWidget.indexOf(self.ui.tab_output)
        self.output_popout_button = TabToolButton(self.ui.tabWidget.parent())
        self.output_popout_button.setIcon(Icon(':/qtutils/fugue/arrow-out'))
        self.output_popout_button.setToolTip('Toggle whether the output box is in a separate window')
        self.ui.tabWidget.tabBar().setTabButton(output_tab_index, QtWidgets.QTabBar.RightSide, self.output_popout_button)
        # Fix the first three tabs in place:
        for index in range(3):
            self.ui.tabWidget.tabBar().setMovable(False, index=index)
        # Whether or not the output box is currently popped out:
        self.output_box_is_popped_out = False
        # The window it will be moved to when popped out:
        self.output_box_window = PoppedOutOutputBoxWindow(self.ui, QtCore.Qt.WindowSystemMenuHint)
        self.output_box_window_verticalLayout = QtWidgets.QVBoxLayout(self.output_box_window)
        self.output_box_window_verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.output_box_window.setWindowTitle('runmanager output')
        self.output_box_window.resize(800, 1000)
        self.setup_config()
        self.setup_axes_tab()
        self.setup_groups_tab()
        self.connect_signals()
        self.logger.info('UI loaded')

        # The last location from which a labscript file was selected, defaults
        # to labscriptlib:
        self.last_opened_labscript_folder = self.exp_config.get('paths', 'labscriptlib')
        # The last location from which a globals file was selected, defaults
        # to experiment_shot_storage:
        self.last_opened_globals_folder = self.exp_config.get('paths', 'experiment_shot_storage')
        # The last file to which the user saved or loaded a configuration:
        self.last_save_config_file = None
        # The last manually selected shot output folder, defaults to
        # experiment_shot_storage:
        self.last_selected_shot_output_folder = self.exp_config.get('paths', 'experiment_shot_storage')
        self.shared_drive_prefix = self.exp_config.get('paths', 'shared_drive')
        self.experiment_shot_storage = self.exp_config.get('paths', 'experiment_shot_storage')
        # Store the currently open groups as {(globals_filename, group_name): GroupTab}
        self.currently_open_groups = {}

        # A thread that will evaluate globals when they change, allowing us to
        # show their values and any errors in the tabs they came from.
        self.preparse_globals_thread = threading.Thread(target=self.preparse_globals_loop)
        self.preparse_globals_thread.daemon = True
        # A Queue for informing the preparser thread when globals have changed, and thus
        # need parsing again. It is a queue rather than a threading.Event() so that
        # callers can call Queue.join() to wait for parsing to complete in a race-free
        # way
        self.preparse_globals_required = queue.Queue()
        self.preparse_globals_thread.start()

        # A flag telling the compilation thread to abort:
        self.compilation_aborted = threading.Event()

        # The prospective number of shots resulting from compilation
        self.n_shots = None

        # Start the loop that allows compilations to be queued up:
        self.compile_queue = queue.Queue()
        self.compile_queue_thread = threading.Thread(target=self.compile_loop)
        self.compile_queue_thread.daemon = True
        self.compile_queue_thread.start()

        splash.update_text('starting compiler subprocess')
        # Start the compiler subprocess:
        self.to_child, self.from_child, self.child = process_tree.subprocess(
            os.path.join(runmanager_dir, 'batch_compiler.py'),
            output_redirection_port=self.output_box.port,
        )
        self.logger.info('compiler subprocess started')

        # Is blank until a labscript file is selected:
        self.previous_default_output_folder = ''

        # Start a thread to monitor the time of day and create new shot output
        # folders for each day:
        inthread(self.rollover_shot_output_folder)
        self.non_default_folder = None

        # The data from the last time we saved the configuration, so we can
        # know if something's changed:
        self.last_save_data = None

        # autoload a config file, if labconfig is set to do so:
        try:
            autoload_config_file = self.exp_config.get('runmanager', 'autoload_config_file')
        except (LabConfig.NoOptionError, LabConfig.NoSectionError):
            self.output_box.output('Ready.\n\n')
        else:
            self.ui.setEnabled(False)
            self.output_box.output(f'Loading default config file {autoload_config_file}...')

            def load_the_config_file():
                try:
                    self.load_configuration(autoload_config_file)
                    self.output_box.output('done.\n')
                except Exception as e: # noqa: BLE001 send all errors to output box
                    self.output_box.output(f'\nCould not load config file: {e.__class__.__name__}: {e!s}\n\n', red=True)
                else:
                    self.output_box.output('Ready.\n\n')
                finally:
                    self.ui.setEnabled(True)
            # Defer this until 50ms after the window has shown,
            # so that the GUI pops up faster in the meantime
            self.ui.firstPaint.connect(lambda: QtCore.QTimer.singleShot(50, load_the_config_file))

        splash.update_text('done')
        self.ui.show()

    def setup_config(self):
        required_config_params = {"DEFAULT": ["apparatus_name"],
                                  "programs": ["text_editor",
                                               "text_editor_arguments",
                                               ],
                                  "ports": ['BLACS', 'runviewer'],
                                  "paths": ["shared_drive",
                                            "experiment_shot_storage",
                                            "labscriptlib",
                                            ],
                                  }
        self.exp_config = LabConfig(required_params = required_config_params)
        self.logger.info('LabConfig loaded')

    def setup_axes_tab(self):
        self.axes_model = QtGui.QStandardItemModel()

        # Setup the model columns and link to the treeview
        name_header_item = QtGui.QStandardItem('Name')
        name_header_item.setToolTip('The name of the global or zip group being iterated over')
        self.axes_model.setHorizontalHeaderItem(self.AXES_COL_NAME, name_header_item)

        length_header_item = QtGui.QStandardItem('Length')
        length_header_item.setToolTip('The number of elements in the axis of the parameter space')
        self.axes_model.setHorizontalHeaderItem(self.AXES_COL_LENGTH, length_header_item)

        shuffle_header_item = QtGui.QStandardItem('Shuffle')
        shuffle_header_item.setToolTip('Whether or not the order of the axis should be randomised')
        shuffle_header_item.setIcon(Icon(':qtutils/fugue/arrow-switch'))
        self.axes_model.setHorizontalHeaderItem(self.AXES_COL_SHUFFLE, shuffle_header_item)

        self.ui.treeView_axes.setModel(self.axes_model)

        # Setup stuff for a custom context menu:
        self.ui.treeView_axes.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        # Make the actions for the context menu:
        self.action_axes_check_selected = QtWidgets.QAction(Icon(':qtutils/fugue/ui-check-box'),
                                                        'Check selected', self.ui)
        self.action_axes_uncheck_selected = QtWidgets.QAction(Icon(':qtutils/fugue/ui-check-box-uncheck'),
                                                          'Uncheck selected', self.ui)
                                                          
        # setup header widths
        self.ui.treeView_axes.header().setStretchLastSection(False)
        self.ui.treeView_axes.header().setSectionResizeMode(self.AXES_COL_NAME, QtWidgets.QHeaderView.Stretch)

    def setup_groups_tab(self):
        self.groups_model = QtGui.QStandardItemModel()
        self.groups_model.setHorizontalHeaderLabels(['File/group name', 'Active', 'Delete', 'Open/Close'])
        self.groups_model.setSortRole(self.GROUPS_ROLE_SORT_DATA)
        self.ui.treeView_groups.setModel(self.groups_model)
        self.ui.treeView_groups.setAnimated(True)  # Pretty
        self.ui.treeView_groups.setSelectionMode(QtWidgets.QTreeView.ExtendedSelection)
        self.ui.treeView_groups.setSortingEnabled(True)
        self.ui.treeView_groups.sortByColumn(self.GROUPS_COL_NAME, QtCore.Qt.AscendingOrder)
        # Set column widths:
        self.ui.treeView_groups.setColumnWidth(self.GROUPS_COL_NAME, 400)
        # Make it so the user can just start typing on an item to edit:
        self.ui.treeView_groups.setEditTriggers(QtWidgets.QTreeView.AnyKeyPressed |
                                                QtWidgets.QTreeView.EditKeyPressed |
                                                QtWidgets.QTreeView.SelectedClicked)
        # Ensure the clickable region of the open/close button doesn't extend forever:
        self.ui.treeView_groups.header().setStretchLastSection(False)
        # Stretch the filpath/groupname column to fill available space:
        self.ui.treeView_groups.header().setSectionResizeMode(
            self.GROUPS_COL_NAME, QtWidgets.QHeaderView.Stretch
        )
        # Shrink columns other than the 'name' column to the size of their headers:
        for column in range(self.groups_model.columnCount()):
            if column != self.GROUPS_COL_NAME:
                self.ui.treeView_groups.resizeColumnToContents(column)

        self.ui.treeView_groups.setTextElideMode(QtCore.Qt.ElideMiddle)
        # Setup stuff for a custom context menu:
        self.ui.treeView_groups.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        # Make the actions for the context menu:
        self.action_groups_set_selection_active = QtWidgets.QAction(
            Icon(':qtutils/fugue/ui-check-box'), 'Set selected group(s) active', self.ui)
        self.action_groups_set_selection_inactive = QtWidgets.QAction(
            Icon(':qtutils/fugue/ui-check-box-uncheck'), 'Set selected group(s) inactive', self.ui)
        self.action_groups_delete_selected = QtWidgets.QAction(
            Icon(':qtutils/fugue/minus'), 'Delete selected group(s)', self.ui)
        self.action_groups_open_selected = QtWidgets.QAction(
            Icon(':/qtutils/fugue/plus'), 'Open selected group(s)', self.ui)
        self.action_groups_close_selected_groups = QtWidgets.QAction(
            Icon(':/qtutils/fugue/cross'), 'Close selected group(s)', self.ui)
        self.action_groups_close_selected_files = QtWidgets.QAction(
            Icon(':/qtutils/fugue/cross'), 'Close selected file(s)', self.ui)

        # A counter for keeping track of the recursion depth of
        # self._groups_model_active_changed(). This is used so that some
        # actions can be taken in response to initial data changes, but not to
        # flow-on changes made by the method itself:
        self.on_groups_model_active_changed_recursion_depth = 0

    def connect_signals(self):
        # The button that pops the output box in and out:
        self.output_popout_button.clicked.connect(self.on_output_popout_button_clicked)

        # The menu items:
        self.ui.actionLoad_configuration.triggered.connect(self.on_load_configuration_triggered)
        self.ui.actionRevert_configuration.triggered.connect(self.on_revert_configuration_triggered)
        self.ui.actionSave_configuration.triggered.connect(self.on_save_configuration_triggered)
        self.ui.actionSave_configuration_as.triggered.connect(self.on_save_configuration_as_triggered)
        self.ui.actionQuit.triggered.connect(self.ui.close)

        # labscript file and folder selection stuff:
        self.ui.toolButton_select_labscript_file.clicked.connect(self.on_select_labscript_file_clicked)
        self.ui.toolButton_select_shot_output_folder.clicked.connect(self.on_select_shot_output_folder_clicked)
        self.ui.toolButton_edit_labscript_file.clicked.connect(self.on_edit_labscript_file_clicked)
        self.ui.toolButton_reset_shot_output_folder.clicked.connect(self.on_reset_shot_output_folder_clicked)
        self.ui.lineEdit_labscript_file.textChanged.connect(self.on_labscript_file_text_changed)
        self.ui.lineEdit_shot_output_folder.textChanged.connect(self.on_shot_output_folder_text_changed)

        # Control buttons; engage, abort, restart subprocess:
        self.ui.pushButton_engage.clicked.connect(self.on_engage_clicked)
        self.ui.pushButton_abort.clicked.connect(self.on_abort_clicked)
        self.ui.pushButton_restart_subprocess.clicked.connect(self.on_restart_subprocess_clicked)
        
        # shuffle master control
        self.ui.pushButton_shuffle.stateChanged.connect(self.on_master_shuffle_clicked)

        # Tab closebutton clicked:
        self.ui.tabWidget.tabCloseRequested.connect(self.on_tabCloseRequested)

        # Axes tab; right click menu, menu actions, reordering
        # self.ui.treeView_axes.customContextMenuRequested.connect(self.on_treeView_axes_context_menu_requested)
        self.action_axes_check_selected.triggered.connect(self.on_axes_check_selected_triggered)
        self.action_axes_uncheck_selected.triggered.connect(self.on_axes_uncheck_selected_triggered)
        self.ui.toolButton_axis_to_top.clicked.connect(self.on_axis_to_top_clicked)
        self.ui.toolButton_axis_up.clicked.connect(self.on_axis_up_clicked)
        self.ui.toolButton_axis_down.clicked.connect(self.on_axis_down_clicked)
        self.ui.toolButton_axis_to_bottom.clicked.connect(self.on_axis_to_bottom_clicked)
        # axes tab item changed handler
        self.axes_model.itemChanged.connect(self.on_axes_item_changed)
        self.axes_model.rowsRemoved.connect(self.update_global_shuffle_state)
        self.axes_model.rowsInserted.connect(self.update_global_shuffle_state)

        # Groups tab; right click menu, menu actions, open globals file, new globals file, diff globals file,
        self.ui.treeView_groups.customContextMenuRequested.connect(self.on_treeView_groups_context_menu_requested)
        self.action_groups_set_selection_active.triggered.connect(
            lambda: self.on_groups_set_selection_active_triggered(QtCore.Qt.Checked))
        self.action_groups_set_selection_inactive.triggered.connect(
            lambda: self.on_groups_set_selection_active_triggered(QtCore.Qt.Unchecked))
        self.action_groups_delete_selected.triggered.connect(self.on_groups_delete_selected_triggered)
        self.action_groups_open_selected.triggered.connect(self.on_groups_open_selected_triggered)
        self.action_groups_close_selected_groups.triggered.connect(self.on_groups_close_selected_groups_triggered)
        self.action_groups_close_selected_files.triggered.connect(self.on_groups_close_selected_files_triggered)

        self.ui.pushButton_open_globals_file.clicked.connect(self.on_open_globals_file_clicked)
        self.ui.pushButton_new_globals_file.clicked.connect(self.on_new_globals_file_clicked)
        self.ui.pushButton_diff_globals_file.clicked.connect(self.on_diff_globals_file_clicked)
        self.ui.treeView_groups.leftClicked.connect(self.on_treeView_groups_leftClicked)
        self.ui.treeView_groups.doubleLeftClicked.connect(self.on_treeView_groups_doubleLeftClicked)
        self.groups_model.itemChanged.connect(self.on_groups_model_item_changed)
        # A context manager with which we can temporarily disconnect the above connection.
        self.groups_model_item_changed_disconnected = DisconnectContextManager(
            self.groups_model.itemChanged, self.on_groups_model_item_changed)
        
        # Keyboard shortcuts:
        engage_shortcut = QtGui.QShortcut('F5', self.ui,
            lambda: self.ui.pushButton_engage.clicked.emit(False))
        engage_shortcut.setAutoRepeat(False)
        QtGui.QShortcut('ctrl+W', self.ui, self.close_current_tab)
        QtGui.QShortcut('ctrl+Tab', self.ui, lambda: self.switch_tabs(+1))
        QtGui.QShortcut('ctrl+shift+Tab', self.ui, lambda: self.switch_tabs(-1))
        self.logger.info('Signals connected')

    def on_close_event(self):
        save_data = self.get_save_data()
        if self.last_save_data is not None and save_data != self.last_save_data:
            message = ('Current configuration (which groups are active/open and other GUI state) '
                       f'has changed: save config file \'{self.last_save_config_file}\'?')
            reply = QtWidgets.QMessageBox.question(self.ui, 'Quit runmanager', message,
                                               QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel)
            if reply == QtWidgets.QMessageBox.Cancel:
                return False
            if reply == QtWidgets.QMessageBox.Yes:
                self.save_configuration(self.last_save_config_file)
        self.to_child.put(['quit', None])
        return True

    def close_current_tab(self):
        current_tab_widget = self.ui.tabWidget.currentWidget()
        for (globals_file, group_name), tab in self.currently_open_groups.items():
            if tab.ui is current_tab_widget:
                self.close_group(globals_file, group_name)
                break

    def switch_tabs(self, change):
        current_index = self.ui.tabWidget.currentIndex()
        n_tabs = self.ui.tabWidget.count()
        new_index = (current_index + change) % n_tabs
        self.ui.tabWidget.setCurrentIndex(new_index)

    def on_output_popout_button_clicked(self):
        if self.output_box_is_popped_out:
            self.ui.verticalLayout_output_tab.addWidget(self.output_box.output_textedit)
            self.output_box_window.hide()
            self.output_popout_button.setIcon(Icon(':/qtutils/fugue/arrow-out'))
        else:
            # pop it out
            # self.ui.verticalLayout_output_tab.remove(self.output_box)
            self.output_box_window_verticalLayout.addWidget(self.output_box.output_textedit)
            self.output_popout_button.setIcon(Icon(':/qtutils/fugue/arrow-in'))
            self.output_box_window.show()
        self.output_box_is_popped_out = not self.output_box_is_popped_out

    def on_select_labscript_file_clicked(self, checked):
        labscript_file = QtWidgets.QFileDialog.getOpenFileName(self.ui,
                                                           'Select labscript file',
                                                           self.last_opened_labscript_folder,
                                                           "Python files (*.py)")
        if type(labscript_file) is tuple:
            labscript_file, _ = labscript_file

        if not labscript_file:
            # User cancelled selection
            return
        # Convert to standard platform specific path, otherwise Qt likes forward slashes:
        labscript_file = os.path.abspath(labscript_file)
        if not os.path.isfile(labscript_file):
            error_dialog(f"No such file {labscript_file}.")
            return
        # Save the containing folder for use next time we open the dialog box:
        self.last_opened_labscript_folder = os.path.dirname(labscript_file)
        # Write the file to the lineEdit:
        self.ui.lineEdit_labscript_file.setText(labscript_file)

    def on_edit_labscript_file_clicked(self, checked):
        current_labscript_file = self.ui.lineEdit_labscript_file.text()
        # Ignore if no file selected
        if not current_labscript_file:
            return
        try:
            open_file(current_labscript_file, 'text_editor', self.exp_config)
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(f"Unable to launch text editor. Error was: {e!s}. The text editor is set in the [programs] section of {self.exp_config.config_path}.")

    def on_select_shot_output_folder_clicked(self, checked):
        shot_output_folder = QtWidgets.QFileDialog.getExistingDirectory(self.ui,
                                                                    'Select shot output folder',
                                                                    self.last_selected_shot_output_folder)
        if type(shot_output_folder) is tuple:
            shot_output_folder, _ = shot_output_folder

        if not shot_output_folder:
            # User cancelled selection
            return
        # Convert to standard platform specific path, otherwise Qt likes forward slashes:
        shot_output_folder = os.path.abspath(shot_output_folder)
        # Save the containing folder for use next time we open the dialog box:
        self.last_selected_shot_output_folder = os.path.dirname(shot_output_folder)
        # Write the file to the lineEdit:
        self.ui.lineEdit_shot_output_folder.setText(shot_output_folder)
        # Update our knowledge about whether this is the default output folder or not:
        self.check_output_folder_update()

    def on_reset_shot_output_folder_clicked(self, checked):
        current_default_output_folder = self.get_default_output_folder()
        if current_default_output_folder is None:
            return
        self.ui.lineEdit_shot_output_folder.setText(current_default_output_folder)
        self.check_output_folder_update()

    def on_labscript_file_text_changed(self, text):
        # Blank out the 'edit labscript file' button if no labscript file is
        # selected
        enabled = bool(text)
        self.ui.toolButton_edit_labscript_file.setEnabled(enabled)
        # Blank out the 'select shot output folder' button if no labscript
        # file is selected:
        self.ui.toolButton_select_shot_output_folder.setEnabled(enabled)
        self.ui.lineEdit_labscript_file.setToolTip(text)
        # Check if the output folder needs to be updated:
        self.check_output_folder_update()

    def on_shot_output_folder_text_changed(self, text):
        # Blank out the 'reset default output folder' button if the user is
        # already using the default output folder
        if text == self.get_default_output_folder():
            self.non_default_folder = False
        else:
            self.non_default_folder = True
        self.ui.toolButton_reset_shot_output_folder.setEnabled(self.non_default_folder)
        self.ui.label_non_default_folder.setVisible(self.non_default_folder)
        self.ui.lineEdit_shot_output_folder.setToolTip(text)

    def on_engage_clicked(self):
        self.logger.info('Engage')
        try:
            send_to_BLACS = self.ui.checkBox_run_shots.isChecked()
            send_to_runviewer = self.ui.checkBox_view_shots.isChecked()
            labscript_file = self.ui.lineEdit_labscript_file.text()
            # even though we shuffle on a per global basis, if ALL of the globals are set to shuffle, then we may as well shuffle again. This helps shuffle shots more randomly than just shuffling within each level (because without this, you would still do all shots with the outer most variable the same, etc)
            shuffle = self.ui.pushButton_shuffle.checkState() == QtCore.Qt.Checked
            if not labscript_file:
                raise Exception('Error: No labscript file selected')
            output_folder = self.ui.lineEdit_shot_output_folder.text()
            if not output_folder:
                raise Exception('Error: No output folder selected')
            BLACS_host = self.ui.lineEdit_BLACS_hostname.text()
            self.logger.info('Parsing globals...')
            active_groups = self.get_active_groups()
            # Get ordering of expansion globals
            expansion_order = {}
            for i in range(self.axes_model.rowCount()):
                item = self.axes_model.item(i, self.AXES_COL_NAME)
                shuffle_item = self.axes_model.item(i, self.AXES_COL_SHUFFLE)
                name = item.data(self.AXES_ROLE_NAME)
                expansion_order[name] = {'order':i, 'shuffle':shuffle_item.checkState() == QtCore.Qt.Checked}
            
            try:
                sequenceglobals, shots, _, _, _, deprecations = self.parse_globals(
                    active_groups, expansion_order=expansion_order
                )
            except Exception as e:
                raise Exception(f'Error parsing globals:\n{e!s}\nCompilation aborted.')
            for global_name, message in deprecations.items():
                self.output_box.output(f'Warning: global {global_name}: {message}\n', red=True)
            if deprecations:
                self.output_box.output(f'{evaluator.PYLAB_NAMESPACE_NOTE}\n', red=True)
            # Recorded in the history of each global once a shot compiles:
            history = active_groups, sequenceglobals
            self.logger.info('Making h5 files')
            labscript_file, run_files = self.make_h5_files(
                labscript_file, output_folder, sequenceglobals, shots, shuffle)
            self.ui.pushButton_abort.setEnabled(True)
            self.compile_queue.put([labscript_file, run_files, len(shots), send_to_BLACS, BLACS_host, send_to_runviewer, history])
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            self.output_box.output(f'{e!s}\n\n', red=True)
        self.logger.info('end engage')

    def on_abort_clicked(self):
        self.discard_queued_sequences()
        self.compilation_aborted.set()
        self.to_child.put(['abort', None])

    def on_restart_subprocess_clicked(self):
        """Tell the compiler to quit, and kill it if it has not exited after
        QUIT_TIMEOUT seconds. The compiler interrupts the compilation if one is running,
        and if the compilation has not stopped after batch_compiler.ABORT_TIMEOUT
        seconds, exits when it has no HDF5 file open for writing, or after
        batch_compiler.KILL_LOCK_TIMEOUT seconds. Then start a new compiler."""
        self.ui.pushButton_restart_subprocess.setEnabled(False)
        self.to_child.put(['quit', None])
        self.from_child.put(['done', False])
        self.output_box.output('Asking subprocess to quit...')
        timeout_time = time.time() + self.QUIT_TIMEOUT
        QtCore.QTimer.singleShot(50, lambda: self.check_child_exited(timeout_time))

    def check_child_exited(self, timeout_time):
        self.child.poll()
        if self.child.returncode is None and time.time() < timeout_time:
            QtCore.QTimer.singleShot(50, lambda: self.check_child_exited(timeout_time))
            return
        elif self.child.returncode is None:
            self.child.kill()
            self.output_box.output('Killed\n', red=True)
        else:
            self.output_box.output('done.\n')
        self.output_box.output('Spawning new compiler subprocess...')
        self.to_child, self.from_child, self.child = process_tree.subprocess(
            os.path.join(runmanager_dir, 'batch_compiler.py'),
            output_redirection_port=self.output_box.port,
        )
        self.output_box.output('done.\n')
        self.output_box.output('Ready.\n\n')
        self.ui.pushButton_restart_subprocess.setEnabled(True)

    def on_tabCloseRequested(self, index):
        tab_page = self.ui.tabWidget.widget(index)
        for (globals_file, group_name), group_tab in self.currently_open_groups.items():
            if group_tab.ui is tab_page:
                self.close_group(globals_file, group_name)
                break

    def on_treeView_axes_context_menu_requested(self, point):
        raise NotImplementedError

    def on_axes_check_selected_triggered(self, *args):
        raise NotImplementedError

    def on_axes_uncheck_selected_triggered(self, *args):
        raise NotImplementedError
        
    def on_axis_to_top_clicked(self, checked):
        # Get the selection model from the treeview
        selection_model = self.ui.treeView_axes.selectionModel()    
        # Create a list of select row indices
        selected_row_list = [index.row() for index in sorted(selection_model.selectedRows())]
        # For each row selected
        for i,row in enumerate(selected_row_list):
            # only move the row while it is not element 0, and the row above it is not selected
            # (note that while a row above may have been initially selected, it should by now, be one row higher
            # since we start moving elements of the list upwards starting from the lowest index)
            while row > 0 and (row-1) not in selected_row_list:
                # Remove the selected row
                items = self.axes_model.takeRow(row)
                # Add the selected row into a position one above
                self.axes_model.insertRow(row-1,items)
                # Since it is now a newly inserted row, select it again
                selection_model.select(self.axes_model.indexFromItem(items[0]),QtCore.QItemSelectionModel.SelectCurrent|QtCore.QItemSelectionModel.Rows)
                # reupdate the list of selected indices to reflect this change
                selected_row_list[i] -= 1
                row -= 1
                    
        self.update_axes_indentation()

    def on_axis_up_clicked(self, checked):
        # Get the selection model from the treeview
        selection_model = self.ui.treeView_axes.selectionModel()    
        # Create a list of select row indices
        selected_row_list = [index.row() for index in sorted(selection_model.selectedRows())]
        # For each row selected
        for i,row in enumerate(selected_row_list):
            # only move the row if it is not element 0, and the row above it is not selected
            # (note that while a row above may have been initially selected, it should by now, be one row higher
            # since we start moving elements of the list upwards starting from the lowest index)
            if row > 0 and (row-1) not in selected_row_list:
                # Remove the selected row
                items = self.axes_model.takeRow(row)
                # Add the selected row into a position one above
                self.axes_model.insertRow(row-1,items)
                # Since it is now a newly inserted row, select it again
                selection_model.select(self.axes_model.indexFromItem(items[0]),QtCore.QItemSelectionModel.SelectCurrent|QtCore.QItemSelectionModel.Rows)
                # reupdate the list of selected indices to reflect this change
                selected_row_list[i] -= 1
                
        self.update_axes_indentation()

    def on_axis_down_clicked(self, checked):
        # Get the selection model from the treeview
        selection_model = self.ui.treeView_axes.selectionModel()    
        # Create a list of select row indices
        selected_row_list = [index.row() for index in sorted(selection_model.selectedRows(), reverse=True)]
        # For each row selected
        for i,row in enumerate(selected_row_list):
            # only move the row if it is not the last element, and the row above it is not selected
            # (note that while a row below may have been initially selected, it should by now, be one row lower
            # since we start moving elements of the list upwards starting from the highest index)
            if row < self.axes_model.rowCount()-1 and (row+1) not in selected_row_list:
                # Remove the selected row
                items = self.axes_model.takeRow(row)
                # Add the selected row into a position one above
                self.axes_model.insertRow(row+1,items)
                # Since it is now a newly inserted row, select it again
                selection_model.select(self.axes_model.indexFromItem(items[0]),QtCore.QItemSelectionModel.SelectCurrent|QtCore.QItemSelectionModel.Rows)
                # reupdate the list of selected indices to reflect this change
                selected_row_list[i] += 1
            
        self.update_axes_indentation()

    def on_axis_to_bottom_clicked(self, checked):
        selection_model = self.ui.treeView_axes.selectionModel()    
        # Create a list of select row indices
        selected_row_list = [index.row() for index in sorted(selection_model.selectedRows(), reverse=True)]
        # For each row selected
        for i,row in enumerate(selected_row_list):
            # only move the row while it is not the last element, and the row above it is not selected
            # (note that while a row below may have been initially selected, it should by now, be one row lower
            # since we start moving elements of the list upwards starting from the highest index)
            while row < self.axes_model.rowCount()-1 and (row+1) not in selected_row_list:
                # Remove the selected row
                items = self.axes_model.takeRow(row)
                # Add the selected row into a position one above
                self.axes_model.insertRow(row+1,items)
                # Since it is now a newly inserted row, select it again
                selection_model.select(self.axes_model.indexFromItem(items[0]),QtCore.QItemSelectionModel.SelectCurrent|QtCore.QItemSelectionModel.Rows)
                # reupdate the list of selected indices to reflect this change
                selected_row_list[i] += 1
                row += 1
                
        self.update_axes_indentation()
        
    def on_axes_item_changed(self, item):
        if item.column() == self.AXES_COL_SHUFFLE:
            self.update_global_shuffle_state()
            
    def update_global_shuffle_state(self, *args, **kwargs):
        all_checked = True
        none_checked = True
        for i in range(self.axes_model.rowCount()):
            check_state = self.axes_model.item(i, self.AXES_COL_SHUFFLE).checkState() == QtCore.Qt.Checked
            all_checked = all_checked and check_state
            none_checked = none_checked and not check_state
            
        if not all_checked and not none_checked:
            self.ui.pushButton_shuffle.setTristate(True)
            self.ui.pushButton_shuffle.setCheckState(QtCore.Qt.PartiallyChecked)
        elif none_checked and not all_checked:
            self.ui.pushButton_shuffle.setTristate(False)
            self.ui.pushButton_shuffle.setCheckState(QtCore.Qt.Unchecked)
        elif all_checked and not none_checked:
            self.ui.pushButton_shuffle.setTristate(False)
            self.ui.pushButton_shuffle.setCheckState(QtCore.Qt.Checked)
        else:
            # No axes. Set if partially checked, otherwise else leave it alone:
            if self.ui.pushButton_shuffle.checkState() == QtCore.Qt.PartiallyChecked:
                self.ui.pushButton_shuffle.setTristate(False)
                self.ui.pushButton_shuffle.setCheckState(QtCore.Qt.Checked)


    def on_master_shuffle_clicked(self, state):
        state = QtCore.Qt.CheckState(state)
        if state in [QtCore.Qt.Checked, QtCore.Qt.Unchecked]:
            self.ui.pushButton_shuffle.setTristate(False)
            for i in range(self.axes_model.rowCount()):
                item = self.axes_model.item(i, self.AXES_COL_SHUFFLE)
                if item.checkState() != state:
                    self.axes_model.item(i, self.AXES_COL_SHUFFLE).setCheckState(state)

    def on_treeView_groups_context_menu_requested(self, point):
        menu = QtWidgets.QMenu(self.ui)
        menu.addAction(self.action_groups_set_selection_active)
        menu.addAction(self.action_groups_set_selection_inactive)
        menu.addAction(self.action_groups_delete_selected)
        menu.addAction(self.action_groups_open_selected)
        menu.addAction(self.action_groups_close_selected_groups)
        menu.addAction(self.action_groups_close_selected_files)
        copy_menu = QtWidgets.QMenu('Copy selected group(s) to...', menu)
        copy_menu.setIcon(Icon(':/qtutils/fugue/blue-document-copy'))
        menu.addMenu(copy_menu)
        move_menu = QtWidgets.QMenu('Move selected group(s) to...', menu)
        move_menu.setIcon(Icon(':/qtutils/fugue/blue-document--arrow'))
        menu.addMenu(move_menu)

        # Create a dict of all filepaths -> filenames
        filenames = {}
        for index in range(self.groups_model.rowCount()):
            filepath = self.groups_model.item(index, self.GROUPS_COL_NAME).text()
            filenames[filepath] = filepath.split(os.sep)[-1]

        # Add parent directories to duplicate filenames until there are no more
        # duplicates
        i = 2
        while True:
            all_filenames = list(filenames.values())
            duplicates = [filepath for filepath, filename in filenames.items()
                          if all_filenames.count(filename) > 1]
            if not duplicates:
                break
            for filepath in duplicates:
                filenames[filepath] = os.sep.join(filepath.split(os.sep)[-i:])
            i += 1

        # add all filenames to the copy and move submenu
        for filepath, filename in filenames.items():
            copy_menu.addAction(filename, lambda filepath=filepath: self.on_groups_copy_selected_groups_triggered(filepath, False))
            move_menu.addAction(filename, lambda filepath=filepath: self.on_groups_copy_selected_groups_triggered(filepath, True))

        menu.exec(QtGui.QCursor.pos())

    def on_groups_copy_selected_groups_triggered(self, dest_globals_file=None, delete_source_group=False):
        selected_indexes = self.ui.treeView_groups.selectedIndexes()
        selected_items = (self.groups_model.itemFromIndex(index) for index in selected_indexes)
        name_items = [item for item in selected_items
                      if item.column() == self.GROUPS_COL_NAME
                      and item.parent() is not None]
        for item in name_items:
            source_globals_file = item.parent().text()
            self.copy_group(source_globals_file, item.text(), dest_globals_file, delete_source_group)

    def on_groups_set_selection_active_triggered(self, checked_state):
        selected_indexes = self.ui.treeView_groups.selectedIndexes()
        # Filter to only include the 'active' column:
        selected_items = (self.groups_model.itemFromIndex(index) for index in selected_indexes)
        active_items = (item for item in selected_items
                        if item.column() == self.GROUPS_COL_ACTIVE
                        and item.parent() is not None)
        for item in active_items:
            item.setCheckState(checked_state)

    def on_groups_delete_selected_triggered(self):
        selected_indexes = self.ui.treeView_groups.selectedIndexes()
        selected_items = (self.groups_model.itemFromIndex(index) for index in selected_indexes)
        name_items = [item for item in selected_items
                      if item.column() == self.GROUPS_COL_NAME
                      and item.parent() is not None]
        # If multiple selected, show 'delete n groups?' message. Otherwise,
        # pass confirm=True to self.delete_group so it can show the regular
        # message.
        confirm_multiple = (len(name_items) > 1)
        if confirm_multiple and not question_dialog(f"Delete {len(name_items)} groups?"):
            return
        for item in name_items:
            globals_file = item.parent().text()
            group_name = item.text()
            self.delete_group(globals_file, group_name, confirm=not confirm_multiple)

    def on_groups_open_selected_triggered(self):
        selected_indexes = self.ui.treeView_groups.selectedIndexes()
        selected_items = [self.groups_model.itemFromIndex(index) for index in selected_indexes]
        name_items = [item for item in selected_items
                      if item.column() == self.GROUPS_COL_NAME
                      and item.parent() is not None]

        # Include all grous of selected globals files:
        for item in selected_items:
            if item.parent() is None:
                children = [item.child(i) for i in range(item.rowCount())]
                # Exclude <add new group> item, which is not selectable
                name_items += [child for child in children if child.isSelectable() ]

        for item in name_items:
            globals_file = item.parent().text()
            group_name = item.text()
            if (globals_file, group_name) not in self.currently_open_groups:
                self.open_group(globals_file, group_name, trigger_preparse=False)
        if name_items:
            self.globals_changed()

    def on_groups_close_selected_groups_triggered(self):
        selected_indexes = self.ui.treeView_groups.selectedIndexes()
        selected_items = (self.groups_model.itemFromIndex(index) for index in selected_indexes)
        name_items = [item for item in selected_items
                      if item.column() == self.GROUPS_COL_NAME
                      and item.parent() is not None]
        for item in name_items:
            globals_file = item.parent().text()
            group_name = item.text()
            if (globals_file, group_name) in self.currently_open_groups:
                self.close_group(globals_file, group_name)

    def on_groups_close_selected_files_triggered(self):
        selected_indexes = self.ui.treeView_groups.selectedIndexes()
        selected_items = (self.groups_model.itemFromIndex(index) for index in selected_indexes)
        name_items = [item for item in selected_items
                      if item.column() == self.GROUPS_COL_NAME
                      and item.parent() is None]
        child_openclose_items = [item.child(i, self.GROUPS_COL_OPENCLOSE)
                                 for item in name_items
                                 for i in range(item.rowCount())]
        child_is_open = [child_item.data(self.GROUPS_ROLE_GROUP_IS_OPEN)
                         for child_item in child_openclose_items]
        if (any(child_is_open)
            and not question_dialog(f'Close {len(name_items)} file(s)? This will close {child_is_open.count(True)} currently open group(s).')):
            return
        for item in name_items:
            globals_file = item.text()
            self.close_globals_file(globals_file, confirm=False)

    def on_open_globals_file_clicked(self):
        globals_file = QtWidgets.QFileDialog.getOpenFileName(self.ui,
                                                         'Select globals file',
                                                         self.last_opened_globals_folder,
                                                         "HDF5 files (*.h5)")
        if type(globals_file) is tuple:
            globals_file, _ = globals_file

        if not globals_file:
            # User cancelled selection
            return
        # Convert to standard platform specific path, otherwise Qt likes forward slashes:
        globals_file = os.path.abspath(globals_file)
        if not os.path.isfile(globals_file):
            error_dialog(f"No such file {globals_file}.")
            return
        # Save the containing folder for use next time we open the dialog box:
        self.last_opened_globals_folder = os.path.dirname(globals_file)
        # Open the file:
        self.open_globals_file(globals_file)

    def on_new_globals_file_clicked(self):
        globals_file = QtWidgets.QFileDialog.getSaveFileName(self.ui,
                                                         'Create new globals file',
                                                         self.last_opened_globals_folder,
                                                         "HDF5 files (*.h5)")
        if type(globals_file) is tuple:
            globals_file, _ = globals_file

        if not globals_file:
            # User cancelled
            return
        if not globals_file.lower().endswith('.h5'):
            globals_file += '.h5'
        # Convert to standard platform specific path, otherwise Qt likes
        # forward slashes:
        globals_file = os.path.abspath(globals_file)
        # Save the containing folder for use next time we open the dialog box:
        self.last_opened_globals_folder = os.path.dirname(globals_file)
        # Create the new file and open it:
        group_manager.new_globals_file(globals_file)
        self.open_globals_file(globals_file)

    def on_diff_globals_file_clicked(self):
        globals_file = QtWidgets.QFileDialog.getOpenFileName(self.ui,
                                                         'Select globals file to compare',
                                                         self.last_opened_globals_folder,
                                                         "HDF5 files (*.h5)")
        if type(globals_file) is tuple:
            globals_file, _ = globals_file

        if not globals_file:
            # User cancelled
            return

        # Convert to standard platform specific path, otherwise Qt likes forward slashes:
        globals_file = os.path.abspath(globals_file)

        # Get runmanager's globals
        active_groups = self.get_active_groups()
        if active_groups is None:
            # Invalid group selection
            return

        # Get file's globals groups
        other_groups = group_manager.get_all_groups(globals_file)

        # Display the output tab so the user can see the output:
        self.ui.tabWidget.setCurrentWidget(self.ui.tab_output)
        self.output_box.output(f'Globals diff with:\n{globals_file}\n\n')

        # Do the globals diff
        globals_diff_table = differ.globals_diff_groups(active_groups, other_groups)
        self.output_box.output(globals_diff_table)
        self.output_box.output('Ready.\n\n')

    def on_treeView_groups_leftClicked(self, index):
        """Here we respond to user clicks on the treeview. We do the following:

        - If the user clicks on the <click to add group> dummy row, we go into
          edit mode on it so they can enter the name of the new group they
          want.
        - If the user clicks on the icon to open or close a globals file or a
          group, we call the appropriate open and close methods and update the
          open/close data role on the model.
        - If the user clicks delete on a globals group, we call a delete
          method, which deletes it after confirmation, and closes it if it was
          open.
          """
        if qapplication.keyboardModifiers() != QtCore.Qt.NoModifier:
            # Only handle mouseclicks with no keyboard modifiers.
            return
        item = self.groups_model.itemFromIndex(index)
        # The 'name' item in the same row:
        name_index = index.sibling(index.row(), self.GROUPS_COL_NAME)
        name_item = self.groups_model.itemFromIndex(name_index)
        # The parent item, None if there is no parent:
        parent_item = item.parent()
        # What kind of row did the user click on?
        # A globals file, a group, or a 'click to add group' row?
        if item.data(self.GROUPS_ROLE_IS_DUMMY_ROW):
            # They clicked on an 'add new group' row. Enter editing
            # mode on the name item so they can enter a name for
            # the new group:
            self.ui.treeView_groups.setCurrentIndex(name_index)
            self.ui.treeView_groups.edit(name_index)
        elif item.column() == self.GROUPS_COL_ACTIVE:
            # They clicked on the active column. Toggle the checkbox. We do
            # this manually because setting the item checkable means the model
            # changes before we catch the mouse click. This is a pain because
            # we want the ensuing sorting (if the user is sorting by the
            # enabled column) to keep the the selection. If the user only
            # selected the column by clicking on it, then the sort happens
            # before they selected it, and the resort happens without a visual
            # indication of where the item went, because it never got
            # selected.
            state = item.checkState()
            if state in (QtCore.Qt.Unchecked, QtCore.Qt.PartiallyChecked):
                item.setCheckState(QtCore.Qt.Checked)
            elif state == QtCore.Qt.Checked:
                item.setCheckState(QtCore.Qt.Unchecked)
            else:
                raise AssertionError('Invalid Check state')
            # If this changed the sort order, ensure the item is still visible:
            scroll_view_to_row_if_current(self.ui.treeView_groups, item)
        elif parent_item is None:
            # They clicked on a globals file row.
            globals_file = name_item.text()
            # What column did they click on?
            if item.column() == self.GROUPS_COL_OPENCLOSE:
                # They clicked the close button. Close the file:
                self.close_globals_file(globals_file)
        else:
            # They clicked on a globals group row.
            globals_file = parent_item.text()
            group_name = name_item.text()
            # What column did they click on?
            if item.column() == self.GROUPS_COL_DELETE:
                # They clicked the delete button. Delete the group:
                self.delete_group(globals_file, group_name, confirm=True)
            elif item.column() == self.GROUPS_COL_OPENCLOSE:
                # They clicked the open/close button. Which is it, open or close?
                group_is_open = item.data(self.GROUPS_ROLE_GROUP_IS_OPEN)
                if group_is_open:
                    self.close_group(globals_file, group_name)
                else:
                    self.open_group(globals_file, group_name)

    def on_treeView_groups_doubleLeftClicked(self, index):
        item = self.groups_model.itemFromIndex(index)
        # The parent item, None if there is no parent:
        parent_item = item.parent()
        if item.data(self.GROUPS_ROLE_IS_DUMMY_ROW):
            return
        elif parent_item and item.column() == self.GROUPS_COL_NAME:
            # it's a group name item. What's the group and file name?
            globals_file = parent_item.text()
            group_name = item.text()
            if (globals_file, group_name) not in self.currently_open_groups:
                self.open_group(globals_file, group_name)
            # Focus the tab:
            group_tab = self.currently_open_groups[globals_file, group_name]
            for i in range(self.ui.tabWidget.count()):
                if self.ui.tabWidget.widget(i) is group_tab.ui:
                    self.ui.tabWidget.setCurrentIndex(i)
                    break

    def on_groups_model_item_changed(self, item):
        """This function is for responding to data changes in the model. The
        methods for responding to changes different columns do different
        things. Mostly they make other data changes for model consistency, but
        also group creation and renaming is handled in response to changes to
        the 'name' column. When we change things elsewhere, we prefer to only
        change one thing, and the rest of the changes are triggered here. So
        here we do the following:

        Be careful not to recurse unsafely into this method - changing
        something that itself triggers further changes is fine so long as they
        peter out and don't get stuck in a loop. If recursion needs to be
        stopped, one can disconnect the signal temporarily with the context
        manager self.groups_model_item_changed_disconnected. But use this
        sparingly, otherwise there's the risk that some required data updates
        will be forgotten about and won't happen.
        """
        if item.column() == self.GROUPS_COL_NAME:
            self.on_groups_model_name_changed(item)
        elif item.column() == self.GROUPS_COL_ACTIVE:
            self.on_groups_model_active_changed(item)
        elif item.column() == self.GROUPS_COL_OPENCLOSE:
            self.on_groups_model_openclose_changed(item)

    def on_groups_model_name_changed(self, item):
        """Handles group renaming and creation of new groups due to the user
        editing the <click to add group> item"""
        parent_item = item.parent()
        # File rows are supposed to be uneditable, but just to be sure we have
        # a group row:
        assert parent_item is not None
        if item.data(self.GROUPS_ROLE_IS_DUMMY_ROW):
            item_text = item.text()
            if item_text != self.GROUPS_DUMMY_ROW_TEXT:
                # The user has made a new globals group by editing the <click
                # to add group> item.
                globals_file = parent_item.text()
                group_name = item_text
                self.new_group(globals_file, group_name)
        else:
            # User has renamed a globals group.
            new_group_name = item.text()
            previous_group_name = item.data(self.GROUPS_ROLE_PREVIOUS_NAME)
            # Ensure it truly is a name change, and not something else about
            # the item changing:
            if new_group_name != previous_group_name:
                globals_file = parent_item.text()
                self.rename_group(globals_file, previous_group_name, new_group_name)

    def on_groups_model_active_changed(self, item):
        """Sets the sort data for the item in response to its check state
        changing. Also, if this is the first time this function has been
        called on the stack, that is, the change was initiated externally
        instead of via recursion from this function itself, then set the check
        state of other items for consistency. This entails checking/unchecking
        all group rows in response to the file row's check state changing, or
        changing the file row's check state to reflect the check state of the
        child group rows. That's why we need to keep track of the recursion
        depth - so that those changes we make don't in turn cause further
        changes. But we don't disconnect the on_changed signal altogether,
        because we still want to do the update of the sort data, and anything
        else that might be added in future."""
        self.on_groups_model_active_changed_recursion_depth += 1
        try:
            check_state = item.checkState()
            # Ensure sort data matches active state:
            item.setData(self.GROUPS_ACTIVE_SORT_DATA[check_state], self.GROUPS_ROLE_SORT_DATA)
            if self.on_groups_model_active_changed_recursion_depth > 1:
                # Prevent all below code from running in response to data changes
                # initiated from within this method itself. The code above this
                # check still runs in response to all changes.
                return

            parent_item = item.parent()
            if parent_item is not None:
                # A 'group active' checkbox changed due to external action (not from this method itself).
                # Update the parent file checkbox to reflect the state of its children
                children = [parent_item.child(i, self.GROUPS_COL_ACTIVE) for i in range(parent_item.rowCount())]
                child_states = [child.checkState() for child in children
                                if not child.data(self.GROUPS_ROLE_IS_DUMMY_ROW)]
                parent_active_index = parent_item.index().sibling(parent_item.index().row(), self.GROUPS_COL_ACTIVE)
                parent_active_item = self.groups_model.itemFromIndex(parent_active_index)
                if all(state == QtCore.Qt.Checked for state in child_states):
                    parent_active_item.setCheckState(QtCore.Qt.Checked)
                elif all(state == QtCore.Qt.Unchecked for state in child_states):
                    parent_active_item.setCheckState(QtCore.Qt.Unchecked)
                else:
                    parent_active_item.setCheckState(QtCore.Qt.PartiallyChecked)
            else:
                # A 'file active' checkbox changed due to external action (not from this method itself).
                # Update the check state of all children to match.
                name_index = item.index().sibling(item.index().row(), self.GROUPS_COL_NAME)
                name_item = self.groups_model.itemFromIndex(name_index)
                checkstate = item.checkState()
                children = [name_item.child(i, self.GROUPS_COL_ACTIVE) for i in range(name_item.rowCount())]
                for child in children:
                    if not child.data(self.GROUPS_ROLE_IS_DUMMY_ROW):
                        child.setCheckState(checkstate)
        finally:
            self.on_groups_model_active_changed_recursion_depth -= 1
            if self.on_groups_model_active_changed_recursion_depth == 0:
                self.do_model_sort()
                # Trigger a preparse to occur:
                self.globals_changed()

    def on_groups_model_openclose_changed(self, item):
        """Sets item sort data and icon in response to the open/close state of a group
        changing."""
        parent_item = item.parent()
        # The open/close state of a globals group changed. It is definitely a
        # group, not a file, as the open/close state of a file shouldn't be
        # changing.
        assert parent_item is not None  # Just to be sure.
        # Ensure the sort data matches the open/close state:
        group_is_open = item.data(self.GROUPS_ROLE_GROUP_IS_OPEN)
        item.setData(group_is_open, self.GROUPS_ROLE_SORT_DATA)
        # Set the appropriate icon and tooltip. Changing the icon causes
        # itemChanged to be emitted, even if it the same icon, and even if we
        # were to use the same QIcon instance. So to avoid infinite recursion
        # we temporarily disconnect the signal whilst we set the icons.
        with self.groups_model_item_changed_disconnected:
            if group_is_open:
                item.setIcon(Icon(':qtutils/fugue/cross'))
                item.setToolTip('Close globals group.')
            else:
                item.setIcon(Icon(':qtutils/fugue/plus'))
                item.setToolTip('Load globals group into runmanager.')
            self.do_model_sort()
            # If this changed the sort order, ensure the item is still visible:
            scroll_view_to_row_if_current(self.ui.treeView_groups, item)

    @inmain_decorator()
    def get_default_output_folder(self):
        """Returns what the default output folder would be right now, based on
        the current date and selected labscript file. Returns empty string if
        no labscript file is selected. Does not create the default output
        folder, does not check if it exists."""
        current_labscript_file = self.ui.lineEdit_labscript_file.text()
        if not current_labscript_file:
            return ''
        _, default_output_folder, _ = compiler.new_sequence_details(
            current_labscript_file,
            config=self.exp_config,
            increment_sequence_index=False,
        )
        default_output_folder = os.path.normpath(default_output_folder)
        return default_output_folder

    def rollover_shot_output_folder(self):
        """Runs in a thread, checking every 30 seconds if the default output folder has
        changed, likely because the date has changed, but also possible because another
        instance of runmanager has incremented the sequence index. If the defaulr output
        folder has changed, and if runmanager is configured to use the default output
        folder, sets the folder in which compiled shots will be put. Does not create the
        folder if it does not already exist, this will be done at compile-time."""
        while True:
            time.sleep(30)
            try:
                self.check_output_folder_update()
            except Exception as e:
                # Don't stop the thread.
                self.logger.exception("error checking default output folder: %s", e)

    @inmain_decorator()
    def check_output_folder_update(self):
        """Do a single check of whether the output folder needs updating. This
        is implemented as a separate function to the above loop so that the
        whole check happens at once in the Qt main thread and hence is atomic
        and can't be interfered with by other Qt calls in the program."""
        current_default_output_folder = self.get_default_output_folder()
        if current_default_output_folder is None:
            # No labscript file selected:
            return
        currently_selected_output_folder = self.ui.lineEdit_shot_output_folder.text()
        if current_default_output_folder != self.previous_default_output_folder:
            # It's a new day, or a new labscript file.
            # Is the user using default folders?
            if currently_selected_output_folder == self.previous_default_output_folder:
                # Yes they are. In that case, update to use the new folder:
                self.ui.lineEdit_shot_output_folder.setText(current_default_output_folder)
            self.previous_default_output_folder = current_default_output_folder

    @inmain_decorator()
    def globals_changed(self):
        """Called from either self, a GroupTab, or the RemoteServer to inform runmanager
        that something about globals has changed, and that they need parsing again."""
        self.ui.pushButton_engage.setEnabled(False)
        self.preparse_globals_required.put(None)

    def update_axes_indentation(self):
        for i in range(self.axes_model.rowCount()):
            item = self.axes_model.item(i, self.AXES_COL_NAME)
            text = item.text().lstrip()
            text = '    '*i + text
            item.setText(text)
            
    @inmain_decorator()  # Is called by preparser thread
    def update_axes_tab(self, expansions, dimensions):
        expansion_list = set(expander.axis_names(expansions).values())
                
        # find items to delete
        for i in reversed(range(self.axes_model.rowCount())):
            item = self.axes_model.item(i, self.AXES_COL_NAME)
            name = item.data(self.AXES_ROLE_NAME)
            if name not in expansion_list:
                item = self.axes_model.takeRow(i)
                del item
            else:
                length_item = self.axes_model.item(i, self.AXES_COL_LENGTH)
                if name in dimensions:
                    length_item.setText(f"{dimensions[name]}")
                else:
                    length_item.setText('Unknown')
                
                # remove from expansions list so we don't add it again
                expansion_list.remove(name)
            
        # add new rows
        for expansion_name in expansion_list:
            shuffle = self.ui.pushButton_shuffle.checkState() != QtCore.Qt.Unchecked
            self.add_item_to_axes_model(expansion_name, shuffle, dimensions)
                
        self.update_axes_indentation() 

    def add_item_to_axes_model(self, expansion_name, shuffle, dimensions = None):
        if dimensions is None:
            dimensions = {}
        
        items = []
        
        expansion_type, name = expansion_name.split(None, 1)
        name_item = QtGui.QStandardItem(name)
        name_item.setData(expansion_name, self.AXES_ROLE_NAME)
        if expansion_type == 'outer':
            name_item.setIcon(Icon(':qtutils/custom/outer'))
        else:
            name_item.setIcon(Icon(':qtutils/custom/zip'))
        items.append(name_item)
        
        length = 'Unknown'
        if expansion_name in dimensions:
            length = f"{dimensions[expansion_name]}"
        length_item = QtGui.QStandardItem(length)
        items.append(length_item)
        
        shuffle_item = QtGui.QStandardItem()
        shuffle_item.setCheckable(True)
        shuffle_item.setCheckState(QtCore.Qt.Checked if shuffle else QtCore.Qt.Unchecked)
        
        items.append(shuffle_item)
        
        self.axes_model.appendRow(items)
    
    @inmain_decorator()  # Is called by preparser thread
    def update_tabs_parsing_indication(
        self, active_groups, sequence_globals, evaled_globals, deprecations, custom_expansions, scanned_globals,
        dimensions, n_shots
    ):
        for group_tab in self.currently_open_groups.values():
            group_tab.update_parse_indication(
                active_groups, sequence_globals, evaled_globals, deprecations, custom_expansions, scanned_globals,
                dimensions
            )
        self.ui.pushButton_engage.setEnabled(True)
        if n_shots == 1:
            n_shots_string = '(1 shot)'
        else:
            n_shots_string = f'({n_shots} shots)'
        self.ui.pushButton_engage.setText(f'Engage {n_shots_string}')

    def preparse_globals(self):
        active_groups = self.get_active_groups()
        if active_groups is None:
            # There was an error, get_active_groups has already shown
            # it to the user.
            return
        # The automatic expansion settings depend on the values. If any changes, we
        # parse again so that the globals are evaluated with the new settings:
        while True:
            results = self.parse_globals(active_groups, raise_exceptions=False, expand_globals=False)
            sequence_globals, _, evaled_globals, global_hierarchy, expansions, deprecations = results
            expansions_changed, custom_expansions = self.update_expansions(
                active_groups, sequence_globals, evaled_globals, global_hierarchy, expansions)
            if not expansions_changed:
                # Count the shots without constructing them. This must only be done
                # after the expansion type guessing has been updated, to avoid
                # exceptions when changing a zip group from a list to a single value.
                self.n_shots, dimensions = expander.count_shots(
                    sequence_globals, evaled_globals, return_dimensions=True
                )
                break
        scanned_globals = expander.scanned_globals(sequence_globals, evaled_globals, global_hierarchy)
        self.update_tabs_parsing_indication(
            active_groups, sequence_globals, evaled_globals, deprecations, custom_expansions, scanned_globals,
            dimensions, self.n_shots)
        self.update_axes_tab(expansions, dimensions)
        self.logger.info('Globals parsed')

    def preparse_globals_loop(self):
        """Runs in a thread, waiting on a threading.Event that tells us when
        some globals have changed, and calls parse_globals to evaluate them
        all before feeding the results back to the relevant tabs to be
        displayed."""
        while True:
            try:
                # Wait until we're needed:
                self.preparse_globals_required.get()
                n_requests = 1
                # Wait until the main thread is idle before clearing the queue of
                # requests. This way if preparsing is triggered multiple times within
                # the main thread before it becomes idle, we can respond to this all at
                # once, once they are all done, rather than starting too early and
                # having to preparse again.
                with qtlock:
                    while True:
                        try:
                            self.preparse_globals_required.get(block=False)
                            n_requests += 1
                        except queue.Empty:
                            break
                # Do some work:
                self.logger.info('Pre-parsing globals with %d requests', n_requests)
                try:
                    self.preparse_globals()
                finally:
                    # Tell any callers calling preparse_globals_required.join() that we
                    # are done with their request:
                    for _ in range(n_requests):
                        self.preparse_globals_required.task_done()
            except Exception: # noqa: BLE001
                # Raise the error, but keep going so we don't take down the
                # whole thread if there is a bug.
                exc_info = sys.exc_info()
                raise_exception_in_thread(exc_info)

    def wait_until_preparse_complete(self):
        """Block until the preparse loop has finished pending work"""
        self.preparse_globals_required.join()

    def get_group_item_by_name(self, globals_file, group_name, column, previous_name=None):
        """Returns an item from the row representing a globals group in the
        groups model. Which item is returned is set by the column argument."""
        parent_item = self.groups_model.findItems(globals_file, column=self.GROUPS_COL_NAME)[0]
        possible_name_items = self.groups_model.findItems(group_name, QtCore.Qt.MatchRecursive,
                                                          column=self.GROUPS_COL_NAME)
        # Don't accidentally match on other groups or files with the same name
        # as this group:
        possible_name_items = [item for item in possible_name_items if item.parent() == parent_item]
        if previous_name is not None:
            # Also filter by previous name, useful for telling rows apart when
            # a rename is in progress and two rows may temporarily contain the
            # same name (though the rename code with throw an error and revert
            # it).
            possible_name_items = [item for item in possible_name_items
                                   if item.data(self.GROUPS_ROLE_PREVIOUS_NAME) == previous_name]
        elif group_name != self.GROUPS_DUMMY_ROW_TEXT:
            # Don't return the dummy item unless they asked for it explicitly
            # - if a new group is being created, its name might be
            # simultaneously present in its own row and the dummy row too.
            possible_name_items = [item for item in possible_name_items
                                   if not item.data(self.GROUPS_ROLE_IS_DUMMY_ROW)]

        if len(possible_name_items) > 1:
            raise LookupError('Multiple items found')
        elif not possible_name_items:
            raise LookupError('No item found')
        name_item = possible_name_items[0]
        name_index = name_item.index()
        # Found the name item, get the sibling item for the column requested:
        item_index = name_index.sibling(name_index.row(), column)
        item = self.groups_model.itemFromIndex(item_index)
        return item

    def do_model_sort(self):
        header = self.ui.treeView_groups.header()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        self.ui.treeView_groups.sortByColumn(sort_column, sort_order)

    @inmain_decorator()  # Can be called from a non-main thread
    def get_active_groups(self, interactive=True):
        """Returns active groups in the format {group_name: globals_file}.
        Displays an error dialog and returns None if multiple groups of the
        same name are selected, this is invalid - selected groups must be
        uniquely named. If interactive=False, raises the exception instead."""
        active_groups = {}
        for i in range(self.groups_model.rowCount()):
            file_name_item = self.groups_model.item(i, self.GROUPS_COL_NAME)
            for j in range(file_name_item.rowCount()):
                group_name_item = file_name_item.child(j, self.GROUPS_COL_NAME)
                group_active_item = file_name_item.child(j, self.GROUPS_COL_ACTIVE)
                if group_active_item.checkState() == QtCore.Qt.Checked:
                    group_name = group_name_item.text()
                    globals_file = file_name_item.text()
                    if group_name in active_groups:
                        msg = (
                            f'There are two active groups named {group_name}. '
                            + 'Active groups must have unique names.'
                        )
                        if interactive:
                            error_dialog(msg)
                            return
                        raise RuntimeError(msg)
                    active_groups[group_name] = globals_file
        return active_groups

    def open_globals_file(self, globals_file):
        # Do nothing if this file is already open:
        if self.groups_model.findItems(globals_file, column=self.GROUPS_COL_NAME):
            return

        # Get the groups:
        groups = group_manager.get_grouplist(globals_file)
        # Add the parent row:
        file_name_item = QtGui.QStandardItem(globals_file)
        file_name_item.setEditable(False)
        file_name_item.setToolTip(globals_file)
        # Sort column by name:
        file_name_item.setData(globals_file, self.GROUPS_ROLE_SORT_DATA)

        file_active_item = QtGui.QStandardItem()
        file_active_item.setCheckState(QtCore.Qt.Unchecked)
        # Sort column by CheckState - must keep this updated when checkstate changes:
        file_active_item.setData(self.GROUPS_ACTIVE_SORT_DATA[QtCore.Qt.Unchecked], self.GROUPS_ROLE_SORT_DATA)
        file_active_item.setEditable(False)
        file_active_item.setToolTip('Check to set all the file\'s groups as active.')

        file_delete_item = QtGui.QStandardItem()  # Blank, only groups have a delete button
        file_delete_item.setEditable(False)
        # Must be set to something so that the dummy row doesn't get sorted first:
        file_delete_item.setData(False, self.GROUPS_ROLE_SORT_DATA)

        file_close_item = QtGui.QStandardItem()
        file_close_item.setIcon(Icon(':qtutils/fugue/cross'))
        file_close_item.setEditable(False)
        file_close_item.setToolTip('Close globals file.')

        self.groups_model.appendRow([file_name_item, file_active_item, file_delete_item, file_close_item])

        # Add the groups as children:
        for group_name in groups:
            row = self.make_group_row(group_name)
            file_name_item.appendRow(row)

        # Finally, add the <Click to add group> row at the bottom:
        dummy_name_item = QtGui.QStandardItem(self.GROUPS_DUMMY_ROW_TEXT)
        dummy_name_item.setToolTip('Click to add group')
        # This lets later code know that this row does
        # not correspond to an actual globals group:
        dummy_name_item.setData(True, self.GROUPS_ROLE_IS_DUMMY_ROW)
        dummy_name_item.setData(self.GROUPS_DUMMY_ROW_TEXT, self.GROUPS_ROLE_PREVIOUS_NAME)
        dummy_name_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsEditable)  # Clears the 'selectable' flag

        dummy_active_item = QtGui.QStandardItem()
        dummy_active_item.setData(True, self.GROUPS_ROLE_IS_DUMMY_ROW)
        dummy_active_item.setFlags(QtCore.Qt.ItemIsEnabled)

        dummy_delete_item = QtGui.QStandardItem('')
        dummy_delete_item.setData(True, self.GROUPS_ROLE_IS_DUMMY_ROW)
        dummy_delete_item.setFlags(QtCore.Qt.ItemIsEnabled)

        dummy_open_close_item = QtGui.QStandardItem()
        dummy_open_close_item.setData(True, self.GROUPS_ROLE_IS_DUMMY_ROW)
        dummy_open_close_item.setFlags(QtCore.Qt.ItemIsEnabled)

        # Not setting anything as the above items' sort role has the effect of
        # ensuring this row is always sorted to the end of the list, without
        # us having to implement any custom sorting methods or subclassing
        # anything, yay.

        file_name_item.appendRow([dummy_name_item, dummy_active_item, dummy_delete_item, dummy_open_close_item])
        # Expand the child items to be visible:
        self.ui.treeView_groups.setExpanded(file_name_item.index(), True)
        self.globals_changed()
        self.do_model_sort()
        # If this changed the sort order, ensure the file item is visible:
        scroll_view_to_row_if_current(self.ui.treeView_groups, file_name_item)

    def make_group_row(self, group_name):
        """Returns a new row representing one group in the groups tab, ready to be
        inserted into the model."""
        group_name_item = QtGui.QStandardItem(group_name)
        # We keep the previous name around so that we can detect what changed:
        group_name_item.setData(group_name, self.GROUPS_ROLE_PREVIOUS_NAME)
        # Sort column by name:
        group_name_item.setData(group_name, self.GROUPS_ROLE_SORT_DATA)

        group_active_item = QtGui.QStandardItem()
        group_active_item.setCheckState(QtCore.Qt.Unchecked)
        # Sort column by CheckState - must keep this updated whenever the
        # checkstate changes:
        group_active_item.setData(self.GROUPS_ACTIVE_SORT_DATA[QtCore.Qt.Unchecked], self.GROUPS_ROLE_SORT_DATA)
        group_active_item.setEditable(False)
        group_active_item.setToolTip(
            'Whether or not the globals within this group should be used by runmanager for compilation.')

        group_delete_item = QtGui.QStandardItem()
        group_delete_item.setIcon(Icon(':qtutils/fugue/minus'))
        # Must be set to something so that the dummy row doesn't get sorted first:
        group_delete_item.setData(False, self.GROUPS_ROLE_SORT_DATA)
        group_delete_item.setEditable(False)
        group_delete_item.setToolTip('Delete globals group from file.')

        group_open_close_item = QtGui.QStandardItem()
        group_open_close_item.setIcon(Icon(':qtutils/fugue/plus'))
        group_open_close_item.setData(False, self.GROUPS_ROLE_GROUP_IS_OPEN)
        # Sort column by whether group is open - must keep this manually
        # updated when the state changes:
        group_open_close_item.setData(False, self.GROUPS_ROLE_SORT_DATA)
        group_open_close_item.setEditable(False)
        group_open_close_item.setToolTip('Load globals group into runmananger.')

        row = [group_name_item, group_active_item, group_delete_item, group_open_close_item]
        return row

    def close_globals_file(self, globals_file, confirm=True):
        item = self.groups_model.findItems(globals_file, column=self.GROUPS_COL_NAME)[0]
        # Close any open groups in this globals file:

        child_name_items = [item.child(i, self.GROUPS_COL_NAME) for i in range(item.rowCount())]
        child_openclose_items = [item.child(i, self.GROUPS_COL_OPENCLOSE) for i in range(item.rowCount())]
        child_is_open = [child_item.data(self.GROUPS_ROLE_GROUP_IS_OPEN)
                         for child_item in child_openclose_items]
        if (confirm and any(child_is_open)
            and not question_dialog(f'Close {globals_file}? This will close {child_is_open.count(True)} currently open group(s).')):
            return
        to_close = [name_item for name_item, is_open in zip(child_name_items, child_is_open) if is_open]
        for name_item in to_close:
            group_name = name_item.text()
            self.close_group(globals_file, group_name)

        # Remove the globals file from the model:
        self.groups_model.removeRow(item.row())
        self.globals_changed()

    def copy_group(self, source_globals_file, source_group_name, dest_globals_file=None, delete_source_group=False):
        """This function copys a group of globals with the name source_group_name from the file
            source_globals_file to a new file dest_globals_file. If delete_source_group is True
            the source group is deleted after copying"""
        if delete_source_group and source_globals_file == dest_globals_file:
            return
        try:
            dest_group_name = group_manager.copy_group(source_globals_file, source_group_name, dest_globals_file, delete_source_group)
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(str(e))
        else:
            # Insert the newly created globals group into the model, as a
            # child row of the new globals file.
            if dest_globals_file is None:
                dest_globals_file = source_globals_file

            # find the new groups parent row by filepath
            for index in range(self.groups_model.rowCount()):
                if self.groups_model.item(index, self.GROUPS_COL_NAME).text() == dest_globals_file:
                    parent_row = self.groups_model.item(index)
                    break

            last_index = parent_row.rowCount()
            # Insert it as the row before the last (dummy) row:
            group_row = self.make_group_row(dest_group_name)
            parent_row.insertRow(last_index - 1, group_row)
            self.do_model_sort()

            # Open the group
            self.open_group(dest_globals_file, dest_group_name)
            name_item = group_row[self.GROUPS_COL_NAME]
            self.globals_changed()
            self.ui.treeView_groups.setCurrentIndex(name_item.index())

            # delete original
            if delete_source_group:
                self.delete_group(source_globals_file, source_group_name, confirm=False)

            # If this changed the sort order, ensure the group item is still visible:
            scroll_view_to_row_if_current(self.ui.treeView_groups, name_item)

    def new_group(self, globals_file, group_name):
        item = self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_NAME,
                                           previous_name=self.GROUPS_DUMMY_ROW_TEXT)
        try:
            group_manager.new_group(globals_file, group_name)
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(str(e))
        else:
            # Insert the newly created globals group into the model, as a
            # child row of the globals file it belong to.
            group_row = self.make_group_row(group_name)
            last_index = item.parent().rowCount()
            # Insert it as the row before the last (dummy) row:
            item.parent().insertRow(last_index - 1, group_row)
            self.do_model_sort()
            # Open the group and mark it active:
            self.open_group(globals_file, group_name)
            active_item = group_row[self.GROUPS_COL_ACTIVE]
            name_item = group_row[self.GROUPS_COL_NAME]
            active_item.setCheckState(QtCore.Qt.Checked)
            self.globals_changed()
            self.ui.treeView_groups.setCurrentIndex(name_item.index())
            # If this changed the sort order, ensure the group item is still visible:
            scroll_view_to_row_if_current(self.ui.treeView_groups, name_item)
        finally:
            # Set the dummy row's text back ready for another group to be created:
            item.setText(self.GROUPS_DUMMY_ROW_TEXT)

    def open_group(self, globals_file, group_name, trigger_preparse=True):
        assert (globals_file, group_name) not in self.currently_open_groups  # sanity check
        group_tab = GroupTab(self.ui.tabWidget, globals_file, group_name)
        self.currently_open_groups[globals_file, group_name] = group_tab

        # Set the open/close state in the groups_model. itemChanged will be
        # emitted and self.on_groups_model_item_changed will handle updating
        # the other data roles, icons etc:
        openclose_item = self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_OPENCLOSE)
        openclose_item.setData(True, self.GROUPS_ROLE_GROUP_IS_OPEN)
        # Trigger a preparse to occur in light of this. Calling code can
        # disable this so that multiple groups can be opened at once without
        # triggering a preparse. If they do so, they should call
        # self.globals_changed() themselves.
        if trigger_preparse:
            self.globals_changed()

    def rename_group(self, globals_file, previous_group_name, new_group_name):
        item = self.get_group_item_by_name(globals_file, new_group_name, self.GROUPS_COL_NAME,
                                           previous_name=previous_group_name)
        try:
            group_manager.rename_group(globals_file, previous_group_name, new_group_name)
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            error_dialog(str(e))
            # Set the item text back to the old name, since the rename failed:
            item.setText(previous_group_name)
        else:
            item.setData(new_group_name, self.GROUPS_ROLE_PREVIOUS_NAME)
            item.setData(new_group_name, self.GROUPS_ROLE_SORT_DATA)
            self.do_model_sort()
            # If this changed the sort order, ensure the group item is still visible:
            scroll_view_to_row_if_current(self.ui.treeView_groups, item)
            group_tab = self.currently_open_groups.pop((globals_file, previous_group_name), None)
            if group_tab is not None:
                # Change labels and tooltips appropriately if the group is open:
                group_tab.set_file_and_group_name(globals_file, new_group_name)
                # Re-add it to the dictionary under the new name:
                self.currently_open_groups[globals_file, new_group_name] = group_tab

    def close_group(self, globals_file, group_name):
        group_tab = self.currently_open_groups.pop((globals_file, group_name), None)
        assert group_tab is not None  # Just in case
        group_tab.close()
        openclose_item = self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_OPENCLOSE)
        openclose_item.setData(False, self.GROUPS_ROLE_GROUP_IS_OPEN)

    def delete_group(self, globals_file, group_name, confirm=True):
        if confirm and not question_dialog(f"Delete the group '{group_name}'?"):
            return
        # If the group is open, close it:
        group_tab = self.currently_open_groups.get((globals_file, group_name))
        if group_tab is not None:
            self.close_group(globals_file, group_name)
        group_manager.delete_group(globals_file, group_name)
        # Find the entry for this group in self.groups_model and remove it:
        name_item = self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_NAME)
        name_item.parent().removeRow(name_item.row())
        self.globals_changed()

    def on_save_configuration_triggered(self):
        if self.last_save_config_file is None:
            self.on_save_configuration_as_triggered()
            self.ui.actionSave_configuration_as.setEnabled(True)
            self.ui.actionRevert_configuration.setEnabled(True)
        else:
            self.save_configuration(self.last_save_config_file)

    def on_revert_configuration_triggered(self):
        save_data = self.get_save_data()
        if self.last_save_data is not None and save_data != self.last_save_data:
            message = f'Revert configuration to the last saved state in \'{self.last_save_config_file}\'?'
            reply = QtWidgets.QMessageBox.question(self.ui, 'Load configuration', message,
                                               QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel)
            if reply == QtWidgets.QMessageBox.Cancel:
                return
            elif reply == QtWidgets.QMessageBox.Yes:
                self.load_configuration(self.last_save_config_file)
        else:
            error_dialog('no changes to revert')

    def on_save_configuration_as_triggered(self):
        if self.last_save_config_file is not None:
            default = self.last_save_config_file
        else:
            default = os.path.join(self.exp_config.get('paths', 'experiment_shot_storage'), 'runmanager.ini')
        save_file = QtWidgets.QFileDialog.getSaveFileName(self.ui,
                                                      'Select  file to save current runmanager configuration',
                                                      default,
                                                      "config files (*.ini)")
        if type(save_file) is tuple:
            save_file, _ = save_file

        if not save_file:
            # User cancelled
            return
        if not save_file.lower().endswith('.ini'):
            save_file += '.ini'
        # Convert to standard platform specific path, otherwise Qt likes
        # forward slashes:
        save_file = os.path.abspath(save_file)
        self.save_configuration(save_file)
        self.ui.actionSave_configuration.setText(f'Save configuration {save_file}')

    def get_save_data(self):
        # Get the currently open files and active groups:
        h5_files_open = []
        active_groups = []
        for i in range(self.groups_model.rowCount()):
            file_name_item = self.groups_model.item(i, self.GROUPS_COL_NAME)
            globals_file_name = file_name_item.text()
            h5_files_open.append(globals_file_name)
            for j in range(file_name_item.rowCount()):
                group_name_item = file_name_item.child(j, self.GROUPS_COL_NAME)
                group_name = group_name_item.text()
                group_active_item = file_name_item.child(j, self.GROUPS_COL_ACTIVE)
                if group_active_item.checkState() == QtCore.Qt.Checked:
                    active_groups.append((globals_file_name, group_name))
        # Get the currently open groups:
        groups_open = []
        for i in range(self.ui.tabWidget.count()):
            tab_page = self.ui.tabWidget.widget(i)
            for (globals_file_name, group_name), group_tab in self.currently_open_groups.items():
                if group_tab.ui is tab_page:
                    groups_open.append((globals_file_name, group_name))
                    break
        # Get the labscript file, output folder, and whether the output folder
        # is default:
        current_labscript_file = self.ui.lineEdit_labscript_file.text()
        shot_output_folder = self.ui.lineEdit_shot_output_folder.text()
        is_using_default_shot_output_folder = (shot_output_folder == self.get_default_output_folder())
        # Only save the shot output folder if not using the default, that way
        # the folder updating as the day rolls over will not be detected as a
        # change to the save data:
        if is_using_default_shot_output_folder:
            shot_output_folder = ''

        # Get the server hostnames:
        blacs_host = self.ui.lineEdit_BLACS_hostname.text()

        send_to_runviewer = self.ui.checkBox_view_shots.isChecked()
        send_to_blacs = self.ui.checkBox_run_shots.isChecked()
        shuffle = self.ui.pushButton_shuffle.isChecked()

        # axes tab information
        axes = []
        for i in range(self.axes_model.rowCount()):
            name_item = self.axes_model.item(i, self.AXES_COL_NAME)
            shuffle_item = self.axes_model.item(i, self.AXES_COL_SHUFFLE)
            shuffle_state = shuffle_item.checkState()
            
            axes.append((name_item.data(self.AXES_ROLE_NAME), 1 if shuffle_state == QtCore.Qt.Checked else 0))
        
        save_data = {'h5_files_open': h5_files_open,
                     'active_groups': active_groups,
                     'groups_open': groups_open,
                     'current_labscript_file': current_labscript_file,
                     'shot_output_folder': shot_output_folder,
                     'is_using_default_shot_output_folder': is_using_default_shot_output_folder,
                     'send_to_runviewer': send_to_runviewer,
                     'send_to_blacs': send_to_blacs,
                     'shuffle': shuffle,
                     'axes': axes,
                     'blacs_host': blacs_host}
        return save_data

    def save_configuration(self, save_file):
        save_data = self.get_save_data()
        self.last_save_config_file = save_file
        self.last_save_data = save_data
        save_appconfig(save_file, {'runmanager_state': save_data})

    def on_load_configuration_triggered(self):
        save_data = self.get_save_data()
        if self.last_save_data is not None and save_data != self.last_save_data:
            message = ('Current configuration (which groups are active/open and other GUI state) '
                       f'has changed: save config file \'{self.last_save_config_file}\'?')
            reply = QtWidgets.QMessageBox.question(self.ui, 'Load configuration', message,
                                               QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel)
            if reply == QtWidgets.QMessageBox.Cancel:
                return
            if reply == QtWidgets.QMessageBox.Yes:
                self.save_configuration(self.last_save_config_file)

        if self.last_save_config_file is not None:
            default = self.last_save_config_file
        else:
            default = os.path.join(self.exp_config.get('paths', 'experiment_shot_storage'), 'runmanager.ini')

        file = QtWidgets.QFileDialog.getOpenFileName(self.ui,
                                                 'Select runmanager configuration file to load',
                                                 default,
                                                 "config files (*.ini)")
        if type(file) is tuple:
            file, _ = file

        if not file:
            # User cancelled
            return
        # Convert to standard platform specific path, otherwise Qt likes
        # forward slashes:
        file = os.path.abspath(file)
        self.load_configuration(file)

    def load_configuration(self, filename):
        self.last_save_config_file = filename
        self.ui.actionSave_configuration.setText(f'Save configuration {filename}')
        # Close all files:
        save_data = self.get_save_data()
        for globals_file in save_data['h5_files_open']:
            self.close_globals_file(globals_file, confirm=False)
        # Ensure folder exists, if this was opened programmatically we are
        # creating the file, so the directory had better exist!
        runmanager_config = load_appconfig(filename).get('runmanager_state', {})

        has_been_a_warning = [False]
        def warning(message):
            if not has_been_a_warning[0]:
                has_been_a_warning[0] = True
                self.output_box.output('\n')
            self.output_box.output(f'Warning: {message}\n', red=True)

        for globals_file in runmanager_config.get('h5_files_open', []):
            if os.path.exists(globals_file):
                try:
                    self.open_globals_file(globals_file)
                    self.last_opened_globals_folder = os.path.dirname(globals_file)
                except Exception: # noqa: BLE001 redirect all exceptions to appropriate thread
                    raise_exception_in_thread(sys.exc_info())
                    continue
            else:
                self.output_box.output(f'\nWarning: globals file {globals_file} no longer exists\n', red=True)

        for globals_file, group_name in runmanager_config.get('active_groups', []):
            try:
                group_active_item = self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_ACTIVE)
                group_active_item.setCheckState(QtCore.Qt.Checked)
            except LookupError:
                warning(f"previously active group '{group_name}' in {globals_file} no longer exists")

        for globals_file, group_name in runmanager_config.get('groups_open', []):
            # First check if it exists:
            try:
                self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_NAME)
            except LookupError:
                warning(f"previously open group '{group_name}' in {globals_file} no longer exists")
            else:
                self.open_group(globals_file, group_name)

        current_labscript_file = runmanager_config.get('current_labscript_file')
        if current_labscript_file is not None:
            if os.path.exists(current_labscript_file):
                self.ui.lineEdit_labscript_file.setText(current_labscript_file)
                self.last_opened_labscript_folder = os.path.dirname(current_labscript_file)
            elif current_labscript_file:
                warning(f'previously selected labscript file {current_labscript_file} no longer exists')

        shot_output_folder = runmanager_config.get('shot_output_folder')
        if shot_output_folder is not None:
            self.ui.lineEdit_shot_output_folder.setText(shot_output_folder)
            self.last_selected_shot_output_folder = os.path.dirname(shot_output_folder)

        if runmanager_config.get('is_using_default_shot_output_folder', False):
            default_output_folder = self.get_default_output_folder()
            self.ui.lineEdit_shot_output_folder.setText(default_output_folder)
            self.last_selected_shot_output_folder = os.path.dirname(default_output_folder)

        send_to_runviewer = runmanager_config.get('send_to_runviewer')
        if send_to_runviewer is not None:
            self.ui.checkBox_view_shots.setChecked(send_to_runviewer)

        send_to_blacs = runmanager_config.get('send_to_blacs')
        if send_to_blacs is not None:
            self.ui.checkBox_run_shots.setChecked(send_to_blacs)
        
        # clear the axes model first
        if self.axes_model.rowCount():
            self.axes_model.removeRows(0, self.axes_model.rowCount())

        # set the state of the global shuffle button. This ensure that if no axes items get loaded afterwards
        # (e.g. because the globals in the .ini file are no longer expansion globals), then we still have 
        # an approximate state for the shuffle button that will apply to whatever globals are to be expanded.
        if runmanager_config.get('shuffle', False):
            self.ui.pushButton_shuffle.setChecked(True)

        # Now load the axes states (order and shuffle). This will also ensure the shuffle button matches the 
        # state of these items (since we don't save/restore the tri-state nature of the global shuffle button
        axes = runmanager_config.get('axes')
        if axes is not None and isinstance(axes, list):
            # clear model
            for name, shuffle in axes:
                self.add_item_to_axes_model(name, shuffle)
            self.update_axes_indentation() 

        blacs_host = runmanager_config.get('blacs_host')
        if blacs_host is not None:
            self.ui.lineEdit_BLACS_hostname.setText(blacs_host)

        # Set as self.last_save_data:
        save_data = self.get_save_data()
        self.last_save_data = save_data
        self.ui.actionSave_configuration_as.setEnabled(True)
        self.ui.actionRevert_configuration.setEnabled(True)

    def compile_loop(self):
        while True:
            try:
                labscript_file, run_files, n_shots, send_to_BLACS, BLACS_host, send_to_runviewer, history = self.compile_queue.get()
                inmain(self.ui.pushButton_abort.setEnabled, True)
                run_files = iter(run_files)  # Should already be in iterator but just in case
                shot_number = 0
                while True:
                    if self.compilation_aborted.is_set():
                        self.output_box.output('Compilation aborted.\n\n', red=True)
                        break
                    try:
                        try:
                            # We do next() instead of looping over run_files
                            # so that if compilation is aborted we won't
                            # create an extra file unnecessarily.
                            run_file = next(run_files)
                        except StopIteration:
                            self.output_box.output('Ready.\n\n')
                            break
                        else:
                            shot_number += 1
                            self.output_box.write(
                                f'Compiling {os.path.basename(run_file)} (shot {shot_number} of {n_shots})\n',
                                bold=True,
                            )
                            self.to_child.put(['compile', [labscript_file, run_file]])
                            signal, success = self.from_child.get()
                            assert signal == 'done'
                            if not success:
                                self.compilation_aborted.set()
                                continue
                            if shot_number == 1:
                                self.record_history(*history)
                            if send_to_BLACS:
                                self.send_to_BLACS(run_file, BLACS_host)
                            if send_to_runviewer:
                                self.send_to_runviewer(run_file)
                    except Exception as e: # noqa: BLE001 send all errors to dialog box
                        self.output_box.output(str(e) + '\n', red=True)
                        self.compilation_aborted.set()
                if self.compile_queue.empty():
                    inmain(self.ui.pushButton_abort.setEnabled, False)
                self.compilation_aborted.clear()
            except Exception: # noqa: BLE001
                # Raise it so whatever bug it is gets seen, but keep going so
                # the thread keeps functioning:
                exc_info = sys.exc_info()
                raise_exception_in_thread(exc_info)
                continue

    def record_history(self, active_groups, sequence_globals):
        try:
            group_manager.record_history(active_groups, sequence_globals)
        except Exception as e: # noqa: BLE001 send all errors to the output box
            self.output_box.output(f'Could not record the values of globals in their history: {e!s}\n', red=True)

    def discard_queued_sequences(self):
        """Removes every sequence waiting in the compile queue and reports the number
        removed in the output box."""
        n_discarded = 0
        while True:
            try:
                self.compile_queue.get_nowait()
            except queue.Empty:
                break
            n_discarded += 1
        if n_discarded == 1:
            self.output_box.output('Discarded 1 queued sequence.\n', red=True)
        elif n_discarded:
            self.output_box.output(f'Discarded {n_discarded} queued sequences.\n', red=True)

    def parse_globals(self, active_groups, raise_exceptions=True, expand_globals=True, expansion_order = None, return_dimensions = False):
        sequence_globals = group_manager.get_globals(active_groups)
        #logger.info('got sequence globals')
        evaled_globals, global_hierarchy, expansions, deprecations = evaluator._evaluate_globals(
            sequence_globals, raise_exceptions
        )
        #logger.info('evaluated sequence globals')
        if expand_globals:
            if return_dimensions:
                shots, dimensions = expander.expand_globals(sequence_globals, evaled_globals, expansion_order, return_dimensions=return_dimensions)
            else:
                shots = expander.expand_globals(sequence_globals, evaled_globals, expansion_order)
        else:
            shots = []
            dimensions = {}
        #logger.info('expanded sequence globals')
        if return_dimensions:
            return sequence_globals, shots, evaled_globals, global_hierarchy, expansions, deprecations, dimensions
        else:
            return sequence_globals, shots, evaled_globals, global_hierarchy, expansions, deprecations

    def update_expansions(self, active_groups, sequence_globals, evaled_globals, global_hierarchy, expansions):
        """Writes the automatic expansion setting of each global that has no custom
        setting to its globals file. A global whose file does not record whether its
        setting is custom gets a custom setting if the stored setting is not the
        automatic one. Returns whether any setting changed, and the custom flags of the
        globals of the active groups in the structure of sequence_globals."""
        custom = group_manager.get_custom_expansions(active_groups)
        values = differ.flatten_globals(evaled_globals, evaluated=True)
        flat_custom = {name: bool(flag) for group in custom.values() for name, flag in group.items()}
        automatic = group_manager.automatic_expansions(values, global_hierarchy, expansions, flat_custom)
        migrated = False
        for group_name, globals_file in active_groups.items():
            for global_name in sequence_globals[group_name]:
                if global_name not in custom[group_name]:
                    # Deleted or renamed after the globals were read
                    continue
                if custom[group_name][global_name] is None and not isinstance(values[global_name], Exception):
                    flag = expansions[global_name] != automatic[global_name]
                    group_manager.set_expansion(
                        globals_file, group_name, global_name, expansions[global_name], custom=flag)
                    custom[group_name][global_name] = flat_custom[global_name] = flag
                    migrated = migrated or flag
        if migrated:
            automatic = group_manager.automatic_expansions(values, global_hierarchy, expansions, flat_custom)
        changed = False
        for group_name, globals_file in active_groups.items():
            for global_name in sequence_globals[group_name]:
                if global_name not in custom[group_name]:
                    continue
                if not custom[group_name][global_name] and expansions[global_name] != automatic[global_name]:
                    self.logger.info('%s:%s - automatic expansion: %s = %s -> %s', globals_file, group_name,
                                     global_name, expansions[global_name], automatic[global_name])
                    group_manager.set_expansion(
                        globals_file, group_name, global_name, automatic[global_name], custom=False)
                    changed = True
        return changed, custom

    def zip_group_names(self):
        """The zip group names in use in the open groups, sorted."""
        names = set()
        for group_tab in self.currently_open_groups.values():
            for row in range(group_tab.globals_model.rowCount()):
                item = group_tab.globals_model.item(row, GroupTab.GLOBALS_COL_EXPANSION)
                expansion = GroupTab.expansion_from_text(item.text())
                if not item.data(GroupTab.GLOBALS_ROLE_IS_DUMMY_ROW) and expansion not in ('', 'outer'):
                    names.add(expansion)
        return sorted(names)

    def make_h5_files(self, labscript_file, output_folder, sequence_globals, shots, shuffle):
        if not shots:
            raise ValueError('No shots to compile: a global that is expanded over has no values.')
        sequence_attrs, default_output_dir, filename_prefix = compiler.new_sequence_details(
            labscript_file, config=self.exp_config, increment_sequence_index=True
        )
        if output_folder == self.previous_default_output_folder:
            # The user is using dthe efault output folder. Just in case the sequence
            # index has been updated or the date has changed, use the default_output dir
            # obtained from new_sequence_details, as it is race-free, whereas the one
            # from the UI may be out of date since we only update it once a second.
            output_folder = default_output_dir
        self.check_output_folder_update()
        run_files = compiler.make_run_files(
            output_folder,
            sequence_globals,
            shots,
            sequence_attrs,
            filename_prefix,
            shuffle,
        )
        self.logger.debug(run_files)
        return labscript_file, run_files

    def send_to_BLACS(self, run_file, BLACS_hostname):
        port = int(self.exp_config.get('ports', 'BLACS'))
        agnostic_path = shared_drive.path_to_agnostic(run_file)
        self.output_box.output(f'Submitting run file {os.path.basename(run_file)}.\n')
        try:
            response = zmq_get(port, BLACS_hostname, data=agnostic_path)
            if 'added successfully' in response:
                self.output_box.output(response)
            else:
                raise Exception(response)
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            self.output_box.output(f'Couldn\'t submit job to control server: {e!s}\n', red=True)
            self.compilation_aborted.set()

    def send_to_runviewer(self, run_file):
        runviewer_port = int(self.exp_config.get('ports', 'runviewer'))
        agnostic_path = shared_drive.path_to_agnostic(run_file)
        try:
            response = zmq_get(runviewer_port, 'localhost', data='hello', timeout=1)
            if 'hello' not in response:
                raise Exception(response)
        except Exception:
            self.logger.info('runviewer not running, attempting to start...')
            # Runviewer not running, start it:
            if os.name == 'nt':
                creationflags = 0x00000008  # DETACHED_PROCESS from the win32 API
                scripts_dir = desktop_app.environment.get_scripts_dir('runviewer')
                subprocess.Popen([str(scripts_dir / 'runviewer-gui')],
                                 creationflags=creationflags, stdout=None, stderr=None,
                                 close_fds=True)
            else:
                subprocess.Popen([sys.executable, '-m', 'runviewer'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
            try:
                zmq_get(runviewer_port, 'localhost', data='hello', timeout=15)
            except Exception as e: # noqa: BLE001 send all errors to dialog box
                self.output_box.output(f'Couldn\'t submit shot to runviewer: {e!s}\n\n', red=True)

        try:
            response = zmq_get(runviewer_port, 'localhost', data=agnostic_path, timeout=0.5)
            if 'ok' not in response:
                raise Exception(response)
            else:
                self.output_box.output(f'Shot {os.path.basename(run_file)} sent to runviewer.\n')
        except Exception as e: # noqa: BLE001 send all errors to dialog box
            self.output_box.output(f'Couldn\'t submit shot to runviewer: {e!s}\n\n', red=True)


class RemoteServer(ZMQServer):
    def __init__(self, app):
        self.app = app
        port = self.app.exp_config.getint(
            'ports', 'runmanager', fallback=runmanager.remote.DEFAULT_PORT
        )
        ZMQServer.__init__(self, port=port)

    def handle_get_globals(self, raw=False):
        active_groups = inmain(self.app.get_active_groups, interactive=False)
        sequence_globals = group_manager.get_globals(active_groups)
        all_globals = {}
        if raw:
            for group_globals in sequence_globals.values():
                values_only = {name: val for name, (val, _, _) in group_globals.items()}
                all_globals.update(values_only)
        else:
            evaled_globals, _, _, _ = evaluator._evaluate_globals(
                sequence_globals, raise_exceptions=False
            )
            for group_globals in evaled_globals.values():
                all_globals.update(group_globals)
        return all_globals

    @inmain_decorator()
    def handle_set_globals(self, globals, raw=False, preserve_comments=True):
        active_groups = self.app.get_active_groups(interactive=False)
        sequence_globals = group_manager.get_globals(active_groups)
        try:
            for global_name, new_value in globals.items():
                # Unless raw=True, convert to str representation for saving to the GUI
                # or file. If this does not result in an object the user can actually
                # use, evaluation will error and the caller will find out about it later
                if not raw:
                    # Arrays are written in full, not summarised with an ellipsis:
                    with np.printoptions(threshold=sys.maxsize):
                        new_value = repr(new_value)
                elif not isinstance(new_value, (str, bytes)):
                    msg = "global %s must be a string if raw=True, not %s"
                    raise TypeError(msg % (global_name, new_value.__class__.__name__))

                # Find the group this global is in:
                for group_name, group_globals in sequence_globals.items():
                    globals_file = active_groups[group_name]
                    if global_name in group_globals:
                        # Confirm it's not also in another group:
                        for other_name, other_globals in sequence_globals.items():
                            if other_globals is not group_globals and global_name in other_globals:
                                msg = (f'Cannot set global {global_name}, it is defined in'
                                       f' multiple active groups: {group_name} and {other_name}')
                                raise RuntimeError(msg)
                        previous_value, _, _ = group_globals[global_name]

                        if preserve_comments and not raw:
                            # Append an expression-final comment in the previous
                            # expression to the new one:
                            comments = tokenizer.find_comments(previous_value)
                            if comments:
                                # Only the final comment
                                comment_start, comment_end = comments[-1]
                                # Only if the comment is the last thing in the expression:
                                if comment_end == len(previous_value):
                                    new_value += previous_value[comment_start:comment_end]
                        try:
                            # Is the group open?
                            group_tab = self.app.currently_open_groups[
                                globals_file, group_name
                            ]
                        except KeyError:
                            # Group is not open. Change the global value on disk:
                            group_manager.set_value(
                                globals_file, group_name, global_name, new_value
                            )
                        else:
                            # Group is open. Change the global value via the GUI:
                            group_tab.change_global_value(
                                global_name,
                                previous_value,
                                new_value,
                                interactive=False,
                            )
                        break
                else:
                    # Global was not found.
                    msg = f"Global {global_name} not found in any active group"
                    raise ValueError(msg)
        finally:
            # Trigger preparsing of globals to occur so that changes in globals not in
            # open tabs are reflected in the GUI, such as n_shots, errors on other
            # globals that depend on them, etc.
            self.app.globals_changed()

    def handle_engage(self):
        self.app.wait_until_preparse_complete()
        inmain(self.app.on_engage_clicked)

    @inmain_decorator()
    def handle_abort(self):
        self.app.on_abort_clicked()

    @inmain_decorator()
    def handle_get_run_shots(self):
        return self.app.ui.checkBox_run_shots.isChecked()

    @inmain_decorator()
    def handle_set_run_shots(self, value):
        self.app.ui.checkBox_run_shots.setChecked(value)

    @inmain_decorator()
    def handle_get_view_shots(self):
        return self.app.ui.checkBox_view_shots.isChecked()

    @inmain_decorator()
    def handle_set_view_shots(self, value):
        self.app.ui.checkBox_view_shots.setChecked(value)

    @inmain_decorator()
    def handle_get_shuffle(self):
        return self.app.ui.pushButton_shuffle.isChecked()

    @inmain_decorator()
    def handle_set_shuffle(self, value):
        self.app.ui.pushButton_shuffle.setChecked(value)

    def handle_n_shots(self):
        # Wait until any current preparsing is done, to ensure this is not racy w.r.t
        # previous remote calls:
        self.app.wait_until_preparse_complete()
        return self.app.n_shots

    @inmain_decorator()
    def handle_get_labscript_file(self):
        labscript_file = self.app.ui.lineEdit_labscript_file.text()
        if not labscript_file:
            return ''
        return os.path.abspath(labscript_file)

    @inmain_decorator()
    def handle_set_labscript_file(self, value):
        labscript_file = os.path.abspath(value)
        self.app.ui.lineEdit_labscript_file.setText(labscript_file)

    @inmain_decorator()
    def handle_get_shot_output_folder(self):
        shot_output_folder = self.app.ui.lineEdit_shot_output_folder.text()
        if not shot_output_folder:
            return ''
        return os.path.abspath(shot_output_folder)

    @inmain_decorator()
    def handle_set_shot_output_folder(self, value):
        shot_output_folder = os.path.abspath(value)
        self.app.ui.lineEdit_shot_output_folder.setText(shot_output_folder)

    def handle_error_in_globals(self):
        try:
            # This will raise an exception if there are multiple active groups of the
            # same name:
            active_groups = inmain(self.app.get_active_groups, interactive=False)
            sequence_globals = group_manager.get_globals(active_groups)
            # This will raise an exception if any of the globals can't be evaluated:
            evaluator._evaluate_globals(sequence_globals, raise_exceptions=True)
        except Exception: # noqa: BLE001 convert all exceptions to a boolean
            return True
        return False

    def handle_is_output_folder_default(self):
        return not self.app.non_default_folder

    @inmain_decorator()
    def handle_reset_shot_output_folder(self):
        self.app.on_reset_shot_output_folder_clicked(None)

    def handler(self, request_data):
        cmd, args, kwargs = request_data
        if cmd == 'hello':
            return 'hello'
        elif cmd == '__version__':
            return runmanager.__version__
        try:
            return getattr(self, 'handle_' + cmd)(*args, **kwargs)
        except Exception as e: # noqa: BLE001 send all exceptions back to caller
            msg = traceback.format_exc()
            msg = "Runmanager server returned an exception:\n" + msg
            try:
                return e.__class__(msg)
            except Exception: # noqa: BLE001 the class may need other constructor arguments
                return Exception(msg)


if __name__ == "__main__":
    logger = setup_logging('runmanager')
    labscript_utils.excepthook.set_logger(logger)
    logger.info('\n\n===============starting===============\n')
    port = LabConfig().getint(
        'ports', 'runmanager', fallback=runmanager.remote.DEFAULT_PORT
    )
    if port_in_use(port):
        message = (
            f"runmanager cannot start because port {port}, which is the runmanager "
            "port in the labconfig, is in use. Another copy of runmanager is probably "
            "running."
        )
        logger.error(message)
        splash.hide()
        QtWidgets.QMessageBox.critical(None, 'runmanager', message)
        sys.exit(1)
    qapplication = QtWidgets.QApplication.instance()
    if qapplication is None:
        qapplication = QtWidgets.QApplication(sys.argv)
    qapplication.setAttribute(QtCore.Qt.AA_DontShowIconsInMenus, False)
    app = RunManager()
    splash.update_text('Starting remote server')
    remote_server = RemoteServer(app)
    splash.hide()

    # Let the interpreter run every 500ms so it sees Ctrl-C interrupts:
    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)
    # Upon seeing a ctrl-c interrupt, quit the event loop
    signal.signal(signal.SIGINT, lambda *args: qapplication.exit())

    qapplication.exec()
    remote_server.shutdown()

    app.output_box.shutdown()

    # Delete the windows now, rather than in PyQt's QObject cleanup at exit, during which
    # Python code that creates or destroys wrapped Qt objects can crash the interpreter
    # (https://github.com/Python-SIP/sip/pull/121). This can be removed once PyQt5-sip
    # and PyQt6-sip releases include that fix.
    from qtutils.qt import PYQT_LIBS
    if QT_ENV in PYQT_LIBS:
        import importlib
        sip = importlib.import_module(f'{QT_ENV}.sip')
        for widget in qapplication.topLevelWidgets():
            # Other top-level widgets are owned by C++ objects, which delete them
            if sip.ispyowned(widget):
                widget.deleteLater()
        qapplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
