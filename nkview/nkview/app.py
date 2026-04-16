# Copyright (C) 2024  Max Wiklund
#
# Licensed under the Apache License, Version 2.0 (the “License”);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import argparse
import ctypes
import functools
import os
import platform
import sys

import nkview
from nkview import utils
from nkview.constants import QT_STYLE
from nkview.graph_view import NukeNodeGraphWidget
from nkview.outliner import OutlinerWidget
from nkview.qt import QtCore, QtGui, QtWebEngineWidgets, QtWidgets
from nuke_parser.parser import _parseGizmos, _parse_nk_generator
from nkview.nkseHighlighter import NkHighlighter
from nkview.nkCodeEditor import CodeEditor


def _setupCli() -> argparse.Namespace:
    """Setup command line options.

    Returns:
        Parsed args.

    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", "-o", help="File path to nk file")
    return parser.parse_args()


class DocsWidget(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget):
        super(DocsWidget, self).__init__(parent)
        self.web_view = QtWebEngineWidgets.QWebEngineView()
        layout = QtWidgets.QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web_view)
        self.setLayout(layout)

    def loadPage(self, url: str) -> None:
        self.web_view.load(QtCore.QUrl(url))


class NkViewMainWindow(QtWidgets.QMainWindow):
    """Main application window."""

    def __init__(self):
        super(NkViewMainWindow, self).__init__()
        self.setWindowTitle("NkView")
        self.setWindowIcon(QtGui.QIcon(":nuke.png"))
        self.resize(1500, 800)
        self.setStyleSheet(QT_STYLE)

        self.setDockOptions(QtWidgets.QMainWindow.AllowNestedDocks | QtWidgets.QMainWindow.AllowTabbedDocks)
        
        self.outliner = OutlinerWidget(self)
        self._node_graph_view = NukeNodeGraphWidget(self)
        
        self.code_editor = CodeEditor()
        self.highlighter = NkHighlighter(self.code_editor.document())

        self.stack_list = QtWidgets.QListWidget(self)

        self.setCentralWidget(self._node_graph_view)

        # Outliner dock
        self.dock_outliner = QtWidgets.QDockWidget("Outliner", self)
        self.dock_outliner.setWidget(self.outliner)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.dock_outliner)

        # Code editor dock
        self.dock_code = QtWidgets.QDockWidget("Script Editor", self)
        self.dock_code.setWidget(self.code_editor)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.dock_code)

        # Stack list dock
        self.dock_stack = QtWidgets.QDockWidget("Stack View", self)
        self.dock_stack.setWidget(self.stack_list)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.dock_stack)
        
        # Hide debug panels by default
        self.dock_code.hide()
        self.dock_stack.hide()

        self.debug_toolbar = QtWidgets.QToolBar("Debug Tools")
        self.addToolBar(QtCore.Qt.TopToolBarArea, self.debug_toolbar)
        self.debug_toolbar.hide()

        self._setupMenu()

        def _outliner_scene_loaded(node):
            if node:
                self.outliner.buildTree(node.nk_node)
            else:
                self.outliner._view.source_model.clear()
                self.outliner._view._scene_map = {}

        self._node_graph_view.sceneLoaded.connect(_outliner_scene_loaded)
        self._node_graph_view.scenePath.connect(self._setWindowTitleCallback)
        self._node_graph_view.scenePath.connect(self._updateRecentlyOpenedCallback)
        self.outliner.nodesSelected.connect(self._node_graph_view.selectNodes)
        self.outliner.nodesDeSelected.connect(self._node_graph_view.deselectNodes)
        self.outliner.navigated.connect(self._node_graph_view.navigateToNode)
        self._node_graph_view.selectionChanged.connect(self.outliner.selectNodes)

        self.debug_parser = None
        self.debug_mode = False
        self.current_debug_root = None
        self.current_stack_depth = 0
        self._user_touched_canvas = False

        class CanvasInteractionFilter(QtCore.QObject):
            def __init__(self, obj):
                super().__init__()
                self.obj = obj
            def eventFilter(self, o, event):
                if event.type() in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.Wheel):
                    self.obj._user_touched_canvas = True
                return False

        self.interaction_filter = CanvasInteractionFilter(self)
        self._node_graph_view._view.viewport().installEventFilter(self.interaction_filter)

    def _setWindowTitleCallback(self, file_path: str) -> None:
        self.setWindowTitle(os.path.abspath(file_path))

    def _setupMenu(self) -> None:
        """Setup menubar."""
        file_menu = QtWidgets.QMenu("&File", self)
        open_action = QtWidgets.QAction(QtGui.QIcon(), "Open", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._openNkFileCallback)
        file_menu.addAction(open_action)

        debug_action = QtWidgets.QAction("Open in Debug Mode", self)
        debug_action.triggered.connect(self._openDebugNkFileCallback)
        file_menu.addAction(debug_action)

        # Setup Debug Toolbar
        action_step_node = QtWidgets.QAction("Step Node (F8)", self)
        action_step_node.setShortcut("F8")
        action_step_node.triggered.connect(lambda: self._step_debug("node") if self.debug_mode else None)
        self.debug_toolbar.addAction(action_step_node)
        
        action_step_resume = QtWidgets.QAction("Resume/Next Bkpt (F9)", self)
        action_step_resume.setShortcut("F9")
        action_step_resume.triggered.connect(lambda: self._step_debug("breakpoint") if self.debug_mode else None)
        self.debug_toolbar.addAction(action_step_resume)
        
        action_step_into = QtWidgets.QAction("Step Into (F7)", self)
        action_step_into.setShortcut("F7")
        action_step_into.triggered.connect(lambda: self._step_debug("into") if self.debug_mode else None)
        self.debug_toolbar.addAction(action_step_into)

        self.open_recent = file_menu.addMenu(QtGui.QIcon(), "Open Recent")
        self._buildRecentlyOpenedMenu()

        file_menu.addSeparator()
        close_action = QtWidgets.QAction(QtGui.QIcon(), "Close", self)
        close_action.setShortcut("Ctr+Q")
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

        help_menu = QtWidgets.QMenu("&Help", self)
        docs_action = QtWidgets.QAction(QtGui.QIcon(), "Documentation", self)
        docs_action.triggered.connect(self._openDocsCallback)
        help_menu.addAction(docs_action)

        window_menu = QtWidgets.QMenu("&Window", self)
        window_menu.addAction(self.dock_outliner.toggleViewAction())
        window_menu.addAction(self.dock_code.toggleViewAction())
        window_menu.addAction(self.dock_stack.toggleViewAction())
        window_menu.addAction(self.debug_toolbar.toggleViewAction())
        
        self.menuBar().addMenu(file_menu)
        self.menuBar().addMenu(window_menu)
        self.menuBar().addMenu(help_menu)

    def _openNkFileCallback(self) -> None:
        """Brows files to open nuke script."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select .nk file to open...",
            QtCore.QDir.currentPath(),
            "NK (*.nk)",
        )
        if path:
            self.dock_code.hide()
            self.dock_stack.hide()
            self.debug_toolbar.hide()
            self.debug_mode = False
            self.debug_parser = None
            self._node_graph_view.loadNk(path)

    def _openDebugNkFileCallback(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select .nk file to open in Debug...",
            QtCore.QDir.currentPath(),
            "NK (*.nk)",
        )
        if path:
            self.debug_mode = True
            self.dock_code.show()
            self.dock_stack.show()
            self.debug_toolbar.show()
            self._loadDebugNk(path)

    def _loadDebugNk(self, file_path: str):
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        self.code_editor.setPlainText(text)
        self.debug_parser = _parse_nk_generator(file_path, _parseGizmos())
        self.code_editor.clean_all_debug_points()
        self.code_editor.active_debug_point = None
        self.stack_list.clear()
        self._first_debug_frame = True
        self._user_touched_canvas = False
        
        # Clear existing scene UI
        self._node_graph_view._view.setScene(QtWidgets.QGraphicsScene())
        self._node_graph_view.sceneLoaded.emit(None)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if self.debug_mode and self.debug_parser:
            if event.key() == QtCore.Qt.Key_F8:
                self._step_debug("node")
                return
            elif event.key() == QtCore.Qt.Key_F9:
                self._step_debug("breakpoint")
                return
            elif event.key() == QtCore.Qt.Key_F7:
                self._step_debug("into")
                return
        
        super().keyPressEvent(event)

    def _update_debug_ui(self, line_num, main_stack, parents_stack, is_step_into=False):
        self.code_editor.active_debug_point = line_num
        self.code_editor.move_cursor_to_line(line_num)
        self.code_editor.line_number_area.update()
        
        self.stack_list.clear()

        def _get_depth(nk_n):
            depth = 0
            while nk_n and nk_n.parent():
                depth += 1
                nk_n = nk_n.parent()
            return max(0, depth - 1)  # -1 because Root is 0 but we want it at base

        for i, stack_node in enumerate(main_stack._items):
            depth_level = _get_depth(stack_node)
            indent = "    " * depth_level
            name = stack_node.name() if stack_node else "None"
            self.stack_list.addItem(f"{indent}{i}: {name}")
            
        # Temporarily rebuild the visual graph
        if parents_stack and not parents_stack.empty():
            root_node = parents_stack._items[0]
            from nkview.gui_nodes import GroupNode
            
            view = self._node_graph_view._view
            
            # Save scroll positions so the builder doesn't jitter unless it has to
            h_bar = view.horizontalScrollBar().value()
            v_bar = view.verticalScrollBar().value()

            self._node_graph_view._scene_map = {}
            self._node_graph_view.gui_root = GroupNode(root_node, self._node_graph_view._scene_map)
            
            if is_step_into and parents_stack.peek():
                self.current_debug_root_nk_node = parents_stack.peek()
                
            if not getattr(self, "current_debug_root_nk_node", None) or self.current_debug_root_nk_node not in parents_stack._items:
                self.current_debug_root_nk_node = parents_stack.peek()
                
            gui_target = self._node_graph_view._scene_map.get(self.current_debug_root_nk_node.path())
            if gui_target:
                view.setScene(gui_target.getScene())

            if not getattr(self, "_user_touched_canvas", False):
                self._node_graph_view.frameSelected()
            else:
                view.horizontalScrollBar().setValue(h_bar)
                view.verticalScrollBar().setValue(v_bar)

    def _step_debug(self, mode: str):
        import nuke_parser.parser
        
        start_depth = getattr(self, "current_stack_depth", 0)
        
        # Determine if we are on Root. If we are on Root, F8 should NOT step over it, it should evaluate its direct children!
        if mode == "node" and start_depth == 0:
            start_depth = 1

        while True:
            try:
                event_type, line_num, node, ms, ps = next(self.debug_parser)
                
                # F9: stop only at breakpoints
                if mode == "breakpoint" and event_type == "line":
                    if line_num in self.code_editor.breakpoint_lines:
                        self._update_debug_ui(line_num, ms, ps)
                        return
                    continue
                
                if event_type == "node":
                    if mode == "node":
                        if node and node.Class() in nuke_parser.parser.ROOT_NODE_CLASSES:
                            self._update_debug_ui(line_num + 1, ms, ps)
                            return
                        if len(ps._items) <= start_depth:
                            self._update_debug_ui(line_num + 1, ms, ps)
                            return
                            
                    elif mode == "into":
                        self._update_debug_ui(line_num + 1, ms, ps, is_step_into=True)
                        return

            except StopIteration as e:
                root = e.value
                self.debug_parser = None
                self.debug_mode = False
                self.debug_toolbar.hide()
                
                from nkview.gui_nodes import GroupNode
                self._node_graph_view._scene_map = {}
                self._node_graph_view.root = root
                self._node_graph_view.gui_root = GroupNode(root, self._node_graph_view._scene_map)
                self._node_graph_view._view.setScene(self._node_graph_view.gui_root.getScene())
                self._node_graph_view.frameSelected()
                self._node_graph_view.sceneLoaded.emit(self._node_graph_view.gui_root)
                break


    def _updateRecentlyOpenedCallback(self, file_path: str) -> None:
        utils.addRecentlyOpened(os.path.realpath(file_path))
        self._buildRecentlyOpenedMenu()

    def _buildRecentlyOpenedMenu(self) -> None:
        self.open_recent.clear()
        for path in utils.recentlyOpened():
            action = QtWidgets.QAction(QtGui.QIcon(), path, self)
            action.triggered.connect(
                functools.partial(self._node_graph_view.loadNk, path)
            )
            self.open_recent.addAction(action)

    def _openDocsCallback(self) -> None:
        win = DocsWidget(self)
        win.loadPage(
            "https://github.com/maxWiklund/nuke_parser/blob/master/docs/nkview.md"
        )
        win.show()
        win.exec()


def run() -> None:
    """Run app."""
    args = _setupCli()
    if platform.system() == "Windows":
        # Fix app icon on taskbar on Windows.
        app_id = f"nkview.{nkview.__version__}"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)

    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(QtGui.QIcon(":nuke.png"))
    app.setStyle("Fusion")
    win = NkViewMainWindow()

    # Load gizmos.
    _parseGizmos()

    if args.open:
        win.loadNk(args.open)
    win.show()
    sys.exit(app.exec())
