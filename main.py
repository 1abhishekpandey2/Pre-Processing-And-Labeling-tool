import sys
import csv
import pandas as pd
import numpy as np
import re # --- NEW IMPORT for currency parsing ---
from PyQt5 import QtWidgets, uic, QtCore
from PyQt5.QtWidgets import (QFileDialog, QTableWidgetItem, QPushButton, 
                             QMessageBox, QMenu, QUndoStack, QUndoCommand, QInputDialog)
from PyQt5.QtGui import QColor

# --- MODIFIED: BulkChangeCommand now stores BOTH highlight sets ---
class BulkChangeCommand(QUndoCommand):
    """An undo command that stores the entire DataFrame AND highlight states."""
    def __init__(self, main_window, old_df, new_df, 
                 old_cells, new_cells, 
                 old_assumed_cells, new_assumed_cells, message):
        super().__init__(message)
        self.main_window = main_window
        self.old_df = old_df
        self.new_df = new_df
        self.old_cells = old_cells
        self.new_cells = new_cells
        self.old_assumed_cells = old_assumed_cells
        self.new_assumed_cells = new_assumed_cells
        self.message = message
        self.first_run = True 

    def undo(self):
        """Reverts to the old state."""
        self.main_window.load_dataframe(self.old_df)
        self.main_window.changed_cells = self.old_cells.copy()
        self.main_window.assumed_cells = self.old_assumed_cells.copy()
        self.main_window.apply_highlights()
        self.main_window.update_stats(self.old_df) # Update stats
        self.main_window.statusBar().showMessage(f"Undo: {self.message}", 3000)

    def redo(self):
        """Applies the new state."""
        if self.first_run:
            self.first_run = False
            return
        self.main_window.load_dataframe(self.new_df)
        self.main_window.changed_cells = self.new_cells.copy()
        self.main_window.assumed_cells = self.new_assumed_cells.copy()
        self.main_window.apply_highlights()
        self.main_window.update_stats(self.new_df) # Update stats
        self.main_window.statusBar().showMessage(f"Redo: {self.message}", 3000)


class CSVEditor(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi("main.ui", self)
        
        self.HIGHLIGHT_COLOR = QColor(230, 240, 255) # Light blue
        # --- NEW: Highlight color for assumed currency conversions ---
        self.ASSUMED_HIGHLIGHT_COLOR = QColor(255, 249, 196) # Light yellow
        
        self.changed_cells = set() # Stores (row, col) tuples
        # --- NEW: Set for assumed highlights ---
        self.assumed_cells = set() 
        
        # --- NEW: Hardcoded conversion rates relative to USD ---
        self.CONVERSION_RATES = {
            "$": 1.0,      # US Dollar
            "₹": 0.012,    # Indian Rupee (~83 INR to 1 USD)
            "€": 1.08,     # Euro (~1 EUR to 1.08 USD)
            "£": 1.25,     # British Pound (~1 GBP to 1.25 USD)
        }

        # --- Undo Stack Setup ---
        self.undo_stack = QUndoStack(self)
        self.actionUndo.triggered.connect(self.undo_stack.undo)
        self.actionRedo.triggered.connect(self.undo_stack.redo)
        self.actionUndo.setEnabled(False)
        self.actionRedo.setEnabled(False)
        self.undo_stack.canUndoChanged.connect(self.actionUndo.setEnabled)
        self.undo_stack.canRedoChanged.connect(self.actionRedo.setEnabled)

        # Connect menu actions
        self.actionOpen_CSV.triggered.connect(self.load_csv)
        self.actionSave.triggered.connect(self.save_csv)

        # Class variables
        self.current_path = None
        self.hover_row = None
        self.hover_col = None
        self.header_selected_row = None
        self.header_selected_col = None
        self.current_selection = []
        self.current_edit_old_df = None

        # Floating buttons
        self.add_row_button = self.create_floating_button("+", "#4CAF50")
        self.add_col_button = self.create_floating_button("+", "#4CAF50")
        self.row_options_button = self.create_floating_button("...", "#0288D1", size=26)
        self.col_options_button = self.create_floating_button("...", "#0288D1", size=26)

        # Connect actions
        self.add_row_button.clicked.connect(self.add_row_at_hover)
        self.add_col_button.clicked.connect(self.add_col_at_hover)
        self.row_options_button.clicked.connect(self.show_row_menu)
        self.col_options_button.clicked.connect(self.show_col_menu)

        # Event tracking
        self.tableWidget.setMouseTracking(True)
        self.tableWidget.viewport().installEventFilter(self)
        self.tableWidget.horizontalHeader().sectionClicked.connect(self.show_col_options_button)
        self.tableWidget.verticalHeader().sectionClicked.connect(self.show_row_options_button)
        self.tableWidget.itemSelectionChanged.connect(self.hide_options_buttons)
        self.tableWidget.cellPressed.connect(self.on_cell_pressed)
        self.tableWidget.itemChanged.connect(self.on_item_changed)
        self.tableWidget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tableWidget.customContextMenuRequested.connect(self.show_cell_context_menu)

        self.show()

    # ---------------------- Helper ---------------------- #
    def create_floating_button(self, text, color, size=24):
        btn = QPushButton(text, self)
        btn.setFixedSize(size, size)
        btn.hide()
        font_style = "font-size: 16px; line-height: 10px;" if text == "..." else "font-size: 14px;"
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {color};
                color: white;
                border-radius: {size//2}px;
                font-weight: bold; {font_style}
            }}
            QPushButton:hover {{
                background-color: {'#388E3C' if color == '#4CAF50' else '#0277BD'};
            }}
        """)
        return btn
        
    def set_item_and_highlight(self, row, col, text, assumed=False):
        """Helper to create a new item, highlight it, and log the change."""
        item = QTableWidgetItem(str(text))
        if assumed:
            item.setBackground(self.ASSUMED_HIGHLIGHT_COLOR)
            self.assumed_cells.add((row, col))
        else:
            item.setBackground(self.HIGHLIGHT_COLOR)
            self.changed_cells.add((row, col))
        self.tableWidget.setItem(row, col, item)

    def apply_highlights(self):
        """Iterates the master list and highlights all changed cells."""
        self.tableWidget.blockSignals(True)
        # Apply yellow first
        for r, c in self.assumed_cells:
            if r < self.tableWidget.rowCount() and c < self.tableWidget.columnCount():
                item = self.tableWidget.item(r, c)
                if item:
                    item.setBackground(self.ASSUMED_HIGHLIGHT_COLOR)
        # Apply blue over yellow (blue takes precedence)
        for r, c in self.changed_cells:
            if r < self.tableWidget.rowCount() and c < self.tableWidget.columnCount():
                item = self.tableWidget.item(r, c)
                if item:
                    item.setBackground(self.HIGHLIGHT_COLOR)
        self.tableWidget.blockSignals(False)

    def get_dataframe(self):
        rows = self.tableWidget.rowCount()
        cols = self.tableWidget.columnCount()
        headers = [self.tableWidget.horizontalHeaderItem(c).text() 
                   if self.tableWidget.horizontalHeaderItem(c) else str(c) 
                   for c in range(cols)]
        data = []
        for r in range(rows):
            row_data = []
            for c in range(cols):
                item = self.tableWidget.item(r, c)
                row_data.append(item.text() if item and item.text() else np.nan)
            data.append(row_data)
        return pd.DataFrame(data, columns=headers)

    def update_stats(self, df):
        if df is None or df.empty:
            self.text_missing_stats.setText("Missing:\nN/A")
            return
        mis_values = df.isnull().sum()
        stats_text = "Missing Values:\n"
        mis_values = mis_values[mis_values > 0].sort_values(ascending=False).head(10)
        stats_text += "None!" if mis_values.empty else "\n".join([f"{col}: {count}" for col, count in mis_values.items()])
        self.text_missing_stats.setText(stats_text)
        
    def load_dataframe(self, df):
        """Loads the table from a DataFrame."""
        if df is None:
            print("Error: load_dataframe was called with None. Aborting load.")
            return
            
        self.tableWidget.blockSignals(True)
        self.tableWidget.clear() 
        df_filled = df.fillna('') 
        self.tableWidget.setRowCount(df_filled.shape[0])
        self.tableWidget.setColumnCount(df_filled.shape[1])
        self.tableWidget.setHorizontalHeaderLabels(df_filled.columns.astype(str).tolist())
        
        for r in range(df_filled.shape[0]):
            for c in range(df_filled.shape[1]):
                value = str(df_filled.iloc[r, c])
                item = QTableWidgetItem(value)
                self.tableWidget.setItem(r, c, item)
        
        self.tableWidget.blockSignals(False)
        self.hide_options_buttons()
        # Highlights and stats are now applied by the calling function (e.g., undo/redo)

    def on_cell_pressed(self, row, col):
        if not self.tableWidget.signalsBlocked():
            self.current_edit_old_df = self.get_dataframe()

    def on_item_changed(self, item):
        """Called when the user edits a cell."""
        if self.tableWidget.signalsBlocked() or not item or self.current_edit_old_df is None:
            return

        new_df = self.get_dataframe()
        
        try:
            old_val = self.current_edit_old_df.iloc[item.row(), item.column()]
        except IndexError:
            self.current_edit_old_df = None
            return

        if str(old_val if pd.notna(old_val) else '') == item.text():
            self.current_edit_old_df = None
            return

        item.setBackground(self.HIGHLIGHT_COLOR)
        
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        self.changed_cells.add((item.row(), item.column()))
        self.assumed_cells.discard((item.row(), item.column())) # Manual edit removes "assumed"
        
        new_cells = self.changed_cells.copy()
        new_assumed_cells = self.assumed_cells.copy()
        
        msg = f"Edit Cell ({item.row()}, {item.column()})"
        command = BulkChangeCommand(self, self.current_edit_old_df, new_df, 
                                    old_cells, new_cells, 
                                    old_assumed_cells, new_assumed_cells, msg)
        self.undo_stack.push(command)
        
        self.update_stats(new_df)
        self.current_edit_old_df = None
        
    # ---------------------- Menu Functions ---------------------- #
    
    def show_row_menu(self):
        menu = QMenu(self)
        delete_action = menu.addAction("Delete Row")
        menu.addSeparator()
        remove_highlight_action = menu.addAction("Remove Highlights in Row")
        
        delete_action.triggered.connect(self.delete_selected_row)
        remove_highlight_action.triggered.connect(self.remove_highlights_in_row)
        
        menu.exec_(self.row_options_button.mapToGlobal(QtCore.QPoint(0, self.row_options_button.height())))
        self.hide_options_buttons()

    def show_col_menu(self):
        """--- MODIFIED: Column menu with new 'Convert' and 'Find' ---"""
        menu = QMenu(self)

        impute_menu = menu.addMenu("Impute Missing")
        mean_action = impute_menu.addAction("By Mean")
        median_action = impute_menu.addAction("By Median")

        fill_menu = menu.addMenu("Fill Column")
        ffill_col_action = fill_menu.addAction("Forward Fill (↓)")
        bfill_col_action = fill_menu.addAction("Backward Fill (↑)")
        
        remove_nulls_action = menu.addAction("Remove Rows with Nulls")
        menu.addSeparator()
        
        # --- NEW: Convert Menu ---
        convert_menu = menu.addMenu("Convert")
        to_numeric_action = convert_menu.addAction("To Numeric Labels")
        currency_menu = convert_menu.addMenu("Convert Currency")
        to_inr_action = currency_menu.addAction("To Rupee (₹)")
        to_usd_action = currency_menu.addAction("To Dollar ($)")
        
        # --- NEW: Find & Replace ---
        menu.addSeparator()
        find_replace_action = menu.addAction("Find & Replace")
        rename_col_action = menu.addAction("Rename Column")
        
        menu.addSeparator()
        remove_highlight_action = menu.addAction("Remove Highlights in Column")
        
        menu.addSeparator()
        delete_col_action = menu.addAction("Delete Column")
        delete_col_action.setObjectName("deleteAction") # For styling

        # Connect actions
        mean_action.triggered.connect(self.impute_mean)
        median_action.triggered.connect(self.impute_median)
        ffill_col_action.triggered.connect(self.fill_col_forward)
        bfill_col_action.triggered.connect(self.fill_col_backward)
        remove_nulls_action.triggered.connect(self.remove_null_rows_in_col)
        
        to_numeric_action.triggered.connect(self.run_label_conversion_prompt)
        to_inr_action.triggered.connect(lambda: self.run_currency_conversion_prompt("₹", "Rupee"))
        to_usd_action.triggered.connect(lambda: self.run_currency_conversion_prompt("$", "Dollar"))
        
        find_replace_action.triggered.connect(self.run_find_replace_prompt)
        rename_col_action.triggered.connect(self.rename_selected_col)
        remove_highlight_action.triggered.connect(self.remove_highlights_in_col)
        delete_col_action.triggered.connect(self.delete_selected_col)

        menu.setStyleSheet("#deleteAction { color: red; }")
        menu.exec_(self.col_options_button.mapToGlobal(QtCore.QPoint(0, self.col_options_button.height())))
        self.hide_options_buttons()

    def show_cell_context_menu(self, pos):
        self.current_selection = self.tableWidget.selectedRanges()
        if not self.current_selection: return
        menu = QMenu(self)
        fill_menu = menu.addMenu("Fill")
        fill_down_action = fill_menu.addAction("Fill Down (from cell above)")
        fill_up_action = fill_menu.addAction("Fill Up (from cell below)")
        fill_down_action.triggered.connect(self.fill_selection_down)
        fill_up_action.triggered.connect(self.fill_selection_up)
        
        menu.addSeparator()
        remove_highlight_action = menu.addAction("Remove Highlights from Selection")
        remove_highlight_action.triggered.connect(self.remove_highlights_from_selection)

        can_fill_down = all(sel_range.topRow() > 0 for sel_range in self.current_selection)
        can_fill_up = all(sel_range.bottomRow() < self.tableWidget.rowCount() - 1 for sel_range in self.current_selection)
        fill_down_action.setEnabled(can_fill_down)
        fill_up_action.setEnabled(can_fill_up)
        
        menu.exec_(self.tableWidget.viewport().mapToGlobal(pos))

    # ---------------------- Preprocessing Actions ---------------------- #
    
    # --- NEW: Remove Highlight Functions ---
    def remove_highlights_from_selection(self):
        if not self.current_selection: return
        old_df = self.get_dataframe()
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        self.tableWidget.blockSignals(True)
        for sel_range in self.current_selection:
            for r in range(sel_range.topRow(), sel_range.bottomRow() + 1):
                for c in range(sel_range.leftColumn(), sel_range.rightColumn() + 1):
                    item = self.tableWidget.item(r, c)
                    if item:
                        item.setBackground(QColor("white"))
                    self.changed_cells.discard((r, c))
                    self.assumed_cells.discard((r, c))
        self.tableWidget.blockSignals(False)
        
        new_cells = self.changed_cells.copy()
        new_assumed_cells = self.assumed_cells.copy()
        msg = "Remove Highlights from Selection"
        command = BulkChangeCommand(self, old_df, old_df, old_cells, new_cells, old_assumed_cells, new_assumed_cells, msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage("Highlights removed from selection.", 3000)

    def remove_highlights_in_row(self):
        if self.header_selected_row is None: return
        old_df = self.get_dataframe()
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        row = self.header_selected_row
        
        self.tableWidget.blockSignals(True)
        cells_to_remove = set()
        assumed_cells_to_remove = set()
        
        for r, c in self.changed_cells:
            if r == row: cells_to_remove.add((r,c))
        for r, c in self.assumed_cells:
            if r == row: assumed_cells_to_remove.add((r,c))
            
        for r, c in cells_to_remove.union(assumed_cells_to_remove):
            item = self.tableWidget.item(r, c)
            if item: item.setBackground(QColor("white"))
                    
        self.changed_cells.difference_update(cells_to_remove)
        self.assumed_cells.difference_update(assumed_cells_to_remove)
        self.tableWidget.blockSignals(False)

        new_cells = self.changed_cells.copy()
        new_assumed_cells = self.assumed_cells.copy()
        msg = f"Remove Highlights in Row {row}"
        command = BulkChangeCommand(self, old_df, old_df, old_cells, new_cells, old_assumed_cells, new_assumed_cells, msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Highlights removed from row {row}.", 3000)

    def remove_highlights_in_col(self):
        if self.header_selected_col is None: return
        old_df = self.get_dataframe()
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        col = self.header_selected_col
        
        self.tableWidget.blockSignals(True)
        cells_to_remove = set()
        assumed_cells_to_remove = set()

        for r, c in self.changed_cells:
            if c == col: cells_to_remove.add((r,c))
        for r, c in self.assumed_cells:
            if c == col: assumed_cells_to_remove.add((r,c))

        for r, c in cells_to_remove.union(assumed_cells_to_remove):
            item = self.tableWidget.item(r, c)
            if item: item.setBackground(QColor("white"))
                    
        self.changed_cells.difference_update(cells_to_remove)
        self.assumed_cells.difference_update(assumed_cells_to_remove)
        self.tableWidget.blockSignals(False)
        
        new_cells = self.changed_cells.copy()
        new_assumed_cells = self.assumed_cells.copy()
        msg = f"Remove Highlights in Column {col}"
        command = BulkChangeCommand(self, old_df, old_df, old_cells, new_cells, old_assumed_cells, new_assumed_cells, msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Highlights removed from column {col}.", 3000)

    def rename_selected_col(self):
        if self.header_selected_col is None: return
        old_df = self.get_dataframe()
        old_name = old_df.columns[self.header_selected_col]
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()

        new_name, ok = QInputDialog.getText(self, "Rename Column", "Enter new column name:", QtWidgets.QLineEdit.Normal, old_name)
        if not ok or not new_name or new_name == old_name: return
        if new_name in old_df.columns:
            QMessageBox.critical(self, "Error", f"A column named '{new_name}' already exists.")
            return

        new_df = old_df.copy()
        new_df.rename(columns={old_name: new_name}, inplace=True)
        
        self.load_dataframe(new_df) 
        self.apply_highlights()
        self.update_stats(new_df)

        msg = f"Rename Column '{old_name}' to '{new_name}'"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, old_cells, old_assumed_cells, old_assumed_cells, msg) # Highlights don't change
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Renamed column to '{new_name}'")
        self.header_selected_col = None
        
    def impute_mean(self):
        if self.header_selected_col is None: return
        old_df = self.get_dataframe()
        new_df = old_df.copy()
        col_name = new_df.columns[self.header_selected_col]
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        try:
            numeric_col = pd.to_numeric(new_df[col_name], errors='coerce')
            mean_val = numeric_col.mean()
            if pd.isna(mean_val):
                 self.statusBar().showMessage(f"Cannot calculate mean for non-numeric column '{col_name}'", 3000)
                 return
            
            for r, val in enumerate(numeric_col):
                if pd.isna(val):
                    new_df.iloc[r, self.header_selected_col] = mean_val
                    self.changed_cells.add((r, self.header_selected_col))
                    self.assumed_cells.discard((r, self.header_selected_col))
            
            self.load_dataframe(new_df)
            self.apply_highlights()
            self.update_stats(new_df)
            
            msg = f"Impute Mean on '{col_name}'"
            command = BulkChangeCommand(self, old_df, new_df, old_cells, self.changed_cells.copy(), old_assumed_cells, self.assumed_cells.copy(), msg)
            self.undo_stack.push(command)
            self.statusBar().showMessage(f"Imputed column '{col_name}' with mean: {mean_val:.2f}", 3000)
        except Exception as e:
            self.statusBar().showMessage(f"Error imputing mean: {e}", 5000)

    def impute_median(self):
        if self.header_selected_col is None: return
        old_df = self.get_dataframe()
        new_df = old_df.copy()
        col_name = new_df.columns[self.header_selected_col]
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        try:
            numeric_col = pd.to_numeric(new_df[col_name], errors='coerce')
            median_val = numeric_col.median()
            if pd.isna(median_val):
                 self.statusBar().showMessage(f"Cannot calculate median for non-numeric column '{col_name}'", 3000)
                 return

            for r, val in enumerate(numeric_col):
                if pd.isna(val):
                    new_df.iloc[r, self.header_selected_col] = median_val
                    self.changed_cells.add((r, self.header_selected_col))
                    self.assumed_cells.discard((r, self.header_selected_col))

            self.load_dataframe(new_df)
            self.apply_highlights()
            self.update_stats(new_df)
            
            msg = f"Impute Median on '{col_name}'"
            command = BulkChangeCommand(self, old_df, new_df, old_cells, self.changed_cells.copy(), old_assumed_cells, self.assumed_cells.copy(), msg)
            self.undo_stack.push(command)
            self.statusBar().showMessage(f"Imputed column '{col_name}' with median: {median_val}", 3000)
        except Exception as e:
            self.statusBar().showMessage(f"Error imputing median: {e}", 5000)

    def fill_col_forward(self):
        if self.header_selected_col is None: return
        old_df = self.get_dataframe()
        new_df = old_df.copy()
        col_name = new_df.columns[self.header_selected_col]
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        col_data = new_df[col_name]
        for r, val in enumerate(col_data):
            if pd.isna(val):
                if r > 0 and pd.notna(col_data.iloc[r-1]):
                    self.changed_cells.add((r, self.header_selected_col))
                    self.assumed_cells.discard((r, self.header_selected_col))
        
        new_df[col_name] = new_df[col_name].ffill()
        self.load_dataframe(new_df)
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = f"Fill Column Forward '{col_name}'"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, self.changed_cells.copy(), old_assumed_cells, self.assumed_cells.copy(), msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Forward-filled column '{col_name}'", 3000)
        
    def fill_col_backward(self):
        if self.header_selected_col is None: return
        old_df = self.get_dataframe()
        new_df = old_df.copy()
        col_name = new_df.columns[self.header_selected_col]
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()

        col_data = new_df[col_name]
        for r in range(len(col_data) - 1, -1, -1):
            if pd.isna(col_data.iloc[r]):
                if r < len(col_data) - 1 and pd.notna(col_data.iloc[r+1]):
                    self.changed_cells.add((r, self.header_selected_col))
                    self.assumed_cells.discard((r, self.header_selected_col))
        
        new_df[col_name] = new_df[col_name].bfill()
        self.load_dataframe(new_df)
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = f"Fill Column Backward '{col_name}'"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, self.changed_cells.copy(), old_assumed_cells, self.assumed_cells.copy(), msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Backward-filled column '{col_name}'", 3000)
        
    def remove_null_rows_in_col(self):
        if self.header_selected_col is None: return
        old_df = self.get_dataframe()
        col_name = old_df.columns[self.header_selected_col]
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()

        new_df = old_df.dropna(subset=[col_name]).reset_index(drop=True)
        # Clear highlights - re-calculating is too complex for row deletions
        self.changed_cells.clear() 
        self.assumed_cells.clear()
        
        self.load_dataframe(new_df)
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = f"Remove Null Rows in '{col_name}'"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, self.changed_cells.copy(), old_assumed_cells, self.assumed_cells.copy(), msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Removed {len(old_df) - len(new_df)} rows", 3000)

    def fill_selection_down(self):
        if not self.current_selection: return 
        old_df = self.get_dataframe()
        new_df = old_df.copy()
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()

        for sel_range in self.current_selection:
            top_row = sel_range.topRow()
            if top_row == 0: continue 
            for c in range(sel_range.leftColumn(), sel_range.rightColumn() + 1):
                value_to_fill = new_df.iloc[top_row - 1, c]
                for r in range(top_row, sel_range.bottomRow() + 1):
                    new_df.iloc[r, c] = value_to_fill
                    self.changed_cells.add((r, c))
                    self.assumed_cells.discard((r, c))
        
        self.load_dataframe(new_df)
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = "Fill Selection Down"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, self.changed_cells.copy(), old_assumed_cells, self.assumed_cells.copy(), msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Filled selection down.", 3000)
        
    def fill_selection_up(self):
        if not self.current_selection: return 
        old_df = self.get_dataframe()
        new_df = old_df.copy()
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        for sel_range in self.current_selection:
            bottom_row = sel_range.bottomRow()
            if bottom_row == self.tableWidget.rowCount() - 1: continue
            for c in range(sel_range.leftColumn(), sel_range.rightColumn() + 1):
                value_to_fill = new_df.iloc[bottom_row + 1, c]
                for r in range(sel_range.topRow(), sel_range.bottomRow() + 1):
                    new_df.iloc[r, c] = value_to_fill
                    self.changed_cells.add((r, c))
                    self.assumed_cells.discard((r, c))

        self.load_dataframe(new_df)
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = "Fill Selection Up"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, self.changed_cells.copy(), old_assumed_cells, self.assumed_cells.copy(), msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Filled selection up.", 3000)

    def run_label_conversion_prompt(self):
        if self.header_selected_col is None: return
        self.convert_to_numeric_labels(self.header_selected_col)

    def convert_to_numeric_labels(self, col_index):
        if col_index is None: return
        old_df = self.get_dataframe()
        new_df = old_df.copy()
        col_name = new_df.columns[col_index]
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        try:
            # --- FIX: Use .str.lower() for case-insensitivity ---
            codes, uniques = pd.factorize(new_df[col_name].fillna('').astype(str).str.lower())
            new_df[col_name] = codes
            
            for r in range(len(new_df)):
                self.changed_cells.add((r, col_index))
                self.assumed_cells.discard((r, col_index))
            
            self.load_dataframe(new_df)
            self.apply_highlights()
            self.update_stats(new_df)
            
            msg = f"Convert '{col_name}' to Numeric"
            command = BulkChangeCommand(self, old_df, new_df, old_cells, self.changed_cells.copy(), old_assumed_cells, self.assumed_cells.copy(), msg)
            self.undo_stack.push(command)
            self.statusBar().showMessage(f"Converted '{col_name}' to {len(uniques)} labels.", 5000)
        except Exception as e:
            self.statusBar().showMessage(f"Error converting labels: {e}", 5000)
            
    # --- NEW: Currency Conversion Functions ---
    
    def extract_currency_value(self, value_str):
        """Extracts symbol and numeric value from a string."""
        if value_str is None or pd.isna(value_str):
            return None, None, False # Value, Symbol, HasSymbol

        value_str = str(value_str).strip()
        
        # 1. Check for known symbols
        found_symbol = None
        for symbol in self.CONVERSION_RATES:
            if value_str.startswith(symbol) or value_str.endswith(symbol):
                found_symbol = symbol
                break
        
        # 2. Extract numeric part
        # This regex removes symbols, commas, and keeps the decimal part
        numeric_part = re.sub(r"[^0-9.-]", "", value_str)
        if not numeric_part:
            return None, None, False
            
        try:
            value = float(numeric_part)
        except ValueError:
            return None, None, False # Not a valid number
            
        if found_symbol:
            return value, found_symbol, True
        else:
            # No symbol found, return value and assume base currency
            return value, "$", False # Assume base currency ($)

    def run_currency_conversion_prompt(self, target_symbol, target_name):
        if self.header_selected_col is None: return
        col_name = self.tableWidget.horizontalHeaderItem(self.header_selected_col).text()
        reply = QMessageBox.question(self, 'Convert Currency',
            f"Convert all currency values in column '{col_name}' to {target_name} ({target_symbol})?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.convert_to_currency(target_symbol, target_name)
            
    def convert_to_currency(self, target_symbol, target_name):
        if self.header_selected_col is None: return
        
        old_df = self.get_dataframe()
        new_df = old_df.copy()
        col_name = new_df.columns[self.header_selected_col]
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        col = self.header_selected_col
        target_rate = self.CONVERSION_RATES[target_symbol]
        
        something_changed = False
        
        for r in range(len(new_df)):
            value_str = new_df.iloc[r, col]
            value, symbol, has_symbol = self.extract_currency_value(value_str)
            
            if value is None:
                continue # Skip cells that aren't currency
                
            from_rate = self.CONVERSION_RATES.get(symbol, 1.0) # Default to 1.0
            
            # (Value in Base) = value * from_rate
            # (New Value) = (Value in Base) / target_rate
            converted_value = (value * from_rate) / target_rate
            new_value_str = f"{target_symbol}{converted_value:,.2f}"
            
            # Update the DataFrame
            new_df.iloc[r, col] = new_value_str
            
            # Log highlights
            if has_symbol:
                self.changed_cells.add((r, col))
                self.assumed_cells.discard((r, col))
            else:
                self.assumed_cells.add((r, col)) # No symbol, so it's "assumed"
                self.changed_cells.discard((r, col))
            something_changed = True

        if not something_changed:
            self.statusBar().showMessage(f"No convertible currency values found in '{col_name}'.", 3000)
            return
            
        self.load_dataframe(new_df)
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = f"Convert '{col_name}' to {target_name}"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, self.changed_cells.copy(), old_assumed_cells, self.assumed_cells.copy(), msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Converted column '{col_name}' to {target_name}.", 5000)

    # --- NEW: Find & Replace Functions ---
    def run_find_replace_prompt(self):
        if self.header_selected_col is None: return
        
        find_text, ok1 = QInputDialog.getText(self, "Find & Replace", "Text to find:")
        if not ok1: return
        
        replace_text, ok2 = QInputDialog.getText(self, "Find & Replace", f"Replace '{find_text}' with:")
        if not ok2: return
            
        self.find_and_replace_in_col(find_text, replace_text)

    def find_and_replace_in_col(self, find_text, replace_text):
        if self.header_selected_col is None or find_text == replace_text:
            return
            
        old_df = self.get_dataframe()
        new_df = old_df.copy()
        col_name = new_df.columns[self.header_selected_col]
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        col = self.header_selected_col
        changes_made = 0
        
        # We must iterate to log highlights
        for r in range(len(new_df)):
            cell_value = str(new_df.iloc[r, col] if pd.notna(new_df.iloc[r, col]) else "")
            if find_text in cell_value:
                new_value = cell_value.replace(find_text, replace_text)
                new_df.iloc[r, col] = new_value
                self.changed_cells.add((r, col))
                self.assumed_cells.discard((r, col))
                changes_made += 1
                
        if changes_made == 0:
            self.statusBar().showMessage(f"'{find_text}' not found in column.", 3000)
            return

        self.load_dataframe(new_df)
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = f"Replace '{find_text}' with '{replace_text}'"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, self.changed_cells.copy(), old_assumed_cells, self.assumed_cells.copy(), msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Made {changes_made} replacements.", 3000)

    # ---------------------- CSV Loading / Saving ---------------------- #
    def load_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV Files (*.csv)")
        if not path: return
        try:
            df = pd.read_csv(path)
            self.load_dataframe(df) # Loads without highlights
            self.update_stats(df)
            self.current_path = path
            self.statusBar().showMessage(f"Rows: {df.shape[0]}, Columns: {df.shape[1]} Loaded: {path}")
            self.undo_stack.clear()
            self.changed_cells.clear()
            self.assumed_cells.clear() # Clear new set
        except Exception as e:
            self.statusBar().showMessage(f"Error loading CSV: {e}", 5000)

    def save_csv(self):
        if not self.current_path:
            path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV Files (*.csv)")
            if not path: return
        else:
            path = self.current_path
        df = self.get_dataframe()
        df.columns = [self.tableWidget.horizontalHeaderItem(c).text() if self.tableWidget.horizontalHeaderItem(c) else "" for c in range(df.shape[1])]
        try:
            df.to_csv(path, index=False, na_rep='')
            self.statusBar().showMessage(f"Rows: {df.shape[0]}, Columns: {df.shape[1]} Saved: {path}")
            self.undo_stack.clear()
            self.changed_cells.clear()
            self.assumed_cells.clear() # Clear new set
            self.load_dataframe(df) # Reload to clear highlights
            self.update_stats(df)
        except Exception as e:
            self.statusBar().showMessage(f"Error saving file: {e}", 5000)

    # ---------------------- Hover Detection (Unchanged) ---------------------- #
    def eventFilter(self, source, event):
        if source == self.tableWidget.viewport():
            if event.type() == event.MouseMove:
                index = self.tableWidget.indexAt(event.pos())
                if index.isValid():
                    rect = self.tableWidget.visualRect(index)
                    x, y = event.pos().x(), event.pos().y()
                    if abs(y - rect.bottom()) < 8:
                        self.hover_row = index.row()
                        global_pos = self.tableWidget.viewport().mapToGlobal(rect.bottomLeft())
                        table_pos = self.mapFromGlobal(global_pos)
                        self.add_row_button.move(table_pos.x() + rect.width() - 10, table_pos.y())
                        self.add_row_button.show()
                    else: self.add_row_button.hide()
                    if abs(x - rect.right()) < 8:
                        self.hover_col = index.column()
                        global_pos = self.tableWidget.viewport().mapToGlobal(rect.topRight())
                        table_pos = self.mapFromGlobal(global_pos)
                        self.add_col_button.move(table_pos.x(), table_pos.y() + rect.height() // 2)
                        self.add_col_button.show()
                    else: self.add_col_button.hide()
                else:
                    self.add_row_button.hide()
                    self.add_col_button.hide()
        return super().eventFilter(source, event)

    # ---------------------- Add / Delete Actions ---------------------- #
    def add_row_at_hover(self):
        if self.hover_row is None: return
        old_df = self.get_dataframe()
        new_row = pd.Series([np.nan] * len(old_df.columns), index=old_df.columns)
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        new_df = pd.concat([
            old_df.iloc[:self.hover_row + 1], 
            pd.DataFrame(new_row).T, 
            old_df.iloc[self.hover_row + 1:]
        ]).reset_index(drop=True)
        
        # Shift highlights down
        new_cells = set()
        for r, c in old_cells:
            new_cells.add((r + 1, c)) if r > self.hover_row else new_cells.add((r, c))
        new_assumed_cells = set()
        for r, c in old_assumed_cells:
            new_assumed_cells.add((r + 1, c)) if r > self.hover_row else new_assumed_cells.add((r, c))
            
        for c in range(new_df.shape[1]):
            new_cells.add((self.hover_row + 1, c)) # Highlight new row
        self.changed_cells = new_cells
        self.assumed_cells = new_assumed_cells
        
        self.load_dataframe(new_df) 
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = f"Insert Row at {self.hover_row + 1}"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, new_cells, old_assumed_cells, new_assumed_cells, msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Inserted new row after {self.hover_row}")
        self.add_row_button.hide()

    def add_col_at_hover(self):
        if self.hover_col is None: return
        old_df = self.get_dataframe()
        new_df = old_df.copy()
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()
        
        new_col_name = "New_Column"
        i = 1
        while new_col_name in new_df.columns:
            new_col_name = f"New_Column_{i}"; i += 1
            
        new_df.insert(self.hover_col + 1, new_col_name, np.nan)
        
        # Shift highlights right
        new_cells = set()
        for r, c in old_cells:
            new_cells.add((r, c + 1)) if c > self.hover_col else new_cells.add((r, c))
        new_assumed_cells = set()
        for r, c in old_assumed_cells:
            new_assumed_cells.add((r, c + 1)) if c > self.hover_col else new_assumed_cells.add((r, c))

        for r in range(new_df.shape[0]):
            new_cells.add((r, self.hover_col + 1)) # Highlight new column
        self.changed_cells = new_cells
        self.assumed_cells = new_assumed_cells
        
        self.load_dataframe(new_df)
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = f"Insert Column at {self.hover_col + 1}"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, new_cells, old_assumed_cells, new_assumed_cells, msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Inserted new column after {self.hover_col}")
        self.add_col_button.hide()

    def show_row_options_button(self, row_index):
        rect = self.tableWidget.visualRect(self.tableWidget.model().index(row_index, 0))
        global_pos = self.tableWidget.viewport().mapToGlobal(rect.topLeft())
        table_pos = self.mapFromGlobal(global_pos)
        self.row_options_button.move(table_pos.x() - 30, table_pos.y() + rect.height() // 2 - 12)
        self.row_options_button.show()
        self.header_selected_row = row_index 

    def show_col_options_button(self, col_index):
        rect = self.tableWidget.visualRect(self.tableWidget.model().index(0, col_index))
        global_pos = self.tableWidget.viewport().mapToGlobal(rect.topLeft())
        table_pos = self.mapFromGlobal(global_pos)
        self.col_options_button.move(table_pos.x() + rect.width() // 2 - 12, table_pos.y() - 30)
        self.col_options_button.show()
        self.header_selected_col = col_index 

    def hide_options_buttons(self):
        self.row_options_button.hide()
        self.col_options_button.hide()

    def delete_selected_row(self):
        if self.header_selected_row is None: return
        old_df = self.get_dataframe()
        new_df = old_df.drop(self.header_selected_row).reset_index(drop=True)
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()

        # Shift highlights up
        new_cells = set()
        for r, c in old_cells:
            if r < self.header_selected_row: new_cells.add((r, c))
            elif r > self.header_selected_row: new_cells.add((r - 1, c))
        new_assumed_cells = set()
        for r, c in old_assumed_cells:
            if r < self.header_selected_row: new_assumed_cells.add((r, c))
            elif r > self.header_selected_row: new_assumed_cells.add((r - 1, c))
            
        self.changed_cells = new_cells
        self.assumed_cells = new_assumed_cells
        
        self.load_dataframe(new_df) 
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = f"Delete Row {self.header_selected_row}"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, new_cells, old_assumed_cells, new_assumed_cells, msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Deleted row {self.header_selected_row}")
        self.header_selected_row = None 

    def delete_selected_col(self):
        if self.header_selected_col is None: return
        old_df = self.get_dataframe()
        col_name = old_df.columns[self.header_selected_col]
        new_df = old_df.drop(columns=[col_name])
        old_cells = self.changed_cells.copy()
        old_assumed_cells = self.assumed_cells.copy()

        # Shift highlights left
        new_cells = set()
        for r, c in old_cells:
            if c < self.header_selected_col: new_cells.add((r, c))
            elif c > self.header_selected_col: new_cells.add((r, c - 1))
        new_assumed_cells = set()
        for r, c in old_assumed_cells:
            if c < self.header_selected_col: new_assumed_cells.add((r, c))
            elif c > self.header_selected_col: new_assumed_cells.add((r, c - 1))
            
        self.changed_cells = new_cells
        self.assumed_cells = new_assumed_cells

        self.load_dataframe(new_df)
        self.apply_highlights()
        self.update_stats(new_df)
        
        msg = f"Delete Column '{col_name}'"
        command = BulkChangeCommand(self, old_df, new_df, old_cells, new_cells, old_assumed_cells, new_assumed_cells, msg)
        self.undo_stack.push(command)
        self.statusBar().showMessage(f"Deleted column {col_name}")
        self.header_selected_col = None


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = CSVEditor()
    sys.exit(app.exec_())