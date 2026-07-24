"""
Опростен графичен интерфейс за редактиране на config.py
Позволява промяна на най-важните настройки без ръчна редакция на файла.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import copy
import sys
import os
import importlib
import ipaddress
import json
import re
import shutil
import socket
import tempfile
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# Добавяме текущата директория в path
if getattr(sys, 'frozen', False):
    _base_dir = os.path.dirname(sys.executable)
else:
    _base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _base_dir)
import config
from web_gui_auth import CredentialStore, CredentialStoreError, CredentialValidationError


_WEB_GUI_PASSWORD_MIN_LENGTH = 8


def _validate_web_gui_credential_form(username, password, confirmation):
    """Validate credential form data without changing password characters."""
    normalized_username = str(username or "").strip()
    if not normalized_username:
        raise ValueError("Попълни потребителско име.")
    if password == "":
        raise ValueError("Попълни парола.")
    if password != confirmation:
        raise ValueError("Паролата и потвърждението не съвпадат.")
    if len(password) < _WEB_GUI_PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Паролата трябва да е поне {_WEB_GUI_PASSWORD_MIN_LENGTH} знака."
        )
    return normalized_username, password


class ConfigGUI:
    """Графичен интерфейс за config.py"""

    SOLVER_LABELS = {
        "pyvrp": "PyVRP 0.13",
        "pyvrp_experimental": "PyVRP 0.14",
        "or_tools": "OR-Tools",
        "vroom": "VROOM 1.15",
        "vrp": "VRP-Rust (experimental)",
    }
    SOLVER_VALUES_BY_LABEL = {label: value for value, label in SOLVER_LABELS.items()}

    SECTION_LABELS = {
        "input": "Входни данни",
        "vehicles": "Превозни средства",
        "warehouse": "Предварителна оптимизация",
        "cvrp": "Решител (CVRP)",
        "locations": "Локации и зони",
        "output": "Резултати",
        "advanced": "Разширени",
    }

    SECTION_NAVIGATION = (
        ("01", "Входни данни", "Източник, JSON и Excel"),
        ("02", "Превозни средства", "Капацитети, депа и смени"),
        ("03", "Предварителна оптимизация", "Склад и филтриране"),
        ("04", "Решител", "Цел, ограничения и PyVRP"),
        ("05", "Локации и зони", "Депа, трафик и център"),
        ("06", "Резултати", "Карти, Excel и upload"),
        ("07", "Разширени", "API, setData и автоматизация"),
    )

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CVRP Настройки")
        self.root.geometry("1320x860")
        self.root.minsize(1120, 720)
        self.root.resizable(True, True)
        self._configure_style()

        # Зареждаме текущата конфигурация
        importlib.reload(config)
        self.cfg = config.get_config()
        self.widgets = {}  # field_key → widget
        self.controls = {}  # field_key → visible input control
        self.control_default_states = {}
        self.status_var = tk.StringVar(value="Настройките са заредени")
        self.dirty_var = tk.StringVar(value="Всички промени са запазени")
        self.validation_var = tk.StringVar(value="Не е проверено")
        self.is_dirty = False
        self._tracking_ready = False
        self._saved_snapshot = {}
        self.main_notebook = None
        self.nav_buttons = []
        self.setting_search_var = tk.StringVar(value="")
        self.save_button = None
        self.reset_button = None
        self.vehicle_container = None
        self.vehicle_tree = None
        self.vehicle_next_index = 0
        self.depot_choice_widgets = []
        self.depot_listbox = None
        self.depot_tree = None
        self.traffic_zone_listbox = None
        self.traffic_zone_tree = None
        self.center_zone_listbox = None
        self.center_zone_tree = None
        self.center_zone_priority_vars = {}
        self.center_zone_restricted_vars = {}
        self.solver_fine_panels = {}
        self.solver_fine_anchor = None
        self.pyvrp_parallel_specific = None
        self.ortools_parallel_specific = None
        self._web_gui_credential_store = None
        self.web_gui_users_tree = None
        self.web_gui_users_status_var = None

        self._build_ui()
        self._saved_snapshot = copy.deepcopy(self._collect_values())
        self._setup_dirty_tracking()
        self._setup_conditional_controls()
        self._set_dirty(False)
        self.root.protocol("WM_DELETE_WINDOW", self._request_close)
        self.root.bind("<Control-s>", lambda event: self._save())
        self.root.bind("<Control-Return>", lambda event: self._validate_from_ui())
        self.root.bind("<F5>", lambda event: self._validate_from_ui())
        self.root.bind_all("<MouseWheel>", self._dispatch_mousewheel, add="+")
        for index in range(len(self.SECTION_NAVIGATION)):
            self.root.bind(
                f"<Alt-Key-{index + 1}>",
                lambda event, section_index=index: self._select_section(section_index),
            )

    # ── UI ────────────────────────────────────────────────────

    def _configure_style(self):
        self.root.configure(bg="#f3f6fb")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.root.option_add("*Font", ("Segoe UI", 9))
        style.configure("TFrame", background="#f3f6fb")
        style.configure("Toolbar.TFrame", background="#ffffff")
        style.configure("Surface.TFrame", background="#ffffff")
        style.configure("Sidebar.TFrame", background="#172033")
        style.configure("TLabel", background="#f3f6fb", foreground="#243044")
        style.configure("Surface.TLabel", background="#ffffff", foreground="#243044")
        style.configure("Hint.TLabel", background="#ffffff", foreground="#68758a", font=("Segoe UI", 8))
        style.configure("Status.TLabel", background="#ffffff", foreground="#526077", font=("Segoe UI", 9))
        style.configure("Dirty.TLabel", background="#ffffff", foreground="#b45309", font=("Segoe UI", 9, "bold"))
        style.configure("Clean.TLabel", background="#ffffff", foreground="#15803d", font=("Segoe UI", 9, "bold"))
        style.configure("SidebarTitle.TLabel", background="#172033", foreground="#ffffff", font=("Segoe UI", 10, "bold"))
        style.configure("SidebarHint.TLabel", background="#172033", foreground="#9eabc0", font=("Segoe UI", 8))
        style.configure("AppTitle.TLabel", background="#ffffff", foreground="#101828", font=("Segoe UI", 17, "bold"))
        style.configure("AppSubtitle.TLabel", background="#ffffff", foreground="#68758a", font=("Segoe UI", 9))
        style.configure("TLabelframe", background="#ffffff", bordercolor="#d7deea", relief="solid")
        style.configure("TLabelframe.Label", background="#ffffff", foreground="#172033", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(11, 7), font=("Segoe UI", 9))
        style.configure("Primary.TButton", padding=(14, 8), foreground="#ffffff", background="#2563eb", bordercolor="#2563eb")
        style.map(
            "Primary.TButton",
            background=[("disabled", "#9db8f2"), ("active", "#1d4ed8"), ("pressed", "#1e40af")],
            foreground=[("disabled", "#eef4ff"), ("!disabled", "#ffffff")],
        )
        style.configure("Secondary.TButton", foreground="#243044", background="#ffffff", bordercolor="#cbd5e1")
        style.map("Secondary.TButton", background=[("active", "#f1f5f9"), ("pressed", "#e2e8f0")])
        style.configure("Danger.TButton", foreground="#b42318", background="#fff5f4", bordercolor="#f5b7b1")
        style.map("Danger.TButton", background=[("active", "#fee4e2"), ("pressed", "#fecdca")])
        style.configure("Nav.TButton", anchor="w", padding=(14, 9), foreground="#cbd5e1", background="#172033", borderwidth=0)
        style.configure("NavSelected.TButton", anchor="w", padding=(14, 9), foreground="#ffffff", background="#2563eb", borderwidth=0)
        style.map("Nav.TButton", background=[("active", "#263249")], foreground=[("active", "#ffffff")])
        style.map("NavSelected.TButton", background=[("active", "#1d4ed8")])
        style.configure("TEntry", padding=(7, 5), fieldbackground="#ffffff", bordercolor="#cbd5e1")
        style.configure("TCombobox", padding=(7, 5), fieldbackground="#ffffff", bordercolor="#cbd5e1")
        style.configure("Invalid.TEntry", padding=(7, 5), fieldbackground="#fff7f6", bordercolor="#d92d20")
        style.configure("Invalid.TCombobox", padding=(7, 5), fieldbackground="#fff7f6", bordercolor="#d92d20")
        style.configure("TCheckbutton", background="#ffffff", foreground="#243044")
        style.map("TCheckbutton", background=[("active", "#ffffff")])
        style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", foreground="#243044", rowheight=28, bordercolor="#d7deea")
        style.configure("Treeview.Heading", background="#eef2f7", foreground="#344054", font=("Segoe UI", 9, "bold"), padding=(6, 7))
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#1e3a8a")])
        style.configure("TNotebook", background="#f3f6fb", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#111827")])
        style.configure("Sidebar.TNotebook", background="#f3f6fb", borderwidth=0, tabmargins=0)
        style.layout("Sidebar.TNotebook.Tab", [])

    def _build_ui(self):
        toolbar = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(18, 12))
        toolbar.pack(fill="x")

        title_box = ttk.Frame(toolbar, style="Toolbar.TFrame")
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="CVRP Control Center", style="AppTitle.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text="Единно място за входни данни, автопарк, оптимизация, зони, API и резултати.",
            style="AppSubtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        actions = ttk.Frame(toolbar, style="Toolbar.TFrame")
        actions.pack(side="right")
        ttk.Button(actions, text="Провери", command=self._validate_from_ui, style="Secondary.TButton").pack(side="left", padx=3)
        self.reset_button = ttk.Button(actions, text="Отмени промените", command=self._reset_changes, style="Secondary.TButton")
        self.reset_button.pack(side="left", padx=3)
        self.save_button = ttk.Button(actions, text="Запази", command=self._save, style="Primary.TButton")
        self.save_button.pack(side="left", padx=(10, 3))
        ttk.Button(actions, text="Запази и затвори", command=self._save_and_close, style="Secondary.TButton").pack(side="left", padx=3)
        ttk.Button(actions, text="Запази и стартирай", command=self._save_and_run, style="Secondary.TButton").pack(side="left", padx=3)
        ttk.Button(actions, text="Затвори", command=self._request_close, style="Danger.TButton").pack(side="left", padx=(10, 0))

        workspace = ttk.Frame(self.root)
        workspace.pack(fill="both", expand=True)

        sidebar = ttk.Frame(workspace, style="Sidebar.TFrame", width=230, padding=(12, 16))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="НАСТРОЙКИ", style="SidebarTitle.TLabel").pack(anchor="w", padx=8, pady=(0, 2))
        ttk.Label(sidebar, text="Alt+1 … Alt+7 за бърза навигация", style="SidebarHint.TLabel").pack(anchor="w", padx=8, pady=(0, 14))

        content = ttk.Frame(workspace)
        content.pack(side="left", fill="both", expand=True, padx=(12, 12), pady=(12, 8))

        nb = ttk.Notebook(content, style="Sidebar.TNotebook")
        nb.pack(fill="both", expand=True)
        self.main_notebook = nb

        # ─── Tab 1: Входни данни ───
        self._add_input_tab(nb)

        # ─── Tab 2: Превозни средства ───
        self._add_vehicles_tab(nb)

        # ─── Tab 3: Склад ───
        self._add_warehouse_tab(nb)

        # ─── Tab 4: Солвър ───
        self._add_solver_tab(nb)

        # ─── Tab 5: Локации ───
        self._add_locations_tab(nb)

        # ─── Tab 6: Изходни данни ───
        self._add_output_tab(nb)

        # ─── Last: Advanced/reference tools ───
        self._add_advanced_tab(nb)

        for index, (number, title, subtitle) in enumerate(self.SECTION_NAVIGATION):
            button = ttk.Button(
                sidebar,
                text=f"{number}   {title}\n       {subtitle}",
                style="Nav.TButton",
                command=lambda section_index=index: self._select_section(section_index),
            )
            button.pack(fill="x", pady=2)
            self.nav_buttons.append(button)
        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", padx=8, pady=(18, 12))
        ttk.Label(sidebar, text="НАМЕРИ НАСТРОЙКА", style="SidebarTitle.TLabel").pack(anchor="w", padx=8, pady=(0, 6))
        search_combo = ttk.Combobox(
            sidebar,
            textvariable=self.setting_search_var,
            values=sorted(self.controls),
            state="normal",
            width=26,
        )
        search_combo.pack(fill="x", padx=8)
        search_combo.bind("<<ComboboxSelected>>", self._jump_to_setting)
        search_combo.bind("<Return>", self._jump_to_setting)
        ttk.Label(
            sidebar,
            text="Въведи част от името и натисни Enter.",
            style="SidebarHint.TLabel",
        ).pack(anchor="w", padx=8, pady=(5, 0))
        self._select_section(0)

        status_bar = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(18, 8))
        status_bar.pack(fill="x", side="bottom")
        ttk.Label(status_bar, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        ttk.Label(
            status_bar,
            text=os.path.join(_base_dir, "config.py"),
            style="Status.TLabel",
        ).pack(side="right")
        self.dirty_label = ttk.Label(status_bar, textvariable=self.dirty_var, style="Clean.TLabel")
        self.dirty_label.pack(side="right", padx=(16, 20))
        ttk.Label(status_bar, textvariable=self.validation_var, style="Status.TLabel").pack(side="right")

    def _select_section(self, index):
        if self.main_notebook is None:
            return
        tabs = self.main_notebook.tabs()
        if not 0 <= int(index) < len(tabs):
            return
        self.main_notebook.select(int(index))
        for button_index, button in enumerate(self.nav_buttons):
            button.configure(style="NavSelected.TButton" if button_index == int(index) else "Nav.TButton")
        if 0 <= int(index) < len(self.SECTION_NAVIGATION):
            self.status_var.set(f"Отворена секция: {self.SECTION_NAVIGATION[int(index)][1]}")

    def _section_index_for_control(self, control):
        if self.main_notebook is None or control is None:
            return None
        control_path = str(control)
        for index, tab_id in enumerate(self.main_notebook.tabs()):
            if control_path == tab_id or control_path.startswith(f"{tab_id}."):
                return index
        return None

    def _jump_to_setting(self, event=None):
        query = self.setting_search_var.get().strip().lower()
        if not query:
            return
        exact = next((key for key in self.controls if key.lower() == query), None)
        key = exact or next((key for key in sorted(self.controls) if query in key.lower()), None)
        if key is None:
            self.status_var.set(f"Не е намерена настройка: {query}")
            return
        self.setting_search_var.set(key)
        control = self.controls[key]
        section_index = self._section_index_for_control(control)
        if section_index is not None:
            self._select_section(section_index)
        try:
            control.focus_set()
        except tk.TclError:
            pass
        self.status_var.set(f"Намерена настройка: {key}")

    def _set_dirty(self, dirty):
        self.is_dirty = bool(dirty)
        if self.is_dirty:
            self.dirty_var.set("Незаписани промени")
            self.dirty_label.configure(style="Dirty.TLabel")
            self.root.title("CVRP Настройки •")
            if self.save_button is not None:
                self.save_button.configure(state="normal")
            if self.reset_button is not None:
                self.reset_button.configure(state="normal")
        else:
            self.dirty_var.set("Всички промени са запазени")
            self.dirty_label.configure(style="Clean.TLabel")
            self.root.title("CVRP Настройки")
            if self.save_button is not None:
                self.save_button.configure(state="disabled")
            if self.reset_button is not None:
                self.reset_button.configure(state="disabled")

    def _mark_dirty(self, *_args):
        if not self._tracking_ready:
            return
        self.validation_var.set("Има непроверени промени")
        self._set_dirty(True)

    def _on_tracked_text_modified(self, widget):
        if not widget.edit_modified():
            return
        widget.edit_modified(False)
        self._mark_dirty()

    def _setup_dirty_tracking(self):
        seen = set()
        for value_holder in self.widgets.values():
            holder_id = id(value_holder)
            if holder_id in seen:
                continue
            seen.add(holder_id)
            if isinstance(value_holder, tk.Variable):
                value_holder.trace_add("write", self._mark_dirty)
            elif isinstance(value_holder, tk.Text):
                value_holder.edit_modified(False)
                value_holder.bind(
                    "<<Modified>>",
                    lambda event, widget=value_holder: self._on_tracked_text_modified(widget),
                    add="+",
                )
        self._tracking_ready = True

    def _resume_dirty_tracking_after_restore(self):
        """Resume change tracking after Tk has delivered pending Text events.

        Programmatic delete/insert operations queue ``<<Modified>>`` events.
        Keeping tracking suspended until the idle queue is drained prevents a
        successful reset from immediately marking the form dirty again.
        """
        for value_holder in self.widgets.values():
            if isinstance(value_holder, tk.Text):
                try:
                    value_holder.edit_modified(False)
                except tk.TclError:
                    pass
        self._tracking_ready = True

    def _set_control_enabled(self, key, enabled):
        control = self.controls.get(key)
        if control is None:
            return
        target_state = self.control_default_states.get(key, "normal") if enabled else "disabled"
        try:
            control.configure(state=target_state)
        except tk.TclError:
            pass

    def _setup_conditional_controls(self):
        dependency_keys = (
            "input.input_source",
            "cvrp.solver_type",
            "routing.engine",
            "output.route_maps_upload_mode",
            "output.map_provider",
            "set_data.enable_set_data_upload",
            "api.web_gui_enabled",
            "api.tsp_use_time_windows",
        )
        for key in dependency_keys:
            value_holder = self.widgets.get(key)
            if isinstance(value_holder, tk.Variable):
                value_holder.trace_add("write", lambda *_args: self._update_conditional_controls())
        self._update_conditional_controls()

    def _update_conditional_controls(self):
        def current(key, default=""):
            holder = self.widgets.get(key)
            if holder is None:
                return default
            try:
                return holder.get()
            except (tk.TclError, AttributeError):
                return default

        input_source = str(current("input.input_source", "http_json")).strip().lower()
        for key in self.controls:
            if key.startswith("input.json_"):
                self._set_control_enabled(key, input_source == "http_json")
            elif key.startswith("input.") and key in {
                "input.excel_file_path", "input.gps_column", "input.client_id_column",
                "input.client_name_column", "input.volume_column", "input.document_column",
                "input.time_window_column", "input.delivery_comment_column", "input.mandatory_column",
            }:
                self._set_control_enabled(key, input_source == "excel")

        engine = str(current("routing.engine", "osrm")).strip().lower()
        for key in self.controls:
            if key.startswith("osrm."):
                self._set_control_enabled(key, engine == "osrm")
            elif key.startswith("valhalla."):
                self._set_control_enabled(key, engine == "valhalla")

        upload_enabled = str(current("output.route_maps_upload_mode", "disabled")).strip().lower() in {
            "effect", "effect_upload", "upload", "new",
        }
        for key in (
            "output.route_maps_upload_url",
            "output.route_maps_upload_token",
            "output.route_maps_upload_token_field",
            "output.route_maps_upload_file_field",
            "output.route_maps_upload_bus_id_field",
            "output.route_maps_upload_timeout_seconds",
        ):
            self._set_control_enabled(key, upload_enabled)

        google_enabled = str(current("output.map_provider", "osm")).strip().lower() == "google"
        self._set_control_enabled("output.google_maps_api_key", google_enabled)
        self._set_control_enabled("output.folium_tiles", not google_enabled)

        set_data_enabled = bool(current("set_data.enable_set_data_upload", False))
        for key in self.controls:
            if key.startswith("set_data.") and key != "set_data.enable_set_data_upload":
                self._set_control_enabled(key, set_data_enabled)

        web_gui_enabled = bool(current("api.web_gui_enabled", False))
        for key in self.controls:
            if key.startswith("api.web_gui_") and key != "api.web_gui_enabled":
                self._set_control_enabled(key, web_gui_enabled)

        tsp_windows_enabled = bool(current("api.tsp_use_time_windows", True))
        for key in ("api.tsp_time_window_wait_weight", "api.tsp_time_window_late_weight"):
            self._set_control_enabled(key, tsp_windows_enabled)

        self._show_solver_fine_settings(current("cvrp.solver_type", "pyvrp"))

    def _show_solver_fine_settings(self, solver_value=None):
        """Shows only the fine-tuning groups used by the selected solver."""
        panels = getattr(self, "solver_fine_panels", None) or {}
        anchor = getattr(self, "solver_fine_anchor", None)
        if not panels or anchor is None:
            return

        solver_type = str(solver_value or "pyvrp").strip()
        solver_type = self.SOLVER_VALUES_BY_LABEL.get(solver_type, solver_type).lower()
        selected_panels = panels.get(solver_type, panels.get("pyvrp", ()))

        for panel in {panel for solver_panels in panels.values() for panel in solver_panels}:
            panel.pack_forget()

        pyvrp_parallel = getattr(self, "pyvrp_parallel_specific", None)
        ortools_parallel = getattr(self, "ortools_parallel_specific", None)
        if pyvrp_parallel is not None:
            if solver_type in {"pyvrp", "pyvrp_experimental"}:
                pyvrp_parallel.grid()
            else:
                pyvrp_parallel.grid_remove()
        if ortools_parallel is not None:
            if solver_type == "or_tools":
                ortools_parallel.grid()
            else:
                ortools_parallel.grid_remove()

        for panel in selected_panels:
            panel.pack(
                fill="x",
                expand=True,
                padx=2,
                pady=(0, 14),
                before=anchor,
            )

    def _restore_values(self, snapshot):
        self._tracking_ready = False
        try:
            for key, value in snapshot.items():
                holder = self.widgets.get(key)
                if key == "cvrp.solver_type":
                    value = self.SOLVER_LABELS.get(value, value)
                if isinstance(holder, tk.Variable):
                    holder.set(value)
                elif isinstance(holder, tk.Text):
                    previous_state = str(holder.cget("state"))
                    if previous_state == "disabled":
                        holder.configure(state="normal")
                    holder.delete("1.0", "end")
                    holder.insert("1.0", "" if value is None else str(value))
                    holder.edit_modified(False)
                    if previous_state == "disabled":
                        holder.configure(state="disabled")

            if "locations.depot_locations" in snapshot:
                self._sync_depot_widgets(self._parse_depots_text(snapshot["locations.depot_locations"]))
            if "locations.traffic_zones" in snapshot:
                self._sync_traffic_zone_widgets(self._parse_traffic_zones_text(snapshot["locations.traffic_zones"]))
            if "locations.center_zones" in snapshot:
                self._sync_center_zone_widgets(self._parse_center_zones_text(snapshot["locations.center_zones"]))
            if "api.tsp_valhalla_truck_profiles" in snapshot:
                self._sync_tsp_truck_profiles_widgets(
                    self._parse_tsp_truck_profiles_text(snapshot["api.tsp_valhalla_truck_profiles"])
                )
            self._sync_vehicle_tree_from_widgets()
        finally:
            self.root.after_idle(self._resume_dirty_tracking_after_restore)
        self._update_conditional_controls()

    def _reset_changes(self):
        if not self.is_dirty:
            return
        if not messagebox.askyesno("Отмяна на промените", "Да върна ли всички полета до последно запазеното състояние?"):
            return
        self._restore_values(copy.deepcopy(self._saved_snapshot))
        self.validation_var.set("Не е проверено")
        self.status_var.set("Промените са отменени")
        self._set_dirty(False)

    def _request_close(self):
        if not self.is_dirty:
            self.root.destroy()
            return
        choice = messagebox.askyesnocancel(
            "Незаписани промени",
            "Има незапазени промени. Да ги запазя ли преди затваряне?",
        )
        if choice is None:
            return
        if choice and not self._save_config(show_success=False):
            return
        self.root.destroy()

    def _reset_validation_styles(self):
        for control in self.controls.values():
            try:
                if isinstance(control, ttk.Entry):
                    control.configure(style="TEntry")
                elif isinstance(control, ttk.Combobox):
                    control.configure(style="TCombobox")
            except tk.TclError:
                pass

    def _mark_invalid_control(self, key):
        control = self.controls.get(key)
        if control is None:
            return
        try:
            if isinstance(control, ttk.Entry):
                control.configure(style="Invalid.TEntry")
            elif isinstance(control, ttk.Combobox):
                control.configure(style="Invalid.TCombobox")
        except tk.TclError:
            pass

    def _validate_values(self, values):
        errors = []
        warnings = []

        def text_value(key, default=""):
            value = values.get(key, default)
            return str(value or "").strip()

        def require_number(key, label, minimum=None, maximum=None, integer=False, optional=False):
            if key not in values:
                return None
            raw = text_value(key)
            if optional and not raw:
                return None
            try:
                number = int(raw) if integer else float(raw)
            except (TypeError, ValueError):
                errors.append((key, f"{label}: въведи валидно {'цяло число' if integer else 'число'}."))
                return None
            if minimum is not None and number < minimum:
                errors.append((key, f"{label}: минималната стойност е {minimum}."))
            if maximum is not None and number > maximum:
                errors.append((key, f"{label}: максималната стойност е {maximum}."))
            return number

        require_number("api.api_port", "API порт", 1, 65535, integer=True)
        require_number("input.json_timeout_seconds", "HTTP JSON timeout", 1, 3600, integer=True)
        require_number("cvrp.time_limit_seconds", "Времеви лимит на решителя", 1, 86400, integer=True)
        require_number(
            "cvrp.pyvrp_next_worker_timeout_seconds",
            "PyVRP experimental worker timeout",
            0,
            86400,
            integer=True,
        )
        require_number(
            "cvrp.vroom_worker_timeout_seconds",
            "VROOM worker timeout",
            0,
            86400,
            integer=True,
        )
        require_number("cvrp.vroom_threads", "VROOM threads", 0, 256, integer=True)
        require_number(
            "cvrp.vrp_worker_timeout_seconds",
            "VRP-Rust worker timeout",
            0,
            86400,
            integer=True,
        )
        require_number("cvrp.vrp_threads", "VRP-Rust threads", 0, 256, integer=True)
        require_number(
            "cvrp.vrp_max_generations",
            "VRP-Rust maximum generations",
            1,
            1000000000000,
            integer=True,
        )
        require_number(
            "cvrp.vroom_exploration_level",
            "VROOM exploration level",
            0,
            5,
            integer=True,
        )
        require_number("cvrp.pyvrp_num_neighbours", "PyVRP съседи", 1, 10000, integer=True)
        require_number("cvrp.pyvrp_weight_wait_time", "PyVRP тежест на чакането", 0, 1000)
        require_number("cvrp.pyvrp_ils_no_improvement", "PyVRP ILS без подобрение", 0, 1000000000, integer=True)
        require_number("cvrp.pyvrp_ils_history_length", "PyVRP ILS история", 1, 10000000, integer=True)
        min_perturbations = require_number(
            "cvrp.pyvrp_min_perturbations", "PyVRP минимални perturbations", 1, 1000000, integer=True
        )
        max_perturbations = require_number(
            "cvrp.pyvrp_max_perturbations", "PyVRP максимални perturbations", 1, 1000000, integer=True
        )
        if (
            min_perturbations is not None
            and max_perturbations is not None
            and min_perturbations > max_perturbations
        ):
            errors.append((
                "cvrp.pyvrp_max_perturbations",
                "PyVRP максималните perturbations трябва да са поне колкото минималните.",
            ))
        require_number("cvrp.pyvrp_display_interval_seconds", "PyVRP progress интервал", 0.1, 3600)
        require_number(
            "cvrp.pyvrp_penalty_solutions_between_updates",
            "PyVRP penalty update интервал",
            1,
            1000000000,
            integer=True,
        )
        require_number("cvrp.pyvrp_penalty_increase", "PyVRP penalty увеличение", 1, 1000)
        require_number("cvrp.pyvrp_penalty_decrease", "PyVRP penalty намаление", 0, 1)
        require_number("cvrp.pyvrp_penalty_target_feasible", "PyVRP target feasible", 0, 1)
        require_number("cvrp.pyvrp_penalty_feas_tolerance", "PyVRP feasible tolerance", 0, 1)
        penalty_min = require_number("cvrp.pyvrp_penalty_min", "PyVRP минимална penalty", 0, 1000000000)
        penalty_max = require_number("cvrp.pyvrp_penalty_max", "PyVRP максимална penalty", 0, 1000000000)
        if penalty_min is not None and penalty_max is not None and penalty_min > penalty_max:
            errors.append((
                "cvrp.pyvrp_penalty_max",
                "PyVRP максималната penalty трябва да е поне колкото минималната.",
            ))
        workers = require_number("cvrp.num_workers", "Брой workers", -1, 128, integer=True)
        if workers == 0 or (workers is not None and workers < -1):
            errors.append(("cvrp.num_workers", "Брой workers трябва да бъде -1 или положително число."))
        require_number("api.tsp_two_opt_max_passes", "TSP 2-opt обходи", 0, 10000, integer=True)
        require_number("api.tsp_worker_timeout_seconds", "TSP worker timeout", 1, 86400, integer=True)
        require_number("output.route_maps_upload_timeout_seconds", "Upload timeout", 1, 3600, integer=True)

        start_minutes = require_number(
            "cvrp.customer_time_window_default_start_minutes",
            "Начало на стандартния работен прозорец",
            0,
            2879,
            integer=True,
        )
        end_minutes = require_number(
            "cvrp.customer_time_window_default_end_minutes",
            "Край на стандартния работен прозорец",
            0,
            2879,
            integer=True,
        )
        if start_minutes is not None and end_minutes is not None and start_minutes >= end_minutes:
            errors.append((
                "cvrp.customer_time_window_default_end_minutes",
                "Краят на стандартния работен прозорец трябва да е след началото.",
            ))

        coordinate_keys = (
            "locations.depot_location",
            "locations.center_location",
            "locations.vratza_depot_location",
        )
        for key in coordinate_keys:
            raw = text_value(key)
            coords = self._parse_coords_text(raw)
            if not coords or not (-90 <= coords[0] <= 90 and -180 <= coords[1] <= 180):
                errors.append((key, f"{key}: очакват се валидни координати lat, lon."))

        for key, raw_value in values.items():
            if not key.startswith("vehicle."):
                continue
            if key.endswith(".capacity"):
                try:
                    if float(raw_value) <= 0:
                        errors.append((key, "Капацитетът на буса трябва да е положителен."))
                except (TypeError, ValueError):
                    errors.append((key, "Капацитетът на буса трябва да е число."))
            elif key.endswith(".count"):
                try:
                    if int(float(raw_value)) < 0:
                        errors.append((key, "Броят на бусовете не може да е отрицателен."))
                except (TypeError, ValueError):
                    errors.append((key, "Броят на бусовете трябва да е цяло число."))
            elif key.endswith(".reload_time_minutes"):
                try:
                    if int(float(raw_value)) < 0:
                        errors.append((key, "Времето за презареждане не може да е отрицателно."))
                except (TypeError, ValueError):
                    errors.append((key, "Времето за презареждане трябва да е цяло число."))
            elif key.endswith(".max_customers_per_day") and str(raw_value or "").strip():
                try:
                    if int(float(raw_value)) < 1:
                        errors.append((key, "Дневният лимит за клиенти трябва да е положителен."))
                except (TypeError, ValueError):
                    errors.append((key, "Дневният лимит за клиенти трябва да е цяло число."))

        seen_vehicle_config_ids = set()
        for key, raw_value in values.items():
            if not key.startswith("vehicle.") or not key.endswith(".config_id"):
                continue
            prefix = key.rsplit(".", 1)[0]
            if self._parse_bool_value(values.get(f"{prefix}.remove", False)):
                continue
            config_id = str(raw_value or "").strip()
            if not config_id:
                errors.append((key, "Стабилното ID на превозното средство е задължително."))
            elif config_id in seen_vehicle_config_ids:
                errors.append((key, f"Стабилното ID '{config_id}' се използва повече от веднъж."))
            seen_vehicle_config_ids.add(config_id)

        url_keys = (
            "input.json_url",
            "osrm.base_url",
            "osrm.public_osrm_url",
            "valhalla.base_url",
            "output.route_maps_upload_url",
            "set_data.set_data_url",
            "api.api_public_url",
            "api.web_gui_public_url",
        )
        for key in url_keys:
            raw = text_value(key)
            if not raw:
                continue
            parsed = urlparse(raw)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append((key, f"{key}: очаква се пълен http:// или https:// URL."))

        for key in (
            "api.api_endpoint",
            "api.trigger_endpoint",
            "api.saturday_trigger_endpoint",
            "api.tsp_endpoint",
            "api.tsp_report_endpoint",
            "api.shutdown_endpoint",
            "api.health_endpoint",
            "api.web_gui_endpoint",
        ):
            raw = text_value(key)
            if raw and not raw.startswith("/"):
                errors.append((key, f"{key}: endpoint-ът трябва да започва с /."))

        web_endpoint_raw = text_value("api.web_gui_endpoint")
        web_endpoint = web_endpoint_raw.rstrip("/") or "/"
        if web_endpoint_raw:
            if web_endpoint == "/":
                errors.append(("api.web_gui_endpoint", "Web GUI endpoint-ът не може да бъде коренът /."))
            elif not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+", web_endpoint):
                errors.append((
                    "api.web_gui_endpoint",
                    "Web GUI endpoint-ът съдържа невалидни знаци, интервал, query или fragment.",
                ))
            for key in (
                "api.api_endpoint",
                "api.trigger_endpoint",
                "api.saturday_trigger_endpoint",
                "api.tsp_endpoint",
                "api.tsp_report_endpoint",
                "api.shutdown_endpoint",
                "api.health_endpoint",
            ):
                reserved = text_value(key).rstrip("/") or "/"
                if reserved == web_endpoint or reserved.startswith(f"{web_endpoint}/"):
                    errors.append((
                        "api.web_gui_endpoint",
                        f"Web GUI endpoint-ът се припокрива със запазения API endpoint {reserved}.",
                    ))
                    break

        for proxy_item in re.split(r"[,;\s]+", text_value("api.web_gui_trusted_proxy_ips")):
            if not proxy_item:
                continue
            try:
                ipaddress.ip_network(proxy_item, strict=False)
            except ValueError:
                errors.append((
                    "api.web_gui_trusted_proxy_ips",
                    f"Невалиден доверен proxy IP/CIDR: {proxy_item}",
                ))
                break

        routing_engine = text_value("routing.engine", "osrm").lower()
        if routing_engine not in {"osrm", "valhalla"}:
            errors.append(("routing.engine", "Routing engine трябва да бъде osrm или valhalla."))
        solver_type = text_value("cvrp.solver_type", "pyvrp").lower()
        if solver_type not in set(config.CVRP_SOLVER_TYPES):
            errors.append((
                "cvrp.solver_type",
                "Решителят трябва да бъде pyvrp, pyvrp_experimental, or_tools, vroom или vrp.",
            ))
        if solver_type == "vroom" and self._parse_bool_value(
            values.get("cvrp.enable_multiple_trips", False)
        ):
            errors.append((
                "cvrp.enable_multiple_trips",
                "VROOM 1.15 не поддържа свързани повторни курсове. Изключи повторните курсове или избери PyVRP/OR-Tools/VRP-Rust.",
            ))

        if text_value("locations.center_zone_mode", "circle").lower() == "circle":
            require_number("locations.center_zone_radius_km", "Радиус на главната център зона", 0.001, 500)
        else:
            polygon = self._parse_polygon_text(text_value("locations.center_zone_polygon"))
            if len(polygon) < 3:
                errors.append(("locations.center_zone_polygon", "Полигонът на главната център зона изисква поне 3 точки."))

        host = text_value("api.api_host", "127.0.0.1")
        api_key = text_value("api.api_key")
        if host not in {"127.0.0.1", "localhost", "::1"} and not api_key:
            warnings.append("API слуша в мрежата без зададен API ключ.")
        for key in ("input.json_url", "set_data.set_data_url"):
            if text_value(key).lower().startswith("http://"):
                warnings.append(f"{key} използва некриптиран HTTP.")
        if text_value("output.map_provider", "osm").lower() == "google" and not text_value("output.google_maps_api_key"):
            warnings.append("Избран е Google Maps, но липсва API key.")

        self._reset_validation_styles()
        for key, _message in errors:
            self._mark_invalid_control(key)
        return errors, warnings

    def _focus_validation_error(self, key):
        control = self.controls.get(key)
        if control is None:
            return
        section_index = self._section_index_for_control(control)
        if section_index is not None:
            self._select_section(section_index)
        try:
            control.focus_set()
        except tk.TclError:
            pass

    def _validate_from_ui(self):
        values = self._collect_values()
        errors, warnings = self._validate_values(values)
        if errors:
            self.validation_var.set(f"{len(errors)} грешки")
            self.status_var.set("Настройките съдържат грешки")
            self._focus_validation_error(errors[0][0])
            details = "\n".join(f"• {message}" for _key, message in errors[:12])
            if len(errors) > 12:
                details += f"\n• … и още {len(errors) - 12}"
            messagebox.showerror("Невалидни настройки", details)
            return False
        if warnings:
            self.validation_var.set(f"Проверено: {len(warnings)} предупреждения")
            self.status_var.set("Настройките са валидни с предупреждения")
            messagebox.showwarning("Настройките са валидни", "\n".join(f"• {item}" for item in warnings))
        else:
            self.validation_var.set("Проверено успешно")
            self.status_var.set("Всички настройки са валидни")
            messagebox.showinfo("Проверката е успешна", "Не са открити проблеми в настройките.")
        return True

    # ── Helpers ──────────────────────────────────────────────

    def _dispatch_mousewheel(self, event):
        widget = event.widget
        while widget is not None:
            if isinstance(widget, tk.Canvas) and getattr(widget, "_scroll_needed", False):
                units = int(-1 * (event.delta / 120)) if event.delta else 0
                if units:
                    widget.yview_scroll(units, "units")
                    return "break"
                return None
            widget = getattr(widget, "master", None)
        return None

    def _make_scrollable_frame(self, parent):
        shell = ttk.Frame(parent)
        shell.pack(fill="both", expand=True)

        canvas = tk.Canvas(shell, highlightthickness=0, background="#eef2f6", borderwidth=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        frame = ttk.Frame(canvas, padding=(18, 16))
        window_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        def _update_scroll_visibility(event=None):
            canvas.update_idletasks()
            bbox = canvas.bbox("all")
            if not bbox:
                return
            content_height = bbox[3] - bbox[1]
            canvas_height = canvas.winfo_height()
            canvas.configure(scrollregion=bbox)
            if content_height > canvas_height:
                scrollbar.pack(side="right", fill="y")
                canvas._scroll_needed = True
            else:
                scrollbar.pack_forget()
                canvas._scroll_needed = False

        frame.bind("<Configure>", _update_scroll_visibility)
        canvas.bind("<Configure>", _update_scroll_visibility)
        canvas._scroll_needed = False

        def _resize_inner(event):
            canvas.itemconfigure(window_id, width=event.width)

        canvas.bind("<Configure>", _resize_inner, add="+")
        return frame

    def _bind_text_editing(self, widget):
        """Make common clipboard shortcuts reliable in Tk/ttk fields."""
        def _select_all(event=None):
            try:
                if isinstance(widget, tk.Text):
                    widget.tag_add("sel", "1.0", "end-1c")
                    widget.mark_set("insert", "end-1c")
                else:
                    widget.select_range(0, "end")
                    widget.icursor("end")
            except tk.TclError:
                pass
            return "break"

        def _event_handler(virtual_event):
            def _handler(event=None):
                try:
                    widget.event_generate(virtual_event)
                except tk.TclError:
                    pass
                return "break"

            return _handler

        bindings = {
            "<<Cut>>": ("<Control-x>", "<Control-X>", "<Shift-Delete>"),
            "<<Copy>>": ("<Control-c>", "<Control-C>", "<Control-Insert>"),
            "<<Paste>>": ("<Control-v>", "<Control-V>", "<Shift-Insert>"),
        }
        for sequence in ("<Control-a>", "<Control-A>"):
            widget.bind(sequence, _select_all)
        for virtual_event, sequences in bindings.items():
            handler = _event_handler(virtual_event)
            for sequence in sequences:
                widget.bind(sequence, handler)

    def _scroll_parent_canvas_from_child(self, widget, event):
        parent = widget
        while parent is not None:
            parent = getattr(parent, "master", None)
            if isinstance(parent, tk.Canvas):
                if getattr(parent, "_scroll_needed", False):
                    if getattr(event, "num", None) == 4:
                        units = -3
                    elif getattr(event, "num", None) == 5:
                        units = 3
                    else:
                        units = int(-1 * (getattr(event, "delta", 0) / 120))
                    if units:
                        parent.yview_scroll(units, "units")
                return "break"
        return "break"

    def _bind_combobox_scrolling(self, combo):
        def _on_wheel(event):
            return self._scroll_parent_canvas_from_child(combo, event)

        combo.bind("<MouseWheel>", _on_wheel)
        combo.bind("<Button-4>", _on_wheel)
        combo.bind("<Button-5>", _on_wheel)

    def _bind_text_scrolling(self, text_widget):
        def _scroll_units(units):
            first, last = text_widget.yview()
            if units < 0 and first <= 0:
                return None
            if units > 0 and last >= 1:
                return None
            text_widget.yview_scroll(units, "units")
            return "break"

        def _on_mousewheel(event):
            units = -3 if event.delta > 0 else 3
            return _scroll_units(units)

        def _on_button_4(event):
            return _scroll_units(-3)

        def _on_button_5(event):
            return _scroll_units(3)

        text_widget.bind("<MouseWheel>", _on_mousewheel)
        text_widget.bind("<Button-4>", _on_button_4)
        text_widget.bind("<Button-5>", _on_button_5)

    def _paste_to_var(self, var):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Няма текст", "Clipboard-ът е празен.")
            return

        var.set(text.strip())

    def _paste_to_text_widget(self, widget):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Няма текст", "Clipboard-ът е празен.")
            return
        widget.delete("1.0", "end")
        widget.insert("1.0", text.strip())

    def _add_field(self, parent, row, key, label, value, field_type="str", options=None, tooltip=""):
        parent.columnconfigure(1, weight=1)
        display_value = "" if value is None else str(value)
        ttk.Label(parent, text=label, anchor="w", width=28, wraplength=230, style="Surface.TLabel").grid(
            row=row, column=0, sticky="nw", padx=(8, 12), pady=6
        )

        if field_type == "bool":
            var = tk.BooleanVar(value=bool(value))
            cb = ttk.Checkbutton(parent, variable=var)
            cb.grid(row=row, column=1, sticky="w", padx=6, pady=6)
            self.widgets[key] = var
            self.controls[key] = cb
            self.control_default_states[key] = "normal"
        elif field_type == "combo" and options:
            var = tk.StringVar(value=display_value)
            combo = ttk.Combobox(parent, textvariable=var, values=options, state="readonly", width=34)
            combo.grid(row=row, column=1, sticky="w", padx=6, pady=6)
            self._bind_text_editing(combo)
            self._bind_combobox_scrolling(combo)
            self.widgets[key] = var
            self.controls[key] = combo
            self.control_default_states[key] = "readonly"
        else:
            var = tk.StringVar(value=display_value)
            entry_options = {"show": "•"} if field_type == "secret" else {}
            if field_type == "secret":
                secret_box = ttk.Frame(parent, style="Surface.TFrame")
                secret_box.grid(row=row, column=1, sticky="we", padx=6, pady=6)
                secret_box.columnconfigure(0, weight=1)
                entry = ttk.Entry(secret_box, textvariable=var, width=40, **entry_options)
                entry.grid(row=0, column=0, sticky="we")

                def toggle_secret(target=entry):
                    target.configure(show="" if str(target.cget("show")) else "•")

                ttk.Button(
                    secret_box,
                    text="Покажи",
                    command=toggle_secret,
                    style="Secondary.TButton",
                ).grid(row=0, column=1, padx=(6, 0))
            else:
                entry = ttk.Entry(parent, textvariable=var, width=40, **entry_options)
                entry.grid(row=row, column=1, sticky="we", padx=6, pady=6)
            self._bind_text_editing(entry)
            self.widgets[key] = var
            self.controls[key] = entry
            self.control_default_states[key] = "normal"

        if tooltip:
            ttk.Label(parent, text=tooltip, style="Hint.TLabel", wraplength=330).grid(
                row=row, column=2, sticky="nw", padx=(10, 4), pady=6
            )

    def _add_text_field(self, parent, row, key, label, value, height=4, tooltip=""):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, anchor="w", width=28, wraplength=230, style="Surface.TLabel").grid(
            row=row, column=0, sticky="nw", padx=(8, 12), pady=6
        )
        text_frame = ttk.Frame(parent, style="Surface.TFrame")
        text_frame.grid(row=row, column=1, sticky="we", padx=6, pady=6)
        text_frame.columnconfigure(0, weight=1)
        widget = tk.Text(text_frame, width=40, height=height, wrap="none", relief="solid", borderwidth=1)
        widget.insert("1.0", "" if value is None else str(value))
        widget.grid(row=0, column=0, sticky="we")
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=widget.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        widget.configure(yscrollcommand=scrollbar.set)
        self._bind_text_editing(widget)
        self.widgets[key] = widget
        self.controls[key] = widget
        self.control_default_states[key] = "normal"

        if tooltip:
            ttk.Label(parent, text=tooltip, style="Hint.TLabel", wraplength=330).grid(
                row=row, column=2, sticky="nw", padx=(10, 4), pady=6
            )

    def _add_note_row(self, parent, row, text):
        ttk.Label(parent, text=text, style="Hint.TLabel", wraplength=900, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="we", padx=8, pady=(2, 8)
        )

    def _get_web_gui_credential_store(self):
        """Return the shared credential store, migrating legacy config once."""
        if self._web_gui_credential_store is None:
            api_config = getattr(self.cfg, "api", None)
            legacy_users = getattr(api_config, "web_gui_users", "") if api_config else ""
            self._web_gui_credential_store = CredentialStore(
                _base_dir,
                legacy_users=str(legacy_users or ""),
            )
        return self._web_gui_credential_store

    def _selected_web_gui_username(self):
        tree = self.web_gui_users_tree
        if tree is not None:
            selected = tree.selection()
            if selected:
                values = tree.item(selected[0], "values")
                if values:
                    return str(values[0])
        username_var = getattr(self, "web_gui_username_var", None)
        return str(username_var.get() if username_var is not None else "").strip()

    def _refresh_web_gui_users(self, select_username=None, show_errors=True):
        tree = self.web_gui_users_tree
        if tree is None:
            return []
        try:
            usernames = self._get_web_gui_credential_store().list_usernames()
        except (CredentialStoreError, CredentialValidationError, OSError) as exc:
            if self.web_gui_users_status_var is not None:
                self.web_gui_users_status_var.set("Грешка при зареждане на защитеното хранилище")
            if show_errors:
                messagebox.showerror("Потребители за web GUI", str(exc))
            return []

        for item_id in tree.get_children():
            tree.delete(item_id)
        selected_item = None
        for username in usernames:
            item_id = tree.insert("", "end", values=(username,))
            if username == select_username:
                selected_item = item_id
        if selected_item:
            tree.selection_set(selected_item)
            tree.focus(selected_item)
            tree.see(selected_item)

        if self.web_gui_users_status_var is not None:
            count_text = "1 потребител" if len(usernames) == 1 else f"{len(usernames)} потребители"
            if usernames:
                self.web_gui_users_status_var.set(
                    f"{count_text} • записват се веднага в data/web_gui_auth.json"
                )
            else:
                self.web_gui_users_status_var.set(
                    "Няма потребители • добави поне един, за да има достъп до web GUI"
                )
        return usernames

    def _on_web_gui_user_selected(self, _event=None):
        username = self._selected_web_gui_username()
        if not username:
            return
        self.web_gui_username_var.set(username)
        self.web_gui_password_var.set("")
        self.web_gui_password_confirm_var.set("")

    def _web_gui_credential_form_values(self):
        return _validate_web_gui_credential_form(
            self.web_gui_username_var.get(),
            self.web_gui_password_var.get(),
            self.web_gui_password_confirm_var.get(),
        )

    def _clear_web_gui_password_fields(self):
        self.web_gui_password_var.set("")
        self.web_gui_password_confirm_var.set("")

    def _add_web_gui_user(self):
        try:
            username, password = self._web_gui_credential_form_values()
            store = self._get_web_gui_credential_store()
            if username in store.list_usernames():
                messagebox.showwarning(
                    "Потребителят съществува",
                    f"'{username}' вече съществува. Използвай 'Смени паролата'.",
                )
                return
            store.upsert_user(username, password)
        except ValueError as exc:
            messagebox.showwarning("Невалидни данни", str(exc))
            return
        except (CredentialStoreError, CredentialValidationError, OSError) as exc:
            messagebox.showerror("Потребители за web GUI", str(exc))
            return

        self._clear_web_gui_password_fields()
        self._refresh_web_gui_users(select_username=username)
        self.status_var.set(
            f"Потребителят '{username}' е добавен веднага. Не е нужно общото 'Запази'."
        )

    def _reset_web_gui_user_password(self):
        try:
            username, password = self._web_gui_credential_form_values()
            store = self._get_web_gui_credential_store()
            if username not in store.list_usernames():
                messagebox.showwarning(
                    "Няма такъв потребител",
                    f"'{username}' не е намерен. Използвай 'Добави'.",
                )
                return
            store.reset_password(username, password)
        except ValueError as exc:
            messagebox.showwarning("Невалидни данни", str(exc))
            return
        except (CredentialStoreError, CredentialValidationError, OSError) as exc:
            messagebox.showerror("Потребители за web GUI", str(exc))
            return

        self._clear_web_gui_password_fields()
        self._refresh_web_gui_users(select_username=username)
        self.status_var.set(
            f"Паролата на '{username}' е сменена веднага. Активните му сесии са прекратени."
        )

    def _delete_web_gui_user(self):
        username = self._selected_web_gui_username()
        if not username:
            messagebox.showwarning("Липсва потребител", "Избери потребител от списъка.")
            return
        try:
            store = self._get_web_gui_credential_store()
            if username not in store.list_usernames():
                messagebox.showinfo("Няма такъв потребител", f"'{username}' вече не съществува.")
                self._refresh_web_gui_users()
                return
        except (CredentialStoreError, CredentialValidationError, OSError) as exc:
            messagebox.showerror("Потребители за web GUI", str(exc))
            return

        if not messagebox.askyesno(
            "Изтриване на потребител",
            f"Да изтрия ли '{username}'? Достъпът и активните му сесии ще бъдат прекратени веднага.",
            icon="warning",
        ):
            return
        try:
            store.delete_user(username)
        except (CredentialStoreError, CredentialValidationError, OSError) as exc:
            messagebox.showerror("Потребители за web GUI", str(exc))
            return

        self.web_gui_username_var.set("")
        self._clear_web_gui_password_fields()
        self._refresh_web_gui_users()
        self.status_var.set(
            f"Потребителят '{username}' е изтрит веднага. Не е нужно общото 'Запази'."
        )

    def _add_web_gui_user_controls(self, parent, row):
        ttk.Label(
            parent,
            text="Потребители за вход:",
            anchor="w",
            width=28,
            wraplength=230,
            style="Surface.TLabel",
        ).grid(row=row, column=0, sticky="nw", padx=(8, 12), pady=6)

        box = ttk.Frame(parent, style="Surface.TFrame")
        box.grid(row=row, column=1, sticky="we", padx=6, pady=6)
        box.columnconfigure(0, weight=1)

        list_box = ttk.Frame(box, style="Surface.TFrame")
        list_box.grid(row=0, column=0, sticky="nsew")
        list_box.columnconfigure(0, weight=1)
        self.web_gui_users_tree = ttk.Treeview(
            list_box,
            columns=("username",),
            show="headings",
            selectmode="browse",
            height=5,
        )
        self.web_gui_users_tree.heading("username", text="Потребителско име")
        self.web_gui_users_tree.column("username", anchor="w", stretch=True, width=260)
        self.web_gui_users_tree.grid(row=0, column=0, sticky="nsew")
        user_scroll = ttk.Scrollbar(
            list_box,
            orient="vertical",
            command=self.web_gui_users_tree.yview,
        )
        user_scroll.grid(row=0, column=1, sticky="ns")
        self.web_gui_users_tree.configure(yscrollcommand=user_scroll.set)
        self.web_gui_users_tree.bind("<<TreeviewSelect>>", self._on_web_gui_user_selected)

        form = ttk.Frame(box, style="Surface.TFrame")
        form.grid(row=1, column=0, sticky="we", pady=(10, 0))
        form.columnconfigure(1, weight=1)
        self.web_gui_username_var = tk.StringVar()
        self.web_gui_password_var = tk.StringVar()
        self.web_gui_password_confirm_var = tk.StringVar()

        ttk.Label(form, text="Потребител", style="Surface.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=3
        )
        username_entry = ttk.Entry(form, textvariable=self.web_gui_username_var)
        username_entry.grid(row=0, column=1, sticky="we", pady=3)
        ttk.Label(form, text="Нова парола", style="Surface.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=3
        )
        password_entry = ttk.Entry(form, textvariable=self.web_gui_password_var, show="•")
        password_entry.grid(row=1, column=1, sticky="we", pady=3)
        ttk.Label(form, text="Потвърди", style="Surface.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=3
        )
        confirm_entry = ttk.Entry(form, textvariable=self.web_gui_password_confirm_var, show="•")
        confirm_entry.grid(row=2, column=1, sticky="we", pady=3)

        actions = ttk.Frame(box, style="Surface.TFrame")
        actions.grid(row=2, column=0, sticky="we", pady=(9, 0))
        ttk.Button(actions, text="Добави", command=self._add_web_gui_user).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(
            actions,
            text="Смени паролата",
            command=self._reset_web_gui_user_password,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions,
            text="Изтрий",
            command=self._delete_web_gui_user,
            style="Danger.TButton",
        ).pack(side="left")

        self.web_gui_users_status_var = tk.StringVar(value="Зареждам потребителите...")
        ttk.Label(
            box,
            textvariable=self.web_gui_users_status_var,
            style="Hint.TLabel",
            wraplength=520,
        ).grid(row=3, column=0, sticky="w", pady=(7, 0))

        for entry in (username_entry, password_entry, confirm_entry):
            self._bind_text_editing(entry)
        confirm_entry.bind("<Return>", lambda _event: self._add_web_gui_user())
        ttk.Label(
            parent,
            text=(
                "Паролите са скрити и се пазят само като защитени hash-ове. "
                f"Минимум {_WEB_GUI_PASSWORD_MIN_LENGTH} знака. Интервали, Unicode, ':' и ';' са позволени "
                "и не се премахват. Промените влизат в сила веднага."
            ),
            style="Hint.TLabel",
            wraplength=330,
        ).grid(row=row, column=2, sticky="nw", padx=(10, 4), pady=6)

        self._refresh_web_gui_users()

    def _add_group(self, parent, title, hint=""):
        group = ttk.LabelFrame(parent, text=title, padding=(14, 12))
        group.pack(fill="x", expand=True, padx=2, pady=(0, 14))
        group.columnconfigure(1, weight=1)
        group.columnconfigure(2, weight=0, minsize=340)
        row = 0
        if hint:
            ttk.Label(group, text=hint, style="Hint.TLabel", wraplength=900).grid(
                row=row, column=0, columnspan=3, sticky="we", padx=8, pady=(0, 10)
            )
            row += 1
        return group, row

    def _add_grid_group(self, parent, row, title, hint="", columnspan=3):
        group = ttk.LabelFrame(parent, text=title, padding=(14, 12))
        group.grid(row=row, column=0, columnspan=columnspan, sticky="we", padx=2, pady=(0, 14))
        group.columnconfigure(1, weight=1)
        group.columnconfigure(2, weight=0, minsize=340)
        inner_row = 0
        if hint:
            ttk.Label(group, text=hint, style="Hint.TLabel", wraplength=900).grid(
                row=inner_row, column=0, columnspan=3, sticky="we", padx=8, pady=(0, 10)
            )
            inner_row += 1
        return group, inner_row

    def _copy_to_clipboard(self, text, status_message="Копирано"):
        self.root.clipboard_clear()
        self.root.clipboard_append(str(text))
        self.status_var.set(status_message)

    def _widget_value(self, widget, default=""):
        if widget is None:
            return default
        if isinstance(widget, tk.Text):
            return widget.get("1.0", "end-1c")
        try:
            return widget.get()
        except Exception:
            return default

    def _selected_tree_index(self, tree):
        if tree is None:
            return None
        selection = tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except (TypeError, ValueError):
            return None

    def _selected_listbox_index(self, listbox):
        if listbox is None:
            return None
        selection = listbox.curselection()
        if not selection:
            return None
        return selection[0]

    def _clear_tree_selection(self, tree):
        if tree is not None:
            for item in tree.selection():
                tree.selection_remove(item)

    def _api_json_example(self, payload):
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _api_json_compact(self, payload):
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _add_api_doc_row(self, parent, row, title, body):
        ttk.Label(parent, text=title, style="Surface.TLabel", width=18).grid(
            row=row, column=0, sticky="nw", padx=(8, 10), pady=6
        )
        ttk.Label(parent, text=body, style="Hint.TLabel", wraplength=820, justify="left").grid(
            row=row, column=1, columnspan=2, sticky="we", padx=6, pady=6
        )
        return row + 1

    def _add_copyable_doc_block(self, parent, row, title, body, height=5):
        ttk.Label(parent, text=title, style="Surface.TLabel", width=18).grid(
            row=row, column=0, sticky="nw", padx=(8, 10), pady=6
        )
        box = ttk.Frame(parent, style="Surface.TFrame")
        box.grid(row=row, column=1, sticky="we", padx=6, pady=6)
        box.columnconfigure(0, weight=1)
        text = tk.Text(
            box,
            height=height,
            wrap="word",
            relief="solid",
            borderwidth=1,
            font=("Consolas", 9),
            background="#f8fafc",
        )
        text.insert("1.0", str(body).strip())
        text.configure(state="disabled", cursor="arrow")
        text.grid(row=0, column=0, sticky="nsew")
        self._bind_text_editing(text)
        self._bind_text_scrolling(text)
        ttk.Button(
            parent,
            text="Копирай пример",
            command=lambda value=body: self._copy_to_clipboard(value, "Примерът е копиран."),
        ).grid(row=row, column=2, sticky="nw", padx=(8, 4), pady=6)
        return row + 1

    def _api_endpoint_docs(self, base_url, solve_path, trigger_path, saturday_trigger_path, tsp_path, health_path, auth_header, shutdown_path):
        run_body = {
            "return_result": True,
            "callback_url": "https://example.com/cvrp-finished",
            "settings": {
                "solver_type": "pyvrp",
                "objective_metric": "time",
                "time_limit_seconds": 180,
                "vehicles": [
                    {"vehicle_type": "internal_bus", "count": 7, "capacity": 385},
                    {"vehicle_type": "vratza_bus", "count": 3, "capacity": 385},
                ],
                "depots": [
                    {"name": "main", "role": "main", "location": [42.6957, 23.2316]},
                    {"name": "vratza", "role": "vratza", "location": [43.2210, 23.5344]},
                ],
                "output": {"enable_excel_output": True, "excel_output_dir": "C:\\CVRP\\output"},
                "set_data": {"enable_set_data_upload": False},
            },
        }
        solve_body = {
            "settings": {
                "solver_type": "or_tools",
                "objective_metric": "time",
                "time_limit_seconds": 180,
                "set_data.enable_set_data_upload": False,
            },
            "customers": [
                {
                    "IdCust": "1000001",
                    "CustName": "Клиент 1",
                    "GPS": "42.6977,23.3219",
                    "Volume": 10.5,
                    "IdDoc": "DOC001",
                    "WorkTime": "08:00-13:00\n16:00-18:00",
                    "ServiceTimeMinutes": 6,
                    "Mandatory": True,
                    "DeliveryComment": "Обади се 10 минути преди доставка.",
                }
            ],
        }
        tsp_body = {
            "driver_id": "1004501001",
            "driver_location": "42.695785029219415,23.23165887245312",
            "end_location": "42.695785029219415,23.23165887245312",
            "service_time_minutes": 8,
            "customers": [
                {
                    "id": "1005487516",
                    "name": "Клиент 1",
                    "document": "0004384359",
                    "gps": "42.67973749002523,23.324913047254086",
                    "quantity": 8,
                    "turnover": 245.50,
                    "work_time": "08:00-13:00",
                    "comment": "Вход откъм булеварда.",
                },
                {
                    "id": "1007010901",
                    "name": "Клиент 2",
                    "document": "0004385134",
                    "gps": "42.66299,23.31376",
                    "quantity": 2,
                    "turnover": 98.20,
                    "work_time": "",
                    "comment": "",
                },
            ],
        }
        return [
            {
                "tab": trigger_path,
                "what": "Стартира цялата CVRP програма с текущите настройки или с временни settings от body/query. GET връща веднага, POST може да чака резултат при return_result=true.",
                "method": "GET или POST",
                "command": f'curl -X POST "{base_url}{trigger_path}" -H "Content-Type: application/json"{auth_header} -d "{self._api_json_compact(run_body).replace(chr(34), chr(92) + chr(34))}"',
                "body": self._api_json_example(run_body),
                "returns": "202 started за background run, 200 с пълен JSON резултат при return_result=true, 409 ако вече върви основен run.",
            },
            {
                "tab": saturday_trigger_path,
                "what": "Стартира съботен run. Датата е съботата от текущата седмица, а normal prefix-ът и броят цифри идват от полетата за събота в GUI. При включено center ID правило CENTER_BUS запазва отделната си серия.",
                "method": "GET или POST",
                "command": f'curl{auth_header} "{base_url}{saturday_trigger_path}"',
                "body": "Няма задължителен body.",
                "returns": "202 started за background run или 409, ако вече има активен CVRP run.",
            },
            {
                "tab": solve_path,
                "what": "Приема клиенти директно в заявката и връща JSON решение. Използва се, когато външната система подава списък клиенти, а не иска програмата сама да дърпа входа.",
                "method": "POST",
                "command": f'curl -X POST "{base_url}{solve_path}" -H "Content-Type: application/json"{auth_header} -d "{self._api_json_compact(solve_body).replace(chr(34), chr(92) + chr(34))}"',
                "body": self._api_json_example(solve_body),
                "returns": "200 с маршрути, необслужени клиенти, файлове и summary. При грешен вход връща JSON error.",
            },
            {
                "tab": tsp_path,
                "what": "Подрежда текущ маршрут за един шофьор от текущата му GPS позиция до крайна точка. Не пуска основния CVRP solver.",
                "method": "POST",
                "command": f'curl -X POST "{base_url}{tsp_path}" -H "Content-Type: application/json"{auth_header} -d "{self._api_json_compact(tsp_body).replace(chr(34), chr(92) + chr(34))}"',
                "body": self._api_json_example(tsp_body),
                "returns": "Ако TSP HTML и upload -> Отговор от /tsp = json: 200 JSON с ред на доставка, ETA, км/минути, map_file и upload статус. Ако е html: 200 text/html със самата карта като body. Локалният HTML файл се управлява отделно.",
            },
            {
                "tab": health_path,
                "what": "Проверява дали API сървърът работи и показва текущ run, последен run, endpoint-и и TSP готовност.",
                "method": "GET",
                "command": f'curl "{base_url}{health_path}"',
                "body": "Няма body.",
                "returns": "200 със status, listen/public URL, endpoints, run status, TSP статус и налични команди.",
            },
            {
                "tab": shutdown_path,
                "what": "Спира API сървъра/програмата. Използвай го само за контролирано спиране от доверена система.",
                "method": "GET или POST",
                "command": f'curl -X POST "{base_url}{shutdown_path}"{auth_header}',
                "body": "Няма body.",
                "returns": "200/202 със съобщение за спиране. Ако има API key, заявката трябва да го подаде.",
            },
        ]

    def _add_api_docs_notebook(self, parent, row, docs):
        notebook = ttk.Notebook(parent)
        notebook.grid(row=row, column=0, columnspan=3, sticky="nsew", padx=8, pady=6)
        parent.rowconfigure(row, weight=1)
        parent.columnconfigure(0, weight=1)
        for doc in docs:
            page = ttk.Frame(notebook, padding=(12, 10), style="Surface.TFrame")
            page.columnconfigure(1, weight=1)
            notebook.add(page, text=f" {doc['tab']} ")
            r = 0
            r = self._add_api_doc_row(page, r, "Какво прави", doc["what"])
            r = self._add_api_doc_row(page, r, "Метод", doc["method"])
            r = self._add_copyable_doc_block(page, r, "Примерна команда", doc["command"], height=5)
            r = self._add_copyable_doc_block(page, r, "JSON body", doc["body"], height=10)
            self._add_api_doc_row(page, r, "Какво връща", doc["returns"])
        return row + 1

    def _api_base_url_preview(self, api):
        public_url = str(getattr(api, "api_public_url", "") or "").strip().rstrip("/")
        if public_url:
            return public_url

        host = str(getattr(api, "api_host", "0.0.0.0") or "0.0.0.0").strip()
        port = str(getattr(api, "api_port", 8088) or 8088).strip()
        display_host = self._api_display_host(host)
        return f"http://{display_host}:{port}"

    def _api_display_host(self, host):
        host = str(host or "").strip()
        if host not in {"", "0.0.0.0", "::"}:
            return host

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                address = sock.getsockname()[0]
                if address and not address.startswith("127."):
                    return address
        except OSError:
            pass

        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                address = info[4][0]
                if address and not address.startswith("127.") and not address.startswith("169.254."):
                    return address
        except OSError:
            pass

        return "127.0.0.1"

    def _api_path_preview(self, api, attr_name, default):
        path = str(getattr(api, attr_name, default) or default).strip()
        return path if path.startswith("/") else f"/{path}"

    def _api_settings_reference_blocks(self):
        return [
            (
                "Как се пишат settings:",
                """
1. Вложени секции:
   "settings": {
     "output": { "excel_output_dir": "H:/Out" },
     "set_data": { "enable_set_data_upload": false }
   }

2. Точкова нотация:
   "settings": {
     "output.excel_output_dir": "H:/Out",
     "set_data.enable_set_data_upload": false
   }

3. GET query за кратки настройки:
   /run?solver=pyvrp&time_limit=180&output.enable_excel_output=true

Всички настройки са временни само за тази заявка. GUI/config.py не се презаписват.
                """,
                14,
            ),
            (
                "Run опции:",
                """
Тези опции се подават до settings, но НЕ са настройки на програмата.

return_result: true
return_json: true
wait: true
sync: true
  Заявката чака оптимизацията да завърши и връща пълния JSON резултат.

callback_url: "https://example.com/cvrp-finished"
notify_url: "https://example.com/cvrp-finished"
webhook_url: "https://example.com/cvrp-finished"
  API-то връща веднага 202 started, а след края праща POST към този URL.

cmd / command / action:
  run, start, trigger, solve_config, start_program
  Използва се за trigger през /solve, когато външната система няма отделен /run.
                """,
                17,
            ),
            (
                "Бързи aliases:",
                """
solver / solver_type                  -> cvrp.solver_type
objective / objective_metric          -> cvrp.objective_metric
optimize_by                           -> cvrp.objective_metric
time_limit / time_limit_seconds       -> cvrp.time_limit_seconds
parallel                              -> cvrp.enable_parallel_solving
workers / num_workers                 -> cvrp.num_workers
pyvrp_seed                            -> cvrp.pyvrp_seed
pyvrp_seed_base                       -> cvrp.pyvrp_seed_base
pyvrp_next_worker_timeout_seconds     -> cvrp.pyvrp_next_worker_timeout_seconds
pyvrp_next_fallback_to_stable         -> cvrp.pyvrp_next_fallback_to_stable
routing_engine / engine               -> routing.engine
osrm_url / osrm_base_url              -> osrm.base_url
osrm_profile                          -> osrm.profile
osrm_timeout                          -> osrm.timeout_seconds
osrm_chunk_size                       -> osrm.chunk_size
date / json_override_date             -> input.json_override_date
sklad / json_sklad                    -> input.json_sklad
done_flag / json_done_flag            -> input.json_done_flag
group_customer_documents              -> input.enable_customer_document_grouping
tsp_service_time_minutes              -> api.tsp_default_service_time_minutes
tsp_metric / tsp_optimize_by          -> api.tsp_objective_metric
tsp_time_windows                      -> api.tsp_use_time_windows
tsp_wait_weight                       -> api.tsp_time_window_wait_weight
tsp_late_weight                       -> api.tsp_time_window_late_weight
tsp_two_opt                           -> api.tsp_enable_two_opt
tsp_two_opt_max_passes                -> api.tsp_two_opt_max_passes
tsp_response_format                   -> api.tsp_response_format
tsp_generate_map                      -> api.tsp_generate_html_map
tsp_generate_local_html               -> api.tsp_generate_html_map
tsp_local_html                        -> api.tsp_generate_html_map
tsp_upload_map                        -> api.tsp_upload_html_map
tsp_worker_timeout                    -> api.tsp_worker_timeout_seconds
tsp_truck_profiles                    -> api.tsp_valhalla_truck_profiles
tsp_truck_driver_ids                  -> api.tsp_valhalla_truck_driver_ids
tsp_truck_height                      -> api.tsp_valhalla_truck_height
tsp_truck_width                       -> api.tsp_valhalla_truck_width
tsp_truck_length                      -> api.tsp_valhalla_truck_length
tsp_truck_weight                      -> api.tsp_valhalla_truck_weight
tsp_truck_axle_load                   -> api.tsp_valhalla_truck_axle_load
tsp_truck_axle_count                  -> api.tsp_valhalla_truck_axle_count
tsp_truck_hazmat                      -> api.tsp_valhalla_truck_hazmat
tsp_truck_hgv_no_access_penalty       -> api.tsp_valhalla_truck_hgv_no_access_penalty
tsp_daily_report                      -> api.tsp_daily_report_enabled
tsp_daily_report_time                 -> api.tsp_daily_report_time
                """,
                31,
            ),
            (
                "Solver настройки:",
                """
cvrp.solver_type                       pyvrp, pyvrp_experimental, or_tools, vroom или vrp
cvrp.enable_multiple_trips             разрешава динамични допълнителни курсове
cvrp.objective_metric                  distance = най-къси км, time = най-кратко време
cvrp.time_objective_include_waiting    при time включва чакането в целия работен ден
cvrp.time_limit_seconds                време за решаване
cvrp.enable_parallel_solving           паралелно решаване
cvrp.num_workers                       брой процеси (-1 = автоматично)
cvrp.allow_customer_skipping           позволява пропускане на клиенти
cvrp.distance_penalty_disjunction      глоба за пропуснат клиент
cvrp.enable_priority_dropping          индивидуални penalties според обем/близост
cvrp.min_customer_drop_penalty         минимална индивидуална глоба
cvrp.max_customer_drop_penalty         максимална индивидуална глоба
cvrp.enable_customer_time_windows      спазва работното време на клиентите
cvrp.first_solution_strategy           OR-Tools начална стратегия
cvrp.local_search_metaheuristic        OR-Tools локално търсене
cvrp.lns_time_limit_seconds            OR-Tools LNS лимит
cvrp.pyvrp_seed                        точен PyVRP seed
cvrp.pyvrp_seed_base                   база за seed-ове при паралелно PyVRP
cvrp.pyvrp_num_neighbours              PyVRP neighbourhood размер
cvrp.pyvrp_ils_no_improvement          PyVRP търпимост без подобрение
cvrp.pyvrp_ils_history_length          PyVRP ILS история
cvrp.pyvrp_exhaustive_on_best          по-задълбочено търсене при нов best
cvrp.pyvrp_use_extended_operators      разширени PyVRP оператори
cvrp.pyvrp_min_perturbations           минимална perturbation сила
cvrp.pyvrp_max_perturbations           максимална perturbation сила
cvrp.pyvrp_display_progress            PyVRP progress log
cvrp.pyvrp_next_worker_path             optional път до PyVRP 0.14 sidecar
cvrp.pyvrp_next_worker_timeout_seconds  timeout за experimental worker (0 = auto)
cvrp.pyvrp_next_fallback_to_stable      fallback към стабилния PyVRP 0.13
cvrp.vroom_worker_path                  optional път до VROOM sidecar
cvrp.vroom_worker_timeout_seconds       timeout за VROOM worker (0 = auto)
cvrp.vroom_threads                      вътрешни VROOM нишки (0 = auto)
cvrp.vroom_exploration_level            качество 0..5; 5 е най-задълбочено
cvrp.vrp_worker_path                    optional път до VRP-Rust sidecar
cvrp.vrp_worker_timeout_seconds         timeout за VRP-Rust worker (0 = auto)
cvrp.vrp_threads                        вътрешни нишки на VRP-Rust (0 = auto)
cvrp.vrp_max_generations                максимален брой поколения
cvrp.vrp_log_progress                   VRP-Rust progress log
                """,
                27,
            ),
            (
                "Routing и OSRM:",
                """
routing.engine                         osrm или valhalla
routing.enable_time_dependent          time-dependent Valhalla routing
routing.departure_time                 час на тръгване, напр. 08:00
routing.enable_curbside_approach       страна на улицата
routing.valhalla_preferred_side        same / either / opposite

osrm.base_url                          OSRM адрес, напр. http://localhost:5000
osrm.profile                           driving / walking / cycling
osrm.chunk_size                        брой локации в една OSRM заявка
osrm.timeout_seconds                   timeout за OSRM
osrm.retry_attempts                    повторни опити
osrm.average_speed_kmh                 fallback скорост
osrm.fallback_to_public                fallback към публичен OSRM
osrm.max_locations_for_osrm            праг за OSRM матрица
osrm.enable_smart_chunking             smart chunking

valhalla.base_url, valhalla.costing, valhalla.timeout_seconds също могат да се подават.
                """,
                20,
            ),
            (
                "Входни данни:",
                """
input.input_source                     http_json или excel
input.excel_file_path                  Excel файл
input.json_url                         URL за HTTP JSON
input.json_http_method                 GET или POST
input.json_command                     cmd стойност
input.json_date_field                  поле за дата
input.json_sklad                       складове, напр. 106,128
input.json_done_flag                   DoneFlag
input.json_override_date               дата DD/MM/YYYY
input.json_timeout_seconds             timeout
input.enable_customer_document_grouping групира един клиент/GPS с няколко документа в един стоп

JSON mapping:
input.json_gps_field, input.json_client_id_field, input.json_client_name_field
input.json_volume_field, input.json_document_field, input.json_plas_doc_field
input.json_id_skld_field, input.json_time_window_field
input.json_delivery_comment_field, input.json_service_time_field, input.json_mandatory_field

WorkTime може да съдържа няколко прозореца. В JSON използвай escape \\n,
например "08:00-13:00\\n16:00-18:00". Валидните прозорци се подават към solver-а.
ServiceTimeMinutes е индивидуално време за обслужване; при липса се използва времето от буса.
Mandatory=true прави клиента абсолютно задължителен. Допустими са true/false, 1/0 и да/не.
                """,
                21,
            ),
            (
                "Изходни файлове:",
                """
output.enable_interactive_map           генерира основна HTML карта
output.map_output_file                  път до interactive_map.html
output.routes_output_dir                папка за HTML маршрутите
output.route_maps_upload_mode           disabled, legacy или effect_upload
output.route_maps_upload_url            URL за качване на индивидуалните HTML карти
output.route_maps_upload_token_field    име на token POST поле, обикновено pData
output.route_maps_upload_token          token стойност за upload endpoint-а
output.route_maps_upload_file_field     multipart поле, обикновено files[]
output.route_maps_upload_bus_id_field   multipart POST поле за ID-та на бусовете, обикновено pData2[]
output.route_maps_upload_timeout_seconds timeout за качване
output.map_provider                     osm или google
output.folium_tiles                     слой за OSM/Folium
output.google_maps_api_key              Google Maps key
output.map_zoom_level                   zoom на картата
output.show_route_colors                цветове на маршрутите
output.show_vehicle_info                информация за бус на картата

Excel:
output.enable_excel_output              генерира общ cvrp_report_<date>.xlsx
output.excel_output_dir                 папка за Excel
output.routes_excel_file                legacy име; текущият workflow е общ workbook
output.warehouse_excel_file             legacy име; текущият workflow е общ workbook
output.efficiency_excel_file            legacy име; текущият workflow е общ workbook
output.excel_bus_number_prefix          префикс ID бус
output.excel_bus_number_digits          брой цифри
output.saturday_excel_bus_number_prefix normal префикс само за /run_saturday
output.saturday_excel_bus_number_digits normal цифри само за /run_saturday
output.center_bus_numbering_enabled     включва специално ID правило за CENTER_BUS
output.center_bus_numbering_start_id    първи ID за CENTER_BUS, напр. 1004501015

CSV и графики:
output.enable_csv_output                генерира CSV
output.csv_output_file                  път до CSV
output.enable_charts                    генерира графики
output.charts_output_dir                папка за графики
output.efficiency_chart_file            име на efficiency chart
output.route_comparison_file            име на comparison chart
output.volume_distribution_file         име на volume chart
                """,
                29,
            ),
            (
                "setData:",
                """
set_data.enable_set_data_upload         включва/изключва изпращане към Bizant
set_data.set_data_url                   URL за setData
set_data.set_data_http_method           GET или POST
set_data.set_data_command               cmd, обикновено setData
set_data.set_data_done_flag             DoneFlag
set_data.set_data_id_skld               IdSkld за основно депо
set_data.set_data_vratza_id_skld        IdSkld за Враца
set_data.set_data_depot_id_skld_map     ръчна карта Депо=IdSkld
set_data.set_data_id_grafik             фиксиран IdGrafik
set_data.set_data_id_grafik_template    шаблон за IdGrafik
set_data.set_data_bukva_template        шаблон за Bukva
set_data.enable_unserved_set_data_upload изпраща необслужени
set_data.set_data_unserved_done_flag    DoneFlag за необслужени; празно = общия DoneFlag
set_data.set_data_unserved_id_grafik    IdGrafik за необслужени
set_data.set_data_unserved_id_grafik_template шаблон за необслужени
set_data.set_data_unserved_bukva_template     Bukva за необслужени
set_data.enable_make_group              изпраща makeGroup след setData
set_data.set_data_make_group_command    cmd за makeGroup
set_data.set_data_timeout_seconds       timeout

Важно: ако не искаш реално изпращане, подай:
"set_data": { "enable_set_data_upload": false }
                """,
                24,
            ),
            (
                "Бусове, депа и зони:",
                """
vehicle_counts:
  Кратък начин само за брой:
  "vehicle_counts": { "internal_bus": 7, "vratza_bus": 3 }

depots:
  Управлява главното депо, депо Враца и допълнителни депа.
  "depots": [
    { "name": "main", "role": "main", "location": [42.6957, 23.2316] },
    { "name": "vratza", "role": "vratza", "location": [43.2210, 23.5344] },
    { "name": "north", "location": [42.8000, 23.4000] }
  ]

  Или като обект:
  "depots": {
    "main": [42.6957, 23.2316],
    "vratza": [43.2210, 23.5344],
    "north": [42.8000, 23.4000]
  }

vehicles:
  Patch по config_id; ако липсва config_id, patch-ва редовете по vehicle_type.
  Пази останалите бусове непроменени.
  start_depot_name може да сочи име от depots.
  end_location е optional GPS крайна точка. Ако липсва, маршрутът завършва в стартовото депо.
  end_depot_name може да сочи име от depots.
  "vehicles": [
    { "vehicle_type": "internal_bus", "count": 7, "capacity": 385, "name": "HELL", "start_depot_name": "main", "end_location": [42.7000, 23.4000] },
    { "vehicle_type": "vratza_bus", "count": 3, "start_depot_name": "vratza", "end_depot_name": "vratza" }
  ]

replace_vehicles:
  Пълна подмяна на всички бусове. Използвай внимателно.

vehicle_counts_by_id:
  Сменя броя само на конкретен конфигурационен ред по неговия config_id.

Полета за един бус:
  config_id, vehicle_type, capacity, count, name, fixed_cost, max_distance_km,
  max_time_hours, service_time_minutes, enabled, start_location, end_location,
  reload_location/reload_depot_name, reload_time_minutes, max_customers_per_day,
  legacy max_customers_per_route, start_time_minutes, tsp_depot_location

center_zone:
  Управлява старата основна център зона и глобите за нея.
  "center_zone": {
    "mode": "circle",
    "center": [42.6977, 23.3219],
    "radius_km": 2.0,
    "internal_bus_penalty": 40000,
    "external_bus_penalty": 40000,
    "vratza_bus_penalty": 40000,
    "center_bus_outside_penalty": 0
  }

center_zones:
  Допълнителни независими център зони. Всяка зона има собствени правила за кои бусове да важи.
  "center_zones": [
    {
      "name": "Център 2",
      "mode": "circle",
      "center": [42.7093, 23.3137],
      "radius_km": 1.2,
      "priority_vehicle_types": ["center_bus"],
      "restricted_vehicle_types": ["internal_bus", "external_bus", "vratza_bus"],
      "discount_priority_vehicle": 0.9,
      "priority_vehicle_outside_penalty": 0,
      "vehicle_penalties": {
        "internal_bus": 40000,
        "external_bus": 40000,
        "vratza_bus": 40000
      },
      "enabled": true,
      "show_on_map": true
    }
  ]

traffic_zones:
  Допълнителни зони с multiplier за време.
  "traffic_zones": [
    { "name": "Center traffic", "center": [42.6977, 23.3219], "radius_km": 3.0, "multiplier": 1.3, "enabled": true, "show_on_map": false }
  ]

enabled управлява solver правилото на допълнителните зони. Legacy основната
center зона се изключва чрез enable_priority=false и enable_restrictions=false.
show_on_map управлява само визуализацията.

city_traffic:
  Старият общ градски трафик.
  "city_traffic": {
    "enabled": true,
    "center": [42.6977, 23.3219],
    "radius_km": 10,
    "multiplier": 1.55
  }

locations.*:
  Можеш да подадеш и директните вътрешни полета:
  locations.depot_location, locations.center_location, locations.vratza_depot_location
  locations.center_zone_polygon, locations.center_zones, locations.depot_locations, locations.traffic_zones
                """,
                48,
            ),
        ]

    def _add_copyable_command(self, parent, row, title, command, hint=""):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=title, style="Surface.TLabel", width=22).grid(
            row=row, column=0, sticky="nw", padx=(8, 10), pady=6
        )
        text = tk.Text(
            parent,
            height=2,
            wrap="word",
            relief="solid",
            borderwidth=1,
            font=("Consolas", 9),
        )
        text.insert("1.0", command)
        text.configure(state="disabled", cursor="arrow", background="#f8fafc")
        text.grid(row=row, column=1, sticky="we", padx=6, pady=6)
        self._bind_text_editing(text)
        ttk.Button(
            parent,
            text="Копирай",
            command=lambda value=command: self._copy_to_clipboard(value, "Командата е копирана."),
        ).grid(row=row, column=2, sticky="nw", padx=(8, 4), pady=6)
        if hint:
            ttk.Label(parent, text=hint, style="Hint.TLabel", wraplength=720).grid(
                row=row + 1, column=1, columnspan=2, sticky="we", padx=6, pady=(0, 8)
            )
            return row + 2
        return row + 1

    def _add_readonly_text(self, parent, row, title, body, height=7):
        ttk.Label(parent, text=title, style="Surface.TLabel", width=22).grid(
            row=row, column=0, sticky="nw", padx=(8, 10), pady=6
        )
        box = ttk.Frame(parent)
        box.grid(row=row, column=1, columnspan=2, sticky="we", padx=6, pady=6)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        text = tk.Text(
            box,
            height=height,
            wrap="word",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 9),
            background="#f8fafc",
        )
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.insert("1.0", body.strip())
        text.configure(state="disabled", cursor="arrow")
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._bind_text_editing(text)
        self._bind_text_scrolling(text)
        return row + 1

    def _add_list_field(self, parent, row, key, label, values_list, options=None, tooltip=""):
        """Добавя поле за списък от стойности (по една на ред в Text widget)"""
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, anchor="w", width=28, wraplength=230, style="Surface.TLabel").grid(
            row=row, column=0, sticky="nw", padx=(8, 12), pady=6
        )
        text_val = "\n".join(str(v) for v in values_list)
        text_height = max(3, min(8, len(values_list) or 3))
        text_w = tk.Text(
            parent,
            width=40,
            height=text_height,
            wrap="none",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 9),
        )
        text_w.insert("1.0", text_val)
        text_w.grid(row=row, column=1, sticky="we", padx=6, pady=6)
        self._bind_text_editing(text_w)
        self.widgets[key] = text_w
        self.controls[key] = text_w
        self.control_default_states[key] = "normal"
        hint = tooltip or "По един елемент на ред"
        ttk.Label(parent, text=hint, style="Hint.TLabel", wraplength=330).grid(
            row=row, column=2, sticky="nw", padx=(10, 4), pady=6
        )

    def _parse_tsp_truck_profiles_text(self, raw):
        profiles = []
        for line in str(raw or "").splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            parts = [part.strip() for part in text.split("|")]
            profile = {
                "name": parts[0] if parts else "Truck profile",
                "ids": "",
                "height": "3.5",
                "width": "2.5",
                "length": "7.0",
                "weight": "10.0",
                "axle_load": "9.0",
                "axle_count": "2",
                "hazmat": "false",
                "hgv_no_access_penalty": "43200",
            }
            for part in parts[1:]:
                if "=" not in part:
                    continue
                key, value = part.split("=", 1)
                key = key.strip().lower()
                value = value.strip()
                aliases = {
                    "driver_ids": "ids",
                    "drivers": "ids",
                    "bus_ids": "ids",
                    "h": "height",
                    "w": "width",
                    "l": "length",
                    "axles": "axle_count",
                    "hgv_penalty": "hgv_no_access_penalty",
                }
                profile[aliases.get(key, key)] = value
            if profile.get("ids"):
                profiles.append(profile)
        return profiles

    def _format_tsp_truck_profiles_text(self, profiles):
        lines = []
        for profile in profiles or []:
            name = str(profile.get("name") or "Truck profile").strip()
            ids = str(profile.get("ids") or "").strip()
            if not ids:
                continue
            parts = [
                name,
                f"ids={ids}",
                f"height={profile.get('height', '3.5')}",
                f"width={profile.get('width', '2.5')}",
                f"length={profile.get('length', '7.0')}",
                f"weight={profile.get('weight', '10.0')}",
                f"axle_load={profile.get('axle_load', '9.0')}",
                f"axle_count={profile.get('axle_count', '2')}",
                f"hazmat={str(profile.get('hazmat', 'false')).lower()}",
                f"hgv_no_access_penalty={profile.get('hgv_no_access_penalty', '43200')}",
            ]
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    def _sync_tsp_truck_profiles_widgets(self, profiles):
        hidden = self.widgets.get("api.tsp_valhalla_truck_profiles")
        if isinstance(hidden, tk.Text):
            hidden.delete("1.0", "end")
            hidden.insert("1.0", self._format_tsp_truck_profiles_text(profiles))

        tree = getattr(self, "tsp_truck_profiles_tree", None)
        if tree is not None:
            for item in tree.get_children():
                tree.delete(item)
            for index, profile in enumerate(profiles or []):
                tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        profile.get("name", ""),
                        profile.get("ids", ""),
                        profile.get("height", ""),
                        profile.get("width", ""),
                        profile.get("length", ""),
                        profile.get("weight", ""),
                        profile.get("axle_count", ""),
                        profile.get("hazmat", "false"),
                    ),
                )

    def _current_tsp_truck_profiles(self):
        hidden = self.widgets.get("api.tsp_valhalla_truck_profiles")
        if isinstance(hidden, tk.Text):
            return self._parse_tsp_truck_profiles_text(hidden.get("1.0", "end-1c"))
        return []

    def _clear_tsp_truck_profile_form(self):
        defaults = {
            "api.new_tsp_truck_profile_name": "",
            "api.new_tsp_truck_profile_ids": "",
            "api.new_tsp_truck_profile_height": "3.5",
            "api.new_tsp_truck_profile_width": "2.5",
            "api.new_tsp_truck_profile_length": "7.0",
            "api.new_tsp_truck_profile_weight": "10.0",
            "api.new_tsp_truck_profile_axle_load": "9.0",
            "api.new_tsp_truck_profile_axle_count": "2",
            "api.new_tsp_truck_profile_hazmat": False,
            "api.new_tsp_truck_profile_hgv_no_access_penalty": "43200",
        }
        for key, value in defaults.items():
            widget = self.widgets.get(key)
            if widget is None:
                continue
            widget.set(value)

    def _tsp_truck_profile_from_form(self):
        def get(key, default=""):
            widget = self.widgets.get(key)
            return widget.get() if widget is not None else default

        name = str(get("api.new_tsp_truck_profile_name", "") or "").strip()
        ids = str(get("api.new_tsp_truck_profile_ids", "") or "").strip()
        if not ids:
            messagebox.showwarning("Липсват ID-та", "Въведете поне едно ID на шофьор/бус за truck профила.")
            return None
        return {
            "name": name or "Truck profile",
            "ids": ids,
            "height": str(get("api.new_tsp_truck_profile_height", "3.5") or "3.5").strip(),
            "width": str(get("api.new_tsp_truck_profile_width", "2.5") or "2.5").strip(),
            "length": str(get("api.new_tsp_truck_profile_length", "7.0") or "7.0").strip(),
            "weight": str(get("api.new_tsp_truck_profile_weight", "10.0") or "10.0").strip(),
            "axle_load": str(get("api.new_tsp_truck_profile_axle_load", "9.0") or "9.0").strip(),
            "axle_count": str(get("api.new_tsp_truck_profile_axle_count", "2") or "2").strip(),
            "hazmat": "true" if bool(get("api.new_tsp_truck_profile_hazmat", False)) else "false",
            "hgv_no_access_penalty": str(get("api.new_tsp_truck_profile_hgv_no_access_penalty", "43200") or "43200").strip(),
        }

    def _upsert_tsp_truck_profile_from_form(self):
        profile = self._tsp_truck_profile_from_form()
        if profile is None:
            return
        profiles = self._current_tsp_truck_profiles()
        tree = getattr(self, "tsp_truck_profiles_tree", None)
        selected = tree.selection()[0] if tree is not None and tree.selection() else None
        if selected is not None:
            try:
                profiles[int(selected)] = profile
            except (ValueError, IndexError):
                profiles.append(profile)
        else:
            profiles.append(profile)
        self._sync_tsp_truck_profiles_widgets(profiles)

    def _remove_selected_tsp_truck_profile(self):
        tree = getattr(self, "tsp_truck_profiles_tree", None)
        if tree is None or not tree.selection():
            messagebox.showinfo("Няма избор", "Изберете truck профил от таблицата.")
            return
        selected = tree.selection()[0]
        profiles = self._current_tsp_truck_profiles()
        try:
            del profiles[int(selected)]
        except (ValueError, IndexError):
            return
        self._sync_tsp_truck_profiles_widgets(profiles)
        self._clear_tsp_truck_profile_form()

    def _load_selected_tsp_truck_profile(self, event=None):
        tree = getattr(self, "tsp_truck_profiles_tree", None)
        if tree is None or not tree.selection():
            return
        try:
            profile = self._current_tsp_truck_profiles()[int(tree.selection()[0])]
        except (ValueError, IndexError):
            return

        values = {
            "api.new_tsp_truck_profile_name": profile.get("name", ""),
            "api.new_tsp_truck_profile_ids": profile.get("ids", ""),
            "api.new_tsp_truck_profile_height": profile.get("height", "3.5"),
            "api.new_tsp_truck_profile_width": profile.get("width", "2.5"),
            "api.new_tsp_truck_profile_length": profile.get("length", "7.0"),
            "api.new_tsp_truck_profile_weight": profile.get("weight", "10.0"),
            "api.new_tsp_truck_profile_axle_load": profile.get("axle_load", "9.0"),
            "api.new_tsp_truck_profile_axle_count": profile.get("axle_count", "2"),
            "api.new_tsp_truck_profile_hazmat": str(profile.get("hazmat", "false")).lower() in {"1", "true", "yes", "on", "да"},
            "api.new_tsp_truck_profile_hgv_no_access_penalty": profile.get("hgv_no_access_penalty", "43200"),
        }
        for key, value in values.items():
            widget = self.widgets.get(key)
            if widget is not None:
                widget.set(value)

    def _add_tsp_truck_profiles_editor(self, parent, row, api):
        box = ttk.LabelFrame(parent, text="TSP Valhalla truck профили", padding=(14, 12))
        box.pack(fill="x", expand=True, padx=2, pady=(0, 14))
        box.columnconfigure(0, weight=1)
        box.columnconfigure(1, weight=1)

        ttk.Label(
            box,
            text="Добави профил за конкретни driver/bus ID-та. Ако /tsp получи driver_id от този списък, матрицата и HTML линията се чертаят с Valhalla truck.",
            style="Hint.TLabel",
            wraplength=980,
        ).grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 10))

        form = ttk.LabelFrame(box, text="Профил", padding=(12, 10))
        form.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        form.columnconfigure(1, weight=1)

        table_box = ttk.LabelFrame(box, text="Създадени профили", padding=(12, 10))
        table_box.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        table_box.columnconfigure(0, weight=1)

        fields = [
            ("api.new_tsp_truck_profile_name", "Име", "Лек камион", "str"),
            ("api.new_tsp_truck_profile_ids", "ID-та", "", "str"),
            ("api.new_tsp_truck_profile_height", "Височина (м)", "3.5", "str"),
            ("api.new_tsp_truck_profile_width", "Ширина (м)", "2.5", "str"),
            ("api.new_tsp_truck_profile_length", "Дължина (м)", "7.0", "str"),
            ("api.new_tsp_truck_profile_weight", "Тегло (т)", "10.0", "str"),
            ("api.new_tsp_truck_profile_axle_load", "Натоварване ос (т)", "9.0", "str"),
            ("api.new_tsp_truck_profile_axle_count", "Брой оси", "2", "str"),
        ]
        for idx, (key, label, default, _field_type) in enumerate(fields):
            ttk.Label(form, text=label, style="Surface.TLabel", width=18).grid(row=idx, column=0, sticky="w", padx=(0, 8), pady=4)
            var = tk.StringVar(value=default)
            entry = ttk.Entry(form, textvariable=var, width=28)
            entry.grid(row=idx, column=1, sticky="we", pady=4)
            self._bind_text_editing(entry)
            self.widgets[key] = var

        row_idx = len(fields)
        ttk.Label(form, text="Опасен товар", style="Surface.TLabel", width=18).grid(row=row_idx, column=0, sticky="w", padx=(0, 8), pady=4)
        hazmat_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, variable=hazmat_var).grid(row=row_idx, column=1, sticky="w", pady=4)
        self.widgets["api.new_tsp_truck_profile_hazmat"] = hazmat_var
        row_idx += 1

        ttk.Label(form, text="HGV no-access глоба", style="Surface.TLabel", width=18).grid(row=row_idx, column=0, sticky="w", padx=(0, 8), pady=4)
        hgv_var = tk.StringVar(value="43200")
        hgv_entry = ttk.Entry(form, textvariable=hgv_var, width=28)
        hgv_entry.grid(row=row_idx, column=1, sticky="we", pady=4)
        self._bind_text_editing(hgv_entry)
        self.widgets["api.new_tsp_truck_profile_hgv_no_access_penalty"] = hgv_var
        row_idx += 1

        ttk.Label(
            form,
            text="ID-тата може да са със запетая или интервал: 1004501008, 1004501012. HGV 43200 = забранява no-access пътища.",
            style="Hint.TLabel",
            wraplength=360,
        ).grid(row=row_idx, column=0, columnspan=2, sticky="we", pady=(6, 8))
        row_idx += 1

        buttons = ttk.Frame(form, style="Surface.TFrame")
        buttons.grid(row=row_idx, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(buttons, text="Добави / обнови", command=self._upsert_tsp_truck_profile_from_form).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Нова форма", command=self._clear_tsp_truck_profile_form).pack(side="left")

        columns = ("name", "ids", "height", "width", "length", "weight", "axles", "hazmat")
        tree = ttk.Treeview(table_box, columns=columns, show="headings", height=7)
        headings = {
            "name": "Име",
            "ids": "ID-та",
            "height": "H",
            "width": "W",
            "length": "L",
            "weight": "Тегло",
            "axles": "Оси",
            "hazmat": "Hazmat",
        }
        widths = {"name": 110, "ids": 210, "height": 50, "width": 50, "length": 50, "weight": 60, "axles": 45, "hazmat": 60}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="w", stretch=(column == "ids"))
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.bind("<<TreeviewSelect>>", self._load_selected_tsp_truck_profile)
        self.tsp_truck_profiles_tree = tree

        table_buttons = ttk.Frame(table_box, style="Surface.TFrame")
        table_buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(table_buttons, text="Зареди избрания", command=self._load_selected_tsp_truck_profile).pack(side="left", padx=(0, 6))
        ttk.Button(table_buttons, text="Изтрий избрания", command=self._remove_selected_tsp_truck_profile).pack(side="left")

        hidden = tk.Text(box, width=1, height=1)
        self.widgets["api.tsp_valhalla_truck_profiles"] = hidden
        raw_profiles = getattr(api, "tsp_valhalla_truck_profiles", "")
        profiles = self._parse_tsp_truck_profiles_text(raw_profiles)
        if not profiles and str(getattr(api, "tsp_valhalla_truck_driver_ids", "") or "").strip():
            profiles = [{
                "name": "Default truck",
                "ids": getattr(api, "tsp_valhalla_truck_driver_ids", ""),
                "height": str(getattr(api, "tsp_valhalla_truck_height", 3.5)),
                "width": str(getattr(api, "tsp_valhalla_truck_width", 2.5)),
                "length": str(getattr(api, "tsp_valhalla_truck_length", 7.0)),
                "weight": str(getattr(api, "tsp_valhalla_truck_weight", 10.0)),
                "axle_load": str(getattr(api, "tsp_valhalla_truck_axle_load", 9.0)),
                "axle_count": str(getattr(api, "tsp_valhalla_truck_axle_count", 2)),
                "hazmat": "true" if getattr(api, "tsp_valhalla_truck_hazmat", False) else "false",
                "hgv_no_access_penalty": str(getattr(api, "tsp_valhalla_truck_hgv_no_access_penalty", 43200)),
            }]
        self._sync_tsp_truck_profiles_widgets(profiles)
        return row + 1

    def _format_polygon_text(self, polygon):
        return "\n".join(f"{float(lat)}, {float(lon)}" for lat, lon in polygon)

    def _format_optional_coords(self, coords):
        if not coords:
            return ""
        return f"{float(coords[0])}, {float(coords[1])}"

    def _named_depots(self):
        return config.get_named_depots(self.cfg.locations)

    def _depot_name_for_coords(self, coords):
        if not coords:
            return "Главно депо"
        for name, depot_coords in self._named_depots().items():
            if (
                abs(float(coords[0]) - float(depot_coords[0])) < 0.000001
                and abs(float(coords[1]) - float(depot_coords[1])) < 0.000001
            ):
                return name
        return "Главно депо"

    def _format_depots_text(self, depots):
        return "\n".join(
            f"{name}: {float(coords[0])}, {float(coords[1])}"
            for name, coords in (depots or {}).items()
        )

    def _depot_display_label(self, name, coords):
        return f"{name}: {float(coords[0]):.5f}, {float(coords[1]):.5f}"

    def _sync_depot_widgets(self, depots):
        depots = depots or {}
        depot_widget = self.widgets.get("locations.depot_locations")
        if isinstance(depot_widget, tk.Text):
            depot_widget.delete("1.0", "end")
            depot_widget.insert("1.0", self._format_depots_text(depots))

        if self.depot_listbox is not None:
            self.depot_listbox.delete(0, "end")
            for name, coords in depots.items():
                self.depot_listbox.insert("end", self._depot_display_label(name, coords))

        if self.depot_tree is not None:
            for item in self.depot_tree.get_children():
                self.depot_tree.delete(item)
            for index, (name, coords) in enumerate(depots.items()):
                self.depot_tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(name, f"{float(coords[0]):.6f}, {float(coords[1]):.6f}"),
                )

        depot_options = ["Главно депо", "Център", "Враца"]
        for name in depots:
            if name not in depot_options:
                depot_options.append(name)
        self.vehicle_depot_options = depot_options
        for combo in getattr(self, "depot_choice_widgets", []):
            combo.configure(values=depot_options)

    def _format_traffic_zones_text(self, zones):
        lines = []
        for zone in zones or []:
            name = getattr(zone, "name", "Трафик зона")
            center = getattr(zone, "center_coords", None)
            if not center:
                continue
            radius = float(getattr(zone, "radius_km", 0) or 0)
            multiplier = float(getattr(zone, "duration_multiplier", 1.0) or 1.0)
            enabled = "true" if getattr(zone, "enabled", True) else "false"
            show_on_map = "true" if getattr(zone, "show_on_map", False) else "false"
            lines.append(
                f"{name}: {float(center[0])}, {float(center[1])}, {radius}, "
                f"{multiplier}, {enabled}, {show_on_map}"
            )
        return "\n".join(lines)

    def _format_center_zones_text(self, zones):
        """Serialise zones for the hidden GUI widget without losing fields.

        This text is an internal transfer format between the editor and the
        config writer. JSON avoids the delimiter collisions of the legacy
        ``name: mode | ...`` representation and preserves every field from
        ``CenterZoneConfig``.
        """
        payload = [self._center_zone_to_data(zone) for zone in zones or []]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _center_zone_to_data(self, zone):
        center = getattr(zone, "center_coords", None)
        polygon = getattr(zone, "polygon", []) or []
        return {
            "name": str(getattr(zone, "name", "") or ""),
            "mode": str(getattr(zone, "mode", "circle") or "circle").strip().lower(),
            "center_coords": [float(center[0]), float(center[1])] if center else None,
            "radius_km": float(getattr(zone, "radius_km", 0.0) or 0.0),
            "polygon": [[float(lat), float(lon)] for lat, lon in polygon],
            "enabled": bool(getattr(zone, "enabled", True)),
            "show_on_map": bool(getattr(zone, "show_on_map", True)),
            "enable_priority": bool(getattr(zone, "enable_priority", True)),
            "enable_restrictions": bool(getattr(zone, "enable_restrictions", True)),
            "priority_vehicle_types": list(getattr(zone, "priority_vehicle_types", []) or []),
            "restricted_vehicle_types": list(getattr(zone, "restricted_vehicle_types", []) or []),
            "discount_priority_vehicle": float(
                getattr(zone, "discount_priority_vehicle", 0.9) or 0.9
            ),
            "priority_vehicle_outside_penalty": float(
                getattr(zone, "priority_vehicle_outside_penalty", 0.0) or 0.0
            ),
            "vehicle_penalties": {
                str(vehicle_type): float(value)
                for vehicle_type, value in (getattr(zone, "vehicle_penalties", {}) or {}).items()
            },
        }

    def _center_zone_bool(self, value, default=True):
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in ("0", "false", "no", "off", "не", "")

    def _validate_center_zone_coords(self, coords, label="Координати"):
        import math

        try:
            if coords is None or len(coords) != 2:
                raise ValueError
            lat, lon = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            raise ValueError(f"{label}: въведи координати във формат lat, lon.") from None

        if not math.isfinite(lat) or not math.isfinite(lon):
            raise ValueError(f"{label}: координатите трябва да са крайни числа.")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError(f"{label}: latitude трябва да е -90..90, longitude -180..180.")
        return (lat, lon)

    def _parse_center_zone_polygon_strict(self, raw):
        points = []
        raw_lines = str(raw or "").replace(";", "\n").splitlines()
        non_empty_lines = [line.strip() for line in raw_lines if line.strip()]
        if not non_empty_lines:
            raise ValueError("Полигонът трябва да съдържа поне 3 точки.")

        for line_number, line in enumerate(non_empty_lines, start=1):
            parts = [part.strip() for part in line.split(",")]
            point = self._validate_center_zone_coords(
                parts,
                label=f"Точка {line_number}",
            )
            points.append(point)

        if len(points) < 3:
            raise ValueError("Полигонът трябва да съдържа поне 3 точки.")
        if len({(round(lat, 10), round(lon, 10)) for lat, lon in points}) < 3:
            raise ValueError("Полигонът трябва да съдържа поне 3 различни точки.")
        return points

    def _parse_center_zone_geometry(self, mode, raw_coords, raw_radius):
        import math

        mode = str(mode or "").strip().lower()
        if mode not in ("circle", "polygon"):
            raise ValueError("Избери тип на зоната: circle или polygon.")

        if mode == "polygon":
            polygon = self._parse_center_zone_polygon_strict(raw_coords)
            return polygon[0], 0.0, polygon

        coords = self._parse_coords_text(raw_coords)
        center = self._validate_center_zone_coords(coords, label="Център на кръга")
        try:
            radius = float(str(raw_radius or "").strip())
        except (TypeError, ValueError):
            raise ValueError("Радиусът трябва да е число.") from None
        if not math.isfinite(radius) or radius <= 0:
            raise ValueError("Радиусът трябва да е по-голям от 0.")
        return center, radius, []

    def _center_zone_from_data(self, data):
        import math

        if not isinstance(data, dict):
            raise ValueError("Център зоната трябва да е JSON object.")

        name = str(data.get("name", "") or "").strip()
        if not name:
            raise ValueError("Център зоната няма име.")
        mode = str(data.get("mode", "circle") or "circle").strip().lower()
        if mode not in ("circle", "polygon"):
            raise ValueError(f"Неподдържан тип център зона: {mode}")

        polygon = []
        center_raw = data.get("center_coords")
        if mode == "polygon":
            polygon_raw = data.get("polygon") or []
            polygon = [
                self._validate_center_zone_coords(point, label=f"Полигон точка {index}")
                for index, point in enumerate(polygon_raw, start=1)
            ]
            if len(polygon) < 3 or len({(round(lat, 10), round(lon, 10)) for lat, lon in polygon}) < 3:
                raise ValueError(f"Център зона '{name}' има невалиден полигон.")
            center_coords = (
                self._validate_center_zone_coords(center_raw, label="Център на полигона")
                if center_raw is not None
                else polygon[0]
            )
            try:
                radius = float(data.get("radius_km", 0.0) or 0.0)
            except (TypeError, ValueError):
                raise ValueError(f"Център зона '{name}' има невалиден радиус.") from None
            if not math.isfinite(radius) or radius < 0:
                raise ValueError(f"Център зона '{name}' има невалиден радиус.")
        else:
            center_coords = self._validate_center_zone_coords(center_raw, label="Център на кръга")
            try:
                radius = float(data.get("radius_km", 0.0))
            except (TypeError, ValueError):
                raise ValueError(f"Център зона '{name}' има невалиден радиус.") from None
            if not math.isfinite(radius) or radius <= 0:
                raise ValueError(f"Център зона '{name}' има невалиден радиус.")
            polygon = []

        def unique_strings(values):
            if values is None:
                return []
            if not isinstance(values, (list, tuple, set)):
                raise ValueError(f"Център зона '{name}' има невалиден списък с типове бусове.")
            return list(dict.fromkeys(str(item).strip() for item in values or [] if str(item).strip()))

        priority_types = unique_strings(data.get("priority_vehicle_types"))
        restricted_types = unique_strings(data.get("restricted_vehicle_types"))

        try:
            discount = float(data.get("discount_priority_vehicle", 0.9))
            outside_penalty = float(data.get("priority_vehicle_outside_penalty", 0.0))
        except (TypeError, ValueError):
            raise ValueError(f"Център зона '{name}' има невалидна отстъпка или глоба.") from None
        if not math.isfinite(discount) or discount <= 0:
            raise ValueError(f"Център зона '{name}' има невалидна отстъпка.")
        if not math.isfinite(outside_penalty) or outside_penalty < 0:
            raise ValueError(f"Център зона '{name}' има невалидна глоба навън.")

        penalties = {}
        penalties_raw = data.get("vehicle_penalties") or {}
        if not isinstance(penalties_raw, dict):
            raise ValueError(f"Център зона '{name}' има невалидни глоби по тип бус.")
        for vehicle_type, raw_value in penalties_raw.items():
            try:
                penalty_value = float(raw_value)
            except (TypeError, ValueError):
                raise ValueError(f"Център зона '{name}' има невалидна глоба за {vehicle_type}.") from None
            if not math.isfinite(penalty_value) or penalty_value < 0:
                raise ValueError(f"Център зона '{name}' има невалидна глоба за {vehicle_type}.")
            penalties[str(vehicle_type).strip()] = penalty_value

        return config.CenterZoneConfig(
            name=name,
            mode=mode,
            center_coords=center_coords,
            radius_km=radius,
            polygon=polygon,
            enabled=self._center_zone_bool(data.get("enabled"), True),
            show_on_map=self._center_zone_bool(data.get("show_on_map"), True),
            enable_priority=self._center_zone_bool(data.get("enable_priority"), True),
            enable_restrictions=self._center_zone_bool(data.get("enable_restrictions"), True),
            priority_vehicle_types=priority_types,
            restricted_vehicle_types=restricted_types,
            discount_priority_vehicle=discount,
            priority_vehicle_outside_penalty=outside_penalty,
            vehicle_penalties=penalties,
        )

    def _parse_depots_text(self, raw):
        depots = {}
        for line in (raw or "").splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            name, coords_raw = line.split(":", 1)
            name = name.strip()
            try:
                parts = [float(part.strip()) for part in coords_raw.split(",")]
                if name and len(parts) == 2:
                    depots[name] = (parts[0], parts[1])
            except ValueError:
                continue
        return depots

    def _parse_traffic_zones_text(self, raw):
        zones = []
        for line in (raw or "").splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            name, zone_raw = line.split(":", 1)
            if not name.strip():
                errors.append(f"Невалидна център зона без име: {line}")
                continue
            parts = [part.strip() for part in zone_raw.split(",")]
            if len(parts) < 4:
                continue
            try:
                enabled = True
                if len(parts) >= 5:
                    enabled = parts[4].lower() not in ("0", "false", "no", "off", "не")
                show_on_map = False
                if len(parts) >= 6:
                    show_on_map = parts[5].lower() not in ("0", "false", "no", "off", "не")
                zones.append(
                    config.TrafficZoneConfig(
                        name=name.strip(),
                        center_coords=(float(parts[0]), float(parts[1])),
                        radius_km=float(parts[2]),
                        duration_multiplier=float(parts[3]),
                        enabled=enabled,
                        show_on_map=show_on_map,
                    )
                )
            except ValueError:
                continue
        return zones

    def _parse_center_zones_text(self, raw, strict=False):
        text = str(raw or "").strip()
        if not text:
            return []

        if text.startswith("["):
            try:
                payload = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                if strict:
                    raise ValueError(f"Невалиден вътрешен JSON за център зоните: {exc}") from exc
                return []
            if not isinstance(payload, list):
                if strict:
                    raise ValueError("Център зоните трябва да са JSON list.")
                return []

            zones = []
            errors = []
            for index, item in enumerate(payload, start=1):
                try:
                    zones.append(self._center_zone_from_data(item))
                except ValueError as exc:
                    errors.append(f"Зона {index}: {exc}")
            if errors and strict:
                raise ValueError("; ".join(errors))
            return zones

        return self._parse_legacy_center_zones_text(text, strict=strict)

    def _parse_legacy_center_zones_text(self, raw, strict=False):
        zones = []
        errors = []
        for line in (raw or "").splitlines():
            line = line.strip()
            if not line or ":" not in line:
                if line:
                    errors.append(f"Невалиден ред: {line}")
                continue
            name, zone_raw = line.split(":", 1)
            parts = [part.strip() for part in zone_raw.split("|")]
            if len(parts) < 5:
                errors.append(f"Невалидна център зона: {name.strip() or line}")
                continue
            try:
                mode = (parts[0] or "circle").lower()
                geometry = parts[1]
                radius = float(parts[2] or 0)
                priority_types = [item.strip() for item in parts[3].replace(";", ",").split(",") if item.strip()]
                restricted_types = [item.strip() for item in parts[4].replace(";", ",").split(",") if item.strip()]
                enabled = True
                if len(parts) >= 6 and parts[5]:
                    enabled = parts[5].lower() not in ("0", "false", "no", "off", "не")
                show_on_map = True
                if len(parts) >= 10 and parts[9]:
                    show_on_map = parts[9].lower() not in ("0", "false", "no", "off", "не")
                discount = float(parts[6]) if len(parts) >= 7 and parts[6] else 0.9
                outside_penalty = float(parts[7]) if len(parts) >= 8 and parts[7] else 0.0
                penalties = {}
                if len(parts) >= 9 and parts[8]:
                    for item in parts[8].replace(";", ",").split(","):
                        if "=" not in item:
                            continue
                        bus, value = item.split("=", 1)
                        penalties[bus.strip()] = float(value.strip())
                if not penalties:
                    penalties = {bus: 40000.0 for bus in restricted_types}

                if mode == "polygon":
                    polygon = self._parse_center_zone_polygon_strict(geometry)
                    center_coords = polygon[0]
                else:
                    center_coords, radius, polygon = self._parse_center_zone_geometry(mode, geometry, radius)

                zones.append(config.CenterZoneConfig(
                    name=name.strip(),
                    mode=mode,
                    center_coords=center_coords,
                    radius_km=radius,
                    polygon=polygon,
                    enabled=enabled,
                    show_on_map=show_on_map,
                    enable_priority=True,
                    enable_restrictions=True,
                    priority_vehicle_types=priority_types,
                    restricted_vehicle_types=restricted_types,
                    discount_priority_vehicle=discount,
                    priority_vehicle_outside_penalty=outside_penalty,
                    vehicle_penalties=penalties,
                ))
            except (TypeError, ValueError) as exc:
                errors.append(f"{name.strip() or 'Център зона'}: {exc}")
        if errors and strict:
            raise ValueError("; ".join(errors))
        return zones

    def _traffic_zone_display_label(self, zone):
        center = getattr(zone, "center_coords", (0, 0))
        radius = float(getattr(zone, "radius_km", 0) or 0)
        multiplier = float(getattr(zone, "duration_multiplier", 1.0) or 1.0)
        delay_percent = max(0, round((multiplier - 1.0) * 100))
        status = "" if getattr(zone, "enabled", True) else " (изключена)"
        map_status = "" if getattr(zone, "show_on_map", False) else " (скрита на картата)"
        return (
            f"{getattr(zone, 'name', 'Трафик зона')}: "
            f"{float(center[0]):.5f}, {float(center[1]):.5f} | "
            f"{radius:g} км | +{delay_percent}%{status}{map_status}"
        )

    def _center_zone_display_label(self, zone):
        mode = str(getattr(zone, "mode", "circle") or "circle").lower()
        if mode == "polygon":
            polygon = getattr(zone, "polygon", []) or []
            geometry = f"полигон {len(polygon)} точки"
        else:
            center = getattr(zone, "center_coords", (0, 0))
            radius = float(getattr(zone, "radius_km", 0) or 0)
            geometry = f"кръг {float(center[0]):.5f}, {float(center[1]):.5f} | {radius:g} км"
        priority = ", ".join(getattr(zone, "priority_vehicle_types", []) or []) or "-"
        restricted = ", ".join(getattr(zone, "restricted_vehicle_types", []) or []) or "-"
        status = "" if getattr(zone, "enabled", True) else " (изключена)"
        map_status = "" if getattr(zone, "show_on_map", True) else " (скрита на картата)"
        return (
            f"{getattr(zone, 'name', 'Център зона')}: {geometry} | "
            f"приоритет: {priority} | глоба: {restricted}{status}{map_status}"
        )

    def _next_traffic_zone_name(self, zones):
        used = {getattr(zone, "name", "") for zone in zones}
        index = len(zones) + 1
        while f"Трафик зона {index}" in used:
            index += 1
        return f"Трафик зона {index}"

    def _next_center_zone_name(self, zones):
        used = {getattr(zone, "name", "") for zone in zones}
        index = len(zones) + 1
        while f"Център зона {index}" in used:
            index += 1
        return f"Център зона {index}"

    def _sync_traffic_zone_widgets(self, zones):
        zones_text = self._format_traffic_zones_text(zones)
        zones_widget = self.widgets.get("locations.traffic_zones")
        if isinstance(zones_widget, tk.Text):
            zones_widget.delete("1.0", "end")
            zones_widget.insert("1.0", zones_text)

        if self.traffic_zone_listbox is not None:
            self.traffic_zone_listbox.delete(0, "end")
            for zone in zones:
                self.traffic_zone_listbox.insert("end", self._traffic_zone_display_label(zone))

        if self.traffic_zone_tree is not None:
            for item in self.traffic_zone_tree.get_children():
                self.traffic_zone_tree.delete(item)
            for index, zone in enumerate(zones):
                center = getattr(zone, "center_coords", (0, 0))
                multiplier = float(getattr(zone, "duration_multiplier", 1.0) or 1.0)
                delay_percent = max(0, round((multiplier - 1.0) * 100))
                self.traffic_zone_tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        getattr(zone, "name", "Трафик зона"),
                        f"{float(center[0]):.6f}, {float(center[1]):.6f}",
                        f"{float(getattr(zone, 'radius_km', 0) or 0):g}",
                        f"+{delay_percent}%",
                        "активна" if getattr(zone, "enabled", True) else "изключена",
                        "Да" if getattr(zone, "show_on_map", False) else "Не",
                    ),
                )

    def _sync_center_zone_widgets(self, zones):
        zones_text = self._format_center_zones_text(zones)
        zones_widget = self.widgets.get("locations.center_zones")
        if isinstance(zones_widget, tk.Text):
            zones_widget.delete("1.0", "end")
            zones_widget.insert("1.0", zones_text)

        if self.center_zone_listbox is not None:
            self.center_zone_listbox.delete(0, "end")
            for zone in zones:
                self.center_zone_listbox.insert("end", self._center_zone_display_label(zone))

        if self.center_zone_tree is not None:
            for item in self.center_zone_tree.get_children():
                self.center_zone_tree.delete(item)
            for index, zone in enumerate(zones):
                mode = str(getattr(zone, "mode", "circle") or "circle").lower()
                if mode == "polygon":
                    geometry = f"полигон, {len(getattr(zone, 'polygon', []) or [])} точки"
                else:
                    center = getattr(zone, "center_coords", (0, 0))
                    geometry = f"{float(center[0]):.6f}, {float(center[1]):.6f} | {float(getattr(zone, 'radius_km', 0) or 0):g} км"
                self.center_zone_tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        getattr(zone, "name", "Център зона"),
                        mode,
                        geometry,
                        ", ".join(getattr(zone, "priority_vehicle_types", []) or []) or "-",
                        ", ".join(getattr(zone, "restricted_vehicle_types", []) or []) or "-",
                        "активна" if getattr(zone, "enabled", True) else "изключена",
                        "Да" if getattr(zone, "show_on_map", True) else "Не",
                    ),
                )

    def _parse_coords_text(self, raw):
        parts = [part.strip() for part in str(raw or "").replace(";", ",").split(",")]
        if len(parts) != 2:
            return None
        try:
            return (float(parts[0]), float(parts[1]))
        except ValueError:
            return None

    def _parse_polygon_points_text(self, raw):
        points = []
        text = str(raw or "").replace(";", "\n")
        for line in text.splitlines():
            coords = self._parse_coords_text(line)
            if coords is not None:
                points.append(coords)
        return points

    def _append_depot_from_fields(self):
        name_widget = self.widgets.get("locations.new_depot_name")
        coords_widget = self.widgets.get("locations.new_depot_coords")
        depot_widget = self.widgets.get("locations.depot_locations")
        if not name_widget or not coords_widget or not isinstance(depot_widget, tk.Text):
            return

        name = name_widget.get().strip()
        coords = self._parse_coords_text(coords_widget.get())
        if not name:
            messagebox.showwarning("Липсва име", "Въведи име на депото.")
            return
        if coords is None:
            messagebox.showwarning("Грешни координати", "Въведи координати във формат lat, lon.")
            return

        depots = self._parse_depots_text(depot_widget.get("1.0", "end-1c"))
        was_existing = name in depots
        depots[name] = coords
        self._sync_depot_widgets(depots)
        name_widget.set("")
        coords_widget.set("")
        action = "обновено" if was_existing else "добавено"
        self.status_var.set(f"Депо '{name}' е {action}. Натисни Запази, за да влезе в config.py.")

    def _remove_selected_depot(self):
        depot_widget = self.widgets.get("locations.depot_locations")
        if not isinstance(depot_widget, tk.Text):
            return
        index = self._selected_tree_index(self.depot_tree)
        if index is None:
            index = self._selected_listbox_index(self.depot_listbox)
        if index is None:
            messagebox.showinfo("Няма избрано депо", "Избери депо от списъка.")
            return

        depots = self._parse_depots_text(depot_widget.get("1.0", "end-1c"))
        names = list(depots.keys())
        if index >= len(names):
            return
        name = names[index]
        depots.pop(name, None)
        self._sync_depot_widgets(depots)
        self.status_var.set(f"Депо '{name}' е премахнато. Натисни Запази, за да се махне от config.py.")

    def _load_selected_depot(self, event=None):
        depot_widget = self.widgets.get("locations.depot_locations")
        name_widget = self.widgets.get("locations.new_depot_name")
        coords_widget = self.widgets.get("locations.new_depot_coords")
        if not isinstance(depot_widget, tk.Text):
            return
        if not name_widget or not coords_widget:
            return
        index = self._selected_tree_index(self.depot_tree)
        if index is None:
            index = self._selected_listbox_index(self.depot_listbox)
        if index is None:
            return

        depots = self._parse_depots_text(depot_widget.get("1.0", "end-1c"))
        items = list(depots.items())
        if index >= len(items):
            return
        name, coords = items[index]
        name_widget.set(name)
        coords_widget.set(f"{float(coords[0])}, {float(coords[1])}")
        self.status_var.set(f"Заредено е депо '{name}' за редакция.")

    def _clear_depot_form(self):
        name_widget = self.widgets.get("locations.new_depot_name")
        coords_widget = self.widgets.get("locations.new_depot_coords")
        if name_widget:
            name_widget.set("")
        if coords_widget:
            coords_widget.set("")
        if self.depot_listbox is not None:
            self.depot_listbox.selection_clear(0, "end")
        self._clear_tree_selection(self.depot_tree)
        self.status_var.set("Формата за депо е изчистена.")

    def _show_selected_depot_on_map(self):
        self._load_selected_depot()
        coords_widget = self.widgets.get("locations.new_depot_coords")
        coords = self._parse_coords_text(coords_widget.get()) if coords_widget is not None else None
        if coords is None:
            messagebox.showinfo("Няма избрано депо", "Избери депо от таблицата.")
            return
        self._open_new_depot_editor()

    def _append_traffic_zone_from_fields(self):
        name_widget = self.widgets.get("locations.new_traffic_zone_name")
        coords_widget = self.widgets.get("locations.new_traffic_zone_coords")
        radius_widget = self.widgets.get("locations.new_traffic_zone_radius")
        delay_widget = self.widgets.get("locations.new_traffic_zone_delay")
        show_on_map_widget = self.widgets.get("locations.new_traffic_zone_show_on_map")
        zones_widget = self.widgets.get("locations.traffic_zones")
        if not all((coords_widget, radius_widget, delay_widget)) or not isinstance(zones_widget, tk.Text):
            return

        coords = self._parse_coords_text(coords_widget.get())
        try:
            radius = float(radius_widget.get())
            delay_percent = float(delay_widget.get())
        except ValueError:
            messagebox.showwarning("Грешна трафик зона", "Радиусът и забавянето трябва да са числа.")
            return

        if coords is None:
            messagebox.showwarning("Грешни координати", "Въведи център във формат lat, lon.")
            return
        if radius <= 0 or delay_percent <= 0:
            messagebox.showwarning("Грешна трафик зона", "Радиусът и забавянето трябва да са по-големи от 0.")
            return

        zones = self._parse_traffic_zones_text(zones_widget.get("1.0", "end-1c"))
        name = name_widget.get().strip() if name_widget else ""
        if not name:
            name = self._next_traffic_zone_name(zones)
        multiplier = 1.0 + (delay_percent / 100.0)
        zone = config.TrafficZoneConfig(
            name=name,
            center_coords=coords,
            radius_km=radius,
            duration_multiplier=multiplier,
            enabled=True,
            show_on_map=bool(show_on_map_widget.get()) if show_on_map_widget is not None else False,
        )
        existing_index = next((idx for idx, item in enumerate(zones) if item.name == name), None)
        if existing_index is None:
            zones.append(zone)
            action = "добавена"
        else:
            zones[existing_index] = zone
            action = "обновена"
        self._sync_traffic_zone_widgets(zones)
        if name_widget:
            name_widget.set("")
        coords_widget.set("")
        self.status_var.set(f"Трафик зона '{name}' е {action}. Натисни Запази, за да влезе в config.py.")

    def _load_selected_traffic_zone(self, event=None):
        zones_widget = self.widgets.get("locations.traffic_zones")
        if not isinstance(zones_widget, tk.Text):
            return
        index = self._selected_tree_index(self.traffic_zone_tree)
        if index is None:
            index = self._selected_listbox_index(self.traffic_zone_listbox)
        if index is None:
            return
        zones = self._parse_traffic_zones_text(zones_widget.get("1.0", "end-1c"))
        if index >= len(zones):
            return
        zone = zones[index]
        name_widget = self.widgets.get("locations.new_traffic_zone_name")
        coords_widget = self.widgets.get("locations.new_traffic_zone_coords")
        radius_widget = self.widgets.get("locations.new_traffic_zone_radius")
        delay_widget = self.widgets.get("locations.new_traffic_zone_delay")
        show_on_map_widget = self.widgets.get("locations.new_traffic_zone_show_on_map")
        center = getattr(zone, "center_coords", (0, 0))
        if name_widget:
            name_widget.set(getattr(zone, "name", ""))
        if coords_widget:
            coords_widget.set(f"{float(center[0])}, {float(center[1])}")
        if radius_widget:
            radius_widget.set(f"{float(getattr(zone, 'radius_km', 0) or 0):g}")
        if delay_widget:
            delay_percent = max(0, (float(getattr(zone, "duration_multiplier", 1.0) or 1.0) - 1.0) * 100)
            delay_widget.set(f"{delay_percent:g}")
        if show_on_map_widget is not None:
            show_on_map_widget.set(bool(getattr(zone, "show_on_map", False)))
        self.status_var.set(f"Заредена е трафик зона '{getattr(zone, 'name', '')}' за редакция.")

    def _clear_traffic_zone_form(self):
        for key in (
            "locations.new_traffic_zone_name",
            "locations.new_traffic_zone_coords",
            "locations.new_traffic_zone_radius",
            "locations.new_traffic_zone_delay",
        ):
            widget = self.widgets.get(key)
            if widget:
                widget.set("")
        radius_widget = self.widgets.get("locations.new_traffic_zone_radius")
        delay_widget = self.widgets.get("locations.new_traffic_zone_delay")
        if radius_widget:
            radius_widget.set("3")
        if delay_widget:
            delay_widget.set("30")
        show_on_map_widget = self.widgets.get("locations.new_traffic_zone_show_on_map")
        if show_on_map_widget is not None:
            show_on_map_widget.set(False)
        if self.traffic_zone_listbox is not None:
            self.traffic_zone_listbox.selection_clear(0, "end")
        self._clear_tree_selection(self.traffic_zone_tree)
        self.status_var.set("Формата за трафик зона е изчистена.")

    def _show_selected_traffic_zone_on_map(self):
        self._load_selected_traffic_zone()
        coords_widget = self.widgets.get("locations.new_traffic_zone_coords")
        coords = self._parse_coords_text(coords_widget.get()) if coords_widget is not None else None
        if coords is None:
            messagebox.showinfo("Няма избрана зона", "Избери трафик зона от таблицата.")
            return
        self._open_new_traffic_zone_editor()

    def _update_center_zone_shape_fields(self, event=None):
        mode_widget = self.widgets.get("locations.new_center_zone_mode")
        mode = str(mode_widget.get() if mode_widget is not None else "circle").strip().lower()
        is_circle = mode == "circle"

        label_widget = getattr(self, "_center_zone_coords_label", None)
        if label_widget is not None:
            label_widget.configure(text="Център (lat, lon)" if is_circle else "Полигон (lat, lon на ред)")
        hint_var = getattr(self, "_center_zone_geometry_hint_var", None)
        if hint_var is not None:
            hint_var.set(
                "Една GPS точка за центъра на кръга."
                if is_circle
                else "Минимум 3 различни GPS точки, по една на ред."
            )
        map_button = getattr(self, "_center_zone_map_button", None)
        if map_button is not None:
            map_button.configure(text="Избери кръг на карта" if is_circle else "Начертай полигон на карта")

        coords_widget = self.widgets.get("locations.new_center_zone_coords")
        if isinstance(coords_widget, tk.Text):
            coords_widget.configure(height=2 if is_circle else 6)

        for widget in getattr(self, "_center_zone_radius_widgets", ()):
            if is_circle:
                widget.grid()
            else:
                widget.grid_remove()

    def _update_center_zone_rule_fields(self):
        priority_enabled_var = self.widgets.get("locations.new_center_zone_enable_priority")
        restrictions_enabled_var = self.widgets.get("locations.new_center_zone_enable_restrictions")
        priority_enabled = bool(priority_enabled_var.get()) if priority_enabled_var is not None else True
        restrictions_enabled = bool(restrictions_enabled_var.get()) if restrictions_enabled_var is not None else True

        for widget in getattr(self, "_center_zone_priority_checkbuttons", {}).values():
            widget.configure(state="normal" if priority_enabled else "disabled")
        preset_combo = getattr(self, "_center_zone_rule_preset_combo", None)
        if preset_combo is not None:
            preset_combo.configure(state="readonly" if priority_enabled else "disabled")
        for widget in getattr(self, "_center_zone_priority_value_widgets", ()):
            widget.configure(state="normal" if priority_enabled else "disabled")

        for widget in getattr(self, "_center_zone_restricted_checkbuttons", {}).values():
            widget.configure(state="normal" if restrictions_enabled else "disabled")
        for vehicle_type, widget in getattr(self, "_center_zone_penalty_entries", {}).items():
            selected_var = self.center_zone_restricted_vars.get(vehicle_type)
            selected = bool(selected_var.get()) if selected_var is not None else False
            widget.configure(state="normal" if restrictions_enabled and selected else "disabled")

    def _append_center_zone_from_fields(self):
        name_widget = self.widgets.get("locations.new_center_zone_name")
        mode_widget = self.widgets.get("locations.new_center_zone_mode")
        coords_widget = self.widgets.get("locations.new_center_zone_coords")
        radius_widget = self.widgets.get("locations.new_center_zone_radius")
        discount_widget = self.widgets.get("locations.new_center_zone_discount")
        outside_widget = self.widgets.get("locations.new_center_zone_outside_penalty")
        enabled_widget = self.widgets.get("locations.new_center_zone_enabled")
        show_on_map_widget = self.widgets.get("locations.new_center_zone_show_on_map")
        enable_priority_widget = self.widgets.get("locations.new_center_zone_enable_priority")
        enable_restrictions_widget = self.widgets.get("locations.new_center_zone_enable_restrictions")
        zones_widget = self.widgets.get("locations.center_zones")
        if not all((name_widget, mode_widget, coords_widget, radius_widget, discount_widget, outside_widget)):
            return
        if not isinstance(zones_widget, tk.Text):
            return

        try:
            zones = self._parse_center_zones_text(zones_widget.get("1.0", "end-1c"), strict=True)
        except ValueError as exc:
            messagebox.showerror("Грешка в център зоните", str(exc))
            return
        name = name_widget.get().strip() or self._next_center_zone_name(zones)
        mode = str(mode_widget.get() or "circle").strip().lower()

        raw_coords = (
            coords_widget.get("1.0", "end-1c")
            if isinstance(coords_widget, tk.Text)
            else coords_widget.get()
        )
        try:
            center_coords, radius, polygon = self._parse_center_zone_geometry(
                mode,
                raw_coords,
                radius_widget.get(),
            )
        except ValueError as exc:
            messagebox.showwarning("Грешна геометрия", str(exc))
            return

        import math
        try:
            discount = float(discount_widget.get() or 0.9)
            outside_penalty = float(outside_widget.get() or 0)
        except (TypeError, ValueError):
            messagebox.showwarning("Грешна център зона", "Отстъпката и глобата навън трябва да са числа.")
            return
        if not math.isfinite(discount) or discount <= 0:
            messagebox.showwarning("Грешна отстъпка", "Отстъпката трябва да е положително число.")
            return
        if not math.isfinite(outside_penalty) or outside_penalty < 0:
            messagebox.showwarning("Грешна глоба", "Глобата навън трябва да е число, по-голямо или равно на 0.")
            return

        priority_types = [
            vehicle_type
            for vehicle_type, var in self.center_zone_priority_vars.items()
            if var.get()
        ]
        restricted_types = [
            vehicle_type
            for vehicle_type, var in self.center_zone_restricted_vars.items()
            if var.get()
        ]
        enable_priority = bool(enable_priority_widget.get()) if enable_priority_widget is not None else True
        enable_restrictions = (
            bool(enable_restrictions_widget.get()) if enable_restrictions_widget is not None else True
        )
        if enable_priority and not priority_types:
            messagebox.showwarning("Няма приоритет", "Избери поне един приоритетен тип бус или изключи приоритетните правила.")
            return
        if enable_restrictions and not restricted_types:
            messagebox.showwarning("Няма ограничения", "Избери поне един ограничен тип бус или изключи ограниченията.")
            return

        vehicle_penalties = dict(getattr(self, "_center_zone_unmapped_penalties", {}) or {})
        loaded_penalty_keys = set(getattr(self, "_center_zone_loaded_penalty_keys", set()) or set())
        penalty_vehicle_types = [
            vehicle_type
            for vehicle_type in getattr(self, "center_zone_penalty_vars", {})
            if vehicle_type in restricted_types or vehicle_type in loaded_penalty_keys
        ]
        for vehicle_type in penalty_vehicle_types:
            penalty_var = getattr(self, "center_zone_penalty_vars", {}).get(vehicle_type)
            raw_penalty = penalty_var.get() if penalty_var is not None else "0"
            try:
                penalty = float(raw_penalty)
            except (TypeError, ValueError):
                messagebox.showwarning("Грешна глоба", f"Глобата за {vehicle_type} трябва да е число.")
                return
            if not math.isfinite(penalty) or penalty < 0:
                messagebox.showwarning("Грешна глоба", f"Глобата за {vehicle_type} трябва да е поне 0.")
                return
            vehicle_penalties[vehicle_type] = penalty

        edit_index = getattr(self, "_editing_center_zone_index", None)
        if edit_index is not None and not 0 <= edit_index < len(zones):
            edit_index = None
        duplicate_index = next((idx for idx, item in enumerate(zones) if item.name == name), None)
        if duplicate_index is not None and duplicate_index != edit_index:
            messagebox.showwarning("Повтарящо се име", f"Вече има център зона с име '{name}'.")
            return

        zone = config.CenterZoneConfig(
            name=name,
            mode=mode,
            center_coords=center_coords,
            radius_km=radius,
            polygon=polygon,
            enabled=bool(enabled_widget.get()) if enabled_widget is not None else True,
            show_on_map=bool(show_on_map_widget.get()) if show_on_map_widget is not None else True,
            enable_priority=enable_priority,
            enable_restrictions=enable_restrictions,
            priority_vehicle_types=priority_types,
            restricted_vehicle_types=restricted_types,
            discount_priority_vehicle=discount,
            priority_vehicle_outside_penalty=outside_penalty,
            vehicle_penalties=vehicle_penalties,
        )
        if edit_index is None:
            zones.append(zone)
            action = "добавена"
        else:
            zones[edit_index] = zone
            action = "обновена"
        self._sync_center_zone_widgets(zones)
        self._clear_center_zone_form(set_status=False)
        self.status_var.set(f"Център зона '{name}' е {action}. Натисни Запази, за да влезе в config.py.")

    def _load_selected_center_zone(self, event=None):
        zones_widget = self.widgets.get("locations.center_zones")
        if not isinstance(zones_widget, tk.Text):
            return
        index = self._selected_tree_index(self.center_zone_tree)
        if index is None:
            index = self._selected_listbox_index(self.center_zone_listbox)
        if index is None:
            return
        zones = self._parse_center_zones_text(zones_widget.get("1.0", "end-1c"))
        if index >= len(zones):
            return
        zone = zones[index]
        self._editing_center_zone_index = index

        name_widget = self.widgets.get("locations.new_center_zone_name")
        mode_widget = self.widgets.get("locations.new_center_zone_mode")
        coords_widget = self.widgets.get("locations.new_center_zone_coords")
        radius_widget = self.widgets.get("locations.new_center_zone_radius")
        discount_widget = self.widgets.get("locations.new_center_zone_discount")
        outside_widget = self.widgets.get("locations.new_center_zone_outside_penalty")
        enabled_widget = self.widgets.get("locations.new_center_zone_enabled")
        show_on_map_widget = self.widgets.get("locations.new_center_zone_show_on_map")
        enable_priority_widget = self.widgets.get("locations.new_center_zone_enable_priority")
        enable_restrictions_widget = self.widgets.get("locations.new_center_zone_enable_restrictions")
        mode = str(getattr(zone, "mode", "circle") or "circle").lower()

        if name_widget:
            name_widget.set(getattr(zone, "name", ""))
        if mode_widget:
            mode_widget.set(mode)
        if isinstance(coords_widget, tk.Text):
            coords_widget.delete("1.0", "end")
            if mode == "polygon":
                coords_widget.insert("1.0", self._format_polygon_text(getattr(zone, "polygon", []) or []))
            else:
                center = getattr(zone, "center_coords", (0, 0))
                coords_widget.insert("1.0", f"{float(center[0])}, {float(center[1])}")
        if radius_widget:
            radius_widget.set(f"{float(getattr(zone, 'radius_km', 0) or 0):g}")
        if discount_widget:
            discount_widget.set(f"{float(getattr(zone, 'discount_priority_vehicle', 0.9) or 0.9):g}")
        if outside_widget:
            outside_widget.set(f"{float(getattr(zone, 'priority_vehicle_outside_penalty', 0.0) or 0.0):g}")
        if enabled_widget is not None:
            enabled_widget.set(bool(getattr(zone, "enabled", True)))
        if show_on_map_widget is not None:
            show_on_map_widget.set(bool(getattr(zone, "show_on_map", True)))
        if enable_priority_widget is not None:
            enable_priority_widget.set(bool(getattr(zone, "enable_priority", True)))
        if enable_restrictions_widget is not None:
            enable_restrictions_widget.set(bool(getattr(zone, "enable_restrictions", True)))

        penalties = getattr(zone, "vehicle_penalties", {}) or {}
        self._center_zone_loaded_penalty_keys = set(penalties)
        known_vehicle_types = set(getattr(self, "center_zone_penalty_vars", {}))
        self._center_zone_unmapped_penalties = {
            vehicle_type: float(value)
            for vehicle_type, value in penalties.items()
            if vehicle_type not in known_vehicle_types
        }
        for vehicle_type, penalty_var in getattr(self, "center_zone_penalty_vars", {}).items():
            penalty_var.set(f"{float(penalties.get(vehicle_type, 40000.0)):g}")

        priority_types = set(getattr(zone, "priority_vehicle_types", []) or [])
        restricted_types = set(getattr(zone, "restricted_vehicle_types", []) or [])
        for vehicle_type, var in self.center_zone_priority_vars.items():
            var.set(vehicle_type in priority_types)
        for vehicle_type, var in self.center_zone_restricted_vars.items():
            var.set(vehicle_type in restricted_types)

        preset_widget = self.widgets.get("locations.new_center_zone_rule_preset")
        if preset_widget is not None:
            preset_widget.set("custom")

        self._update_center_zone_shape_fields()
        self._update_center_zone_rule_fields()
        self.status_var.set(f"Заредена е център зона '{getattr(zone, 'name', '')}' за редакция.")

    def _clear_center_zone_form(self, set_status=True):
        name_widget = self.widgets.get("locations.new_center_zone_name")
        mode_widget = self.widgets.get("locations.new_center_zone_mode")
        coords_widget = self.widgets.get("locations.new_center_zone_coords")
        radius_widget = self.widgets.get("locations.new_center_zone_radius")
        discount_widget = self.widgets.get("locations.new_center_zone_discount")
        outside_widget = self.widgets.get("locations.new_center_zone_outside_penalty")
        enabled_widget = self.widgets.get("locations.new_center_zone_enabled")
        show_on_map_widget = self.widgets.get("locations.new_center_zone_show_on_map")
        enable_priority_widget = self.widgets.get("locations.new_center_zone_enable_priority")
        enable_restrictions_widget = self.widgets.get("locations.new_center_zone_enable_restrictions")
        self._editing_center_zone_index = None
        self._center_zone_unmapped_penalties = {}
        self._center_zone_loaded_penalty_keys = set()
        if name_widget:
            name_widget.set("")
        if mode_widget:
            mode_widget.set("circle")
        if isinstance(coords_widget, tk.Text):
            coords_widget.delete("1.0", "end")
        if radius_widget:
            radius_widget.set("1.5")
        if discount_widget:
            discount_widget.set("0.9")
        if outside_widget:
            outside_widget.set("0")
        if enabled_widget is not None:
            enabled_widget.set(True)
        if show_on_map_widget is not None:
            show_on_map_widget.set(True)
        if enable_priority_widget is not None:
            enable_priority_widget.set(True)
        if enable_restrictions_widget is not None:
            enable_restrictions_widget.set(True)
        for penalty_var in getattr(self, "center_zone_penalty_vars", {}).values():
            penalty_var.set("40000")
        for vehicle_type, var in self.center_zone_priority_vars.items():
            var.set(vehicle_type == "center_bus")
        for vehicle_type, var in self.center_zone_restricted_vars.items():
            var.set(vehicle_type != "center_bus")
        if self.center_zone_listbox is not None:
            self.center_zone_listbox.selection_clear(0, "end")
        self._clear_tree_selection(self.center_zone_tree)
        preset_widget = self.widgets.get("locations.new_center_zone_rule_preset")
        if preset_widget is not None:
            preset_widget.set("center_bus")
        self._update_center_zone_shape_fields()
        self._update_center_zone_rule_fields()
        if set_status:
            self.status_var.set("Формата за център зона е изчистена.")

    def _apply_center_zone_rule_preset(self, event=None):
        preset_widget = self.widgets.get("locations.new_center_zone_rule_preset")
        preset = str(preset_widget.get() if preset_widget is not None else "custom").strip()
        if preset == "custom":
            return

        for vehicle_type, var in self.center_zone_priority_vars.items():
            var.set(False)
        for vehicle_type, var in self.center_zone_restricted_vars.items():
            var.set(False)

        if preset in self.center_zone_priority_vars:
            self.center_zone_priority_vars[preset].set(True)
        for vehicle_type, var in self.center_zone_restricted_vars.items():
            var.set(vehicle_type != preset)
        self._update_center_zone_rule_fields()

    def _show_selected_center_zone_on_map(self):
        self._load_selected_center_zone()
        coords_widget = self.widgets.get("locations.new_center_zone_coords")
        raw_coords = coords_widget.get("1.0", "end-1c") if isinstance(coords_widget, tk.Text) else ""
        if not raw_coords.strip():
            messagebox.showinfo("Няма избрана зона", "Избери център зона от таблицата.")
            return
        self._open_new_center_zone_editor()

    def _toggle_selected_traffic_zone_visibility(self):
        zones_widget = self.widgets.get("locations.traffic_zones")
        index = self._selected_tree_index(self.traffic_zone_tree)
        if not isinstance(zones_widget, tk.Text) or index is None:
            messagebox.showinfo("Няма избрана зона", "Избери трафик зона от таблицата.")
            return
        zones = self._parse_traffic_zones_text(zones_widget.get("1.0", "end-1c"))
        if not 0 <= index < len(zones):
            return
        zone = zones[index]
        zone.show_on_map = not bool(getattr(zone, "show_on_map", False))
        self._sync_traffic_zone_widgets(zones)
        if self.traffic_zone_tree is not None:
            self.traffic_zone_tree.selection_set(str(index))
        self.status_var.set(
            f"Зона '{zone.name}' {'ще се показва' if zone.show_on_map else 'няма да се показва'} "
            "на картата след Запази."
        )

    def _toggle_selected_center_zone_visibility(self):
        zones_widget = self.widgets.get("locations.center_zones")
        index = self._selected_tree_index(self.center_zone_tree)
        if not isinstance(zones_widget, tk.Text) or index is None:
            messagebox.showinfo("Няма избрана зона", "Избери център зона от таблицата.")
            return
        zones = self._parse_center_zones_text(zones_widget.get("1.0", "end-1c"), strict=True)
        if not 0 <= index < len(zones):
            return
        zone = zones[index]
        zone.show_on_map = not bool(getattr(zone, "show_on_map", True))
        self._sync_center_zone_widgets(zones)
        if self.center_zone_tree is not None:
            self.center_zone_tree.selection_set(str(index))
        self.status_var.set(
            f"Зона '{zone.name}' {'ще се показва' if zone.show_on_map else 'няма да се показва'} "
            "на картата след Запази."
        )

    def _remove_selected_traffic_zone(self):
        zones_widget = self.widgets.get("locations.traffic_zones")
        if not isinstance(zones_widget, tk.Text):
            return
        index = self._selected_tree_index(self.traffic_zone_tree)
        if index is None:
            index = self._selected_listbox_index(self.traffic_zone_listbox)
        if index is None:
            messagebox.showinfo("Няма избрана зона", "Избери зона от списъка.")
            return

        zones = self._parse_traffic_zones_text(zones_widget.get("1.0", "end-1c"))
        if index >= len(zones):
            return
        removed = zones.pop(index)
        self._sync_traffic_zone_widgets(zones)
        self.status_var.set(f"Премахната е {removed.name}. Натисни Запази, за да се махне от config.py.")

    def _remove_selected_center_zone(self):
        zones_widget = self.widgets.get("locations.center_zones")
        if not isinstance(zones_widget, tk.Text):
            return
        index = self._selected_tree_index(self.center_zone_tree)
        if index is None:
            index = self._selected_listbox_index(self.center_zone_listbox)
        if index is None:
            messagebox.showinfo("Няма избрана зона", "Избери център зона от списъка.")
            return

        zones = self._parse_center_zones_text(zones_widget.get("1.0", "end-1c"))
        if index >= len(zones):
            return
        removed = zones.pop(index)
        self._sync_center_zone_widgets(zones)
        self._clear_center_zone_form(set_status=False)
        self.status_var.set(f"Премахната е {removed.name}. Натисни Запази, за да се махне от config.py.")

    def _add_depot_choice_field(self, parent, row, key, label, value, options, tooltip=""):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, anchor="w", width=28, wraplength=230, style="Surface.TLabel").grid(
            row=row, column=0, sticky="nw", padx=(8, 12), pady=6
        )
        var = tk.StringVar(value=value)
        combo = ttk.Combobox(parent, textvariable=var, values=options, state="normal", width=34)
        combo.grid(row=row, column=1, sticky="w", padx=6, pady=6)
        self._bind_text_editing(combo)
        self._bind_combobox_scrolling(combo)
        self.widgets[key] = var
        if key.endswith(".start_depot_name"):
            self.depot_choice_widgets.append(combo)
        if tooltip:
            ttk.Label(parent, text=tooltip, style="Hint.TLabel", wraplength=330).grid(
                row=row, column=2, sticky="nw", padx=(10, 4), pady=6
            )

    def _add_polygon_field(self, parent, row, key, label, polygon):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, anchor="w", style="Surface.TLabel").grid(
            row=row, column=0, sticky="nw", padx=(8, 10), pady=5
        )
        text_w = tk.Text(parent, width=42, height=6, wrap="none", relief="solid", borderwidth=1)
        text_w.insert("1.0", self._format_polygon_text(polygon or []))
        text_w.grid(row=row, column=1, sticky="we", padx=6, pady=5)
        self._bind_text_editing(text_w)
        self.widgets[key] = text_w

        box = ttk.Frame(parent, style="Surface.TFrame")
        box.grid(row=row, column=2, sticky="nw", padx=(8, 4), pady=5)
        ttk.Button(box, text="Чертай на карта", command=self._open_center_zone_editor).pack(anchor="w")
        ttk.Label(
            box,
            text="Една точка на ред: lat, lon",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(4, 0))

    def _parse_polygon_text(self, raw):
        raw = (raw or "").strip()
        if not raw:
            return []

        if raw.startswith("["):
            try:
                data = json.loads(raw)
                return [(float(item[0]), float(item[1])) for item in data]
            except Exception:
                return []

        points = []
        for line in raw.splitlines():
            line = line.strip().strip("[]()")
            if not line:
                continue
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                points.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
        return points

    def _set_center_zone_polygon(self, polygon):
        widget = self.widgets.get("locations.center_zone_polygon")
        if not isinstance(widget, tk.Text):
            return
        widget.delete("1.0", "end")
        widget.insert("1.0", self._format_polygon_text(polygon))

        mode_widget = self.widgets.get("locations.center_zone_mode")
        if mode_widget is not None:
            mode_widget.set("polygon")

    def _set_new_center_zone_polygon(self, polygon):
        widget = self.widgets.get("locations.new_center_zone_coords")
        if not isinstance(widget, tk.Text):
            return
        widget.delete("1.0", "end")
        widget.insert("1.0", self._format_polygon_text(polygon))

        mode_widget = self.widgets.get("locations.new_center_zone_mode")
        if mode_widget is not None:
            mode_widget.set("polygon")
        self._update_center_zone_shape_fields()

    def _set_new_depot_coords(self, coords):
        widget = self.widgets.get("locations.new_depot_coords")
        if widget is not None:
            widget.set(f"{float(coords[0])}, {float(coords[1])}")

    def _set_new_traffic_zone_circle(self, center, radius):
        coords_widget = self.widgets.get("locations.new_traffic_zone_coords")
        radius_widget = self.widgets.get("locations.new_traffic_zone_radius")
        if coords_widget is not None:
            coords_widget.set(f"{float(center[0])}, {float(center[1])}")
        if radius_widget is not None:
            radius_widget.set(f"{float(radius):g}")

    def _set_new_center_zone_circle(self, center, radius):
        coords_widget = self.widgets.get("locations.new_center_zone_coords")
        radius_widget = self.widgets.get("locations.new_center_zone_radius")
        if isinstance(coords_widget, tk.Text):
            coords_widget.delete("1.0", "end")
            coords_widget.insert("1.0", f"{float(center[0])}, {float(center[1])}")
        if radius_widget is not None:
            radius_widget.set(f"{float(radius):g}")
        mode_widget = self.widgets.get("locations.new_center_zone_mode")
        if mode_widget is not None:
            mode_widget.set("circle")
        self._update_center_zone_shape_fields()

    def _default_map_center(self):
        center_widget = self.widgets.get("locations.center_location")
        if center_widget is not None:
            try:
                parts = [float(part.strip()) for part in center_widget.get().split(",")[:2]]
                if len(parts) == 2:
                    return [parts[0], parts[1]]
            except Exception:
                pass
        return [42.69735652560932, 23.323809998750914]

    def _open_center_zone_editor(self):
        center = self._default_map_center()

        polygon_widget = self.widgets.get("locations.center_zone_polygon")
        polygon_raw = polygon_widget.get("1.0", "end-1c") if isinstance(polygon_widget, tk.Text) else ""
        polygon = self._parse_polygon_text(polygon_raw)

        self._open_polygon_editor(center, polygon, self._set_center_zone_polygon)

    def _open_new_depot_editor(self):
        coords_widget = self.widgets.get("locations.new_depot_coords")
        coords = self._parse_coords_text(coords_widget.get()) if coords_widget is not None else None
        center = [coords[0], coords[1]] if coords else self._default_map_center()
        self._open_point_editor(center, self._set_new_depot_coords, "Избор на депо")

    def _open_new_traffic_zone_editor(self):
        coords_widget = self.widgets.get("locations.new_traffic_zone_coords")
        radius_widget = self.widgets.get("locations.new_traffic_zone_radius")
        coords = self._parse_coords_text(coords_widget.get()) if coords_widget is not None else None
        center = [coords[0], coords[1]] if coords else self._default_map_center()
        try:
            radius = float(radius_widget.get() or 3) if radius_widget is not None else 3.0
        except ValueError:
            radius = 3.0
        self._open_circle_editor(center, radius, self._set_new_traffic_zone_circle, "Трафик зона")

    def _open_new_center_zone_editor(self):
        mode_widget = self.widgets.get("locations.new_center_zone_mode")
        mode = str(mode_widget.get() if mode_widget is not None else "circle").strip().lower()
        coords_widget = self.widgets.get("locations.new_center_zone_coords")
        raw_coords = coords_widget.get("1.0", "end-1c") if isinstance(coords_widget, tk.Text) else ""
        center_coords = self._parse_coords_text(raw_coords)
        center = [center_coords[0], center_coords[1]] if center_coords else self._default_map_center()
        if mode == "circle":
            radius_widget = self.widgets.get("locations.new_center_zone_radius")
            try:
                radius = float(radius_widget.get() or 1.5) if radius_widget is not None else 1.5
            except ValueError:
                radius = 1.5
            self._open_circle_editor(center, radius, self._set_new_center_zone_circle, "Център зона")
            return

        polygon = self._parse_polygon_points_text(raw_coords)
        if polygon:
            center = [polygon[0][0], polygon[0][1]]
        elif center_coords:
            center = [center_coords[0], center_coords[1]]
        else:
            center = self._default_map_center()

        self._open_polygon_editor(center, polygon, self._set_new_center_zone_polygon)

    def _open_point_editor(self, center, on_save_callback, title):
        gui = self
        server_box = {}

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, body, content_type="text/html; charset=utf-8"):
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                self._send(200, gui._point_editor_html(center, title))

            def do_POST(self):
                if self.path != "/save":
                    self._send(404, "Not found", "text/plain; charset=utf-8")
                    return

                length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(length).decode("utf-8")
                try:
                    data = json.loads(payload)
                    point = data.get("point", [])
                    saved_point = (float(point[0]), float(point[1]))
                    gui.root.after(0, lambda: on_save_callback(saved_point))
                    self._send(200, "OK", "text/plain; charset=utf-8")
                    threading.Thread(target=server_box["server"].shutdown, daemon=True).start()
                except Exception as exc:
                    self._send(400, str(exc), "text/plain; charset=utf-8")

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_box["server"] = server
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        webbrowser.open(f"http://127.0.0.1:{port}/")

    def _point_editor_html(self, center, title):
        center_json = json.dumps(center)
        title_json = json.dumps(title, ensure_ascii=False)
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    body {{ font-family: Arial, sans-serif; }}
    #panel {{
      position: absolute; top: 10px; left: 50px; z-index: 1000;
      background: white; border: 1px solid #777; border-radius: 4px;
      padding: 8px; box-shadow: 0 2px 10px rgba(0,0,0,.25);
    }}
    button {{ padding: 6px 10px; cursor: pointer; }}
    #coords {{ margin-left: 8px; color: #255; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="panel">
    <button onclick="savePoint()">Запази точката</button>
    <span id="coords"></span>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const center = {center_json};
    const title = {title_json};
    const map = L.map("map").setView(center, 13);
    L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap"
    }}).addTo(map);

    const marker = L.marker(center, {{ draggable: true }}).addTo(map).bindPopup(title).openPopup();

    function updateLabel() {{
      const p = marker.getLatLng();
      document.getElementById("coords").textContent = `${{p.lat.toFixed(6)}}, ${{p.lng.toFixed(6)}}`;
    }}

    marker.on("drag", updateLabel);
    map.on("click", function(event) {{
      marker.setLatLng(event.latlng);
      updateLabel();
    }});
    updateLabel();

    async function savePoint() {{
      const p = marker.getLatLng();
      const point = [Number(p.lat.toFixed(8)), Number(p.lng.toFixed(8))];
      const response = await fetch("/save", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ point }})
      }});
      document.getElementById("coords").textContent = response.ok
        ? "Запазено в GUI. Можеш да затвориш този прозорец."
        : "Грешка при запис.";
    }}
  </script>
</body>
</html>"""

    def _open_circle_editor(self, center, radius_km, on_save_callback, title):
        gui = self
        server_box = {}

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, body, content_type="text/html; charset=utf-8"):
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                self._send(200, gui._circle_editor_html(center, radius_km, title))

            def do_POST(self):
                if self.path != "/save":
                    self._send(404, "Not found", "text/plain; charset=utf-8")
                    return

                length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(length).decode("utf-8")
                try:
                    data = json.loads(payload)
                    center_point = data.get("center", [])
                    saved_center = (float(center_point[0]), float(center_point[1]))
                    saved_radius = float(data.get("radius_km", 0))
                    gui.root.after(0, lambda: on_save_callback(saved_center, saved_radius))
                    self._send(200, "OK", "text/plain; charset=utf-8")
                    threading.Thread(target=server_box["server"].shutdown, daemon=True).start()
                except Exception as exc:
                    self._send(400, str(exc), "text/plain; charset=utf-8")

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_box["server"] = server
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        webbrowser.open(f"http://127.0.0.1:{port}/")

    def _circle_editor_html(self, center, radius_km, title):
        center_json = json.dumps(center)
        radius = max(0.1, float(radius_km or 1.0))
        title_json = json.dumps(title, ensure_ascii=False)
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    body {{ font-family: Arial, sans-serif; }}
    #panel {{
      position: absolute; top: 10px; left: 50px; z-index: 1000;
      background: white; border: 1px solid #777; border-radius: 4px;
      padding: 8px; box-shadow: 0 2px 10px rgba(0,0,0,.25);
      display: flex; align-items: center; gap: 8px;
    }}
    input {{ width: 80px; padding: 5px; }}
    button {{ padding: 6px 10px; cursor: pointer; }}
    #status {{ color: #255; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="panel">
    <button onclick="saveCircle()">Запази зоната</button>
    <label>Радиус км <input id="radius" type="number" min="0.1" step="0.1" value="{radius:g}"></label>
    <span id="status"></span>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const center = {center_json};
    const title = {title_json};
    const map = L.map("map").setView(center, 13);
    L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap"
    }}).addTo(map);

    const marker = L.marker(center, {{ draggable: true }}).addTo(map).bindPopup(title).openPopup();
    const circle = L.circle(center, {{
      radius: Number(document.getElementById("radius").value) * 1000,
      color: "#2563eb",
      fillColor: "#2563eb",
      fillOpacity: 0.14
    }}).addTo(map);

    function updateCircle() {{
      const p = marker.getLatLng();
      const radiusKm = Number(document.getElementById("radius").value || 0);
      circle.setLatLng(p);
      circle.setRadius(Math.max(0.1, radiusKm) * 1000);
      document.getElementById("status").textContent = `${{p.lat.toFixed(6)}}, ${{p.lng.toFixed(6)}}`;
    }}

    marker.on("drag", updateCircle);
    document.getElementById("radius").addEventListener("input", updateCircle);
    map.on("click", function(event) {{
      marker.setLatLng(event.latlng);
      updateCircle();
    }});
    updateCircle();
    try {{ map.fitBounds(circle.getBounds(), {{ padding: [20, 20] }}); }} catch (err) {{}}

    async function saveCircle() {{
      const p = marker.getLatLng();
      const radiusKm = Math.max(0.1, Number(document.getElementById("radius").value || 0));
      const centerPoint = [Number(p.lat.toFixed(8)), Number(p.lng.toFixed(8))];
      const response = await fetch("/save", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ center: centerPoint, radius_km: radiusKm }})
      }});
      document.getElementById("status").textContent = response.ok
        ? "Запазено в GUI. Можеш да затвориш този прозорец."
        : "Грешка при запис.";
    }}
  </script>
</body>
</html>"""

    def _open_polygon_editor(self, center, polygon, on_save_callback):
        gui = self
        server_box = {}

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, body, content_type="text/html; charset=utf-8"):
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                self._send(200, gui._center_zone_editor_html(center, polygon))

            def do_POST(self):
                if self.path != "/save":
                    self._send(404, "Not found", "text/plain; charset=utf-8")
                    return

                length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(length).decode("utf-8")
                try:
                    data = json.loads(payload)
                    saved_polygon = [
                        (float(point[0]), float(point[1]))
                        for point in data.get("polygon", [])
                    ]
                    gui.root.after(0, lambda: on_save_callback(saved_polygon))
                    self._send(200, "OK", "text/plain; charset=utf-8")
                    threading.Thread(target=server_box["server"].shutdown, daemon=True).start()
                except Exception as exc:
                    self._send(400, str(exc), "text/plain; charset=utf-8")

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_box["server"] = server
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        webbrowser.open(f"http://127.0.0.1:{port}/")

    def _center_zone_editor_html(self, center, polygon):
        center_json = json.dumps(center)
        polygon_json = json.dumps(polygon)
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Център зона</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    body {{ font-family: Arial, sans-serif; }}
    #panel {{
      position: absolute; top: 10px; left: 50px; z-index: 1000;
      background: white; border: 1px solid #777; border-radius: 4px;
      padding: 8px; box-shadow: 0 2px 10px rgba(0,0,0,.25);
    }}
    button {{ padding: 6px 10px; cursor: pointer; }}
    #status {{ margin-left: 8px; color: #255; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="panel">
    <button onclick="savePolygon()">Запази зоната</button>
    <span id="status">Начертай или редактирай полигон.</span>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
  <script>
    const center = {center_json};
    const existingPolygon = {polygon_json};
    const map = L.map("map").setView(center, 13);
    L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap"
    }}).addTo(map);

    const drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    if (existingPolygon.length >= 3) {{
      const layer = L.polygon(existingPolygon, {{ color: "#d32f2f", fillOpacity: 0.15 }});
      drawnItems.addLayer(layer);
      map.fitBounds(layer.getBounds(), {{ padding: [20, 20] }});
    }}

    L.marker(center).addTo(map).bindPopup("Център");

    const drawControl = new L.Control.Draw({{
      draw: {{
        polygon: {{ allowIntersection: false, showArea: true, shapeOptions: {{ color: "#d32f2f" }} }},
        polyline: false,
        rectangle: false,
        circle: false,
        marker: false,
        circlemarker: false
      }},
      edit: {{ featureGroup: drawnItems, remove: true }}
    }});
    map.addControl(drawControl);

    map.on(L.Draw.Event.CREATED, function (event) {{
      drawnItems.clearLayers();
      drawnItems.addLayer(event.layer);
    }});

    function currentPolygon() {{
      let coords = [];
      drawnItems.eachLayer(function(layer) {{
        const latLngs = layer.getLatLngs()[0] || [];
        coords = latLngs.map(function(p) {{ return [Number(p.lat.toFixed(8)), Number(p.lng.toFixed(8))]; }});
      }});
      return coords;
    }}

    async function savePolygon() {{
      const polygon = currentPolygon();
      if (polygon.length < 3) {{
        document.getElementById("status").textContent = "Нужни са поне 3 точки.";
        return;
      }}
      const response = await fetch("/save", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ polygon }})
      }});
      document.getElementById("status").textContent = response.ok
        ? "Запазено в GUI. Можеш да затвориш този прозорец."
        : "Грешка при запис.";
    }}
  </script>
</body>
</html>"""

    def _program_guide_blocks(self):
        return [
            (
                "Първо прочети това",
                """
Това ръководство е подредено по начина, по който реално се работи с програмата.

Ако си оператор и искаш просто да пуснеш маршрут:
  1. Провери Входни данни.
  2. Провери Превозни средства.
  3. Провери Солвър.
  4. Провери Изходни данни.
  5. Натисни Запази и стартирай или извикай /run.

Ако искаш да управляваш програмата през API:
  1. Отвори Разширени -> API сървър.
  2. Настрой host, port, public URL и API ключ.
  3. Пускай POST /solve, ако подаваш клиенти.
  4. Пускай POST /run, ако програмата сама зарежда клиентите от input_source.
  5. Подай settings за бусове, депа, зони, output и setData в същия JSON.

Ако един клиент има няколко документа:
  По подразбиране програмата групира редове със същия IdCust и същия GPS в един стоп.
  Обемът се събира, solver-ът посещава клиента веднъж, а setData връща отделна заявка за всеки оригинален документ.
  Изключва се от Входни данни -> Групирай документи или през API с input.enable_customer_document_grouping=false.

Ако търсиш защо резултатът е лош:
  1. Провери дали депата са правилни.
  2. Провери OSRM матрицата.
  3. Провери center_zone и traffic_zones.
  4. Провери дали склад/предварителна оптимизация не е махнала клиенти.
  5. Провери time_limit_seconds и seed.

Правило:
  Не започвай с фините solver настройки, ако има съмнение в данните, депата или матрицата.
                """,
                33,
            ),
            (
                "Карта на GUI-то",
                """
Входни данни:
  Откъде идват клиентите и как се четат полетата им.

Превозни средства:
  Бусове, брой, капацитет, имена, стартови депа и работни ограничения.

Предварителна оптимизация:
  Какво се отделя към склад преди solver-а.

Солвър:
  Избор PyVRP/OR-Tools/VROOM/VRP-Rust, време, пропускане, работно време, OSRM/Valhalla и фини настройки.

Локации:
  Главно депо, Враца, допълнителни депа, център зона, трафик зони и глоби.

Изходни данни:
  HTML карти, route HTML файлове, Excel, CSV, графики и директории.

Разширени:
  API сървър:
    API управление, JSON команди, settings schema и examples.
  setData:
    Връщане на готовите маршрути към Bizant.
  Автоматично стартиране:
    Windows Scheduled Task.
  Ръководство:
    Тази документация.

Горни бутони:
  Запази:
    Записва настройките в config.py.
  Запази и затвори:
    Записва и затваря GUI.
  Запази и стартирай:
    Записва и стартира оптимизацията локално.
  Отказ:
    Затваря без нов запис.
                """,
                36,
            ),
            (
                "Най-чести работни рецепти",
                """
Искам само да стартирам програмата с текущите настройки:
  GET /run
  или бутон Запази и стартирай.

Искам да подам клиенти и да получа JSON решение:
  POST /solve
  Body: { "settings": { ... }, "customers": [...] }

Искам програмата да зареди клиентите сама, но да сменя настройки за този run:
  POST /run
  Body: { "settings": { ... } }

Искам /run да върне JSON резултат директно:
  POST /run
  Body: { "return_result": true, "settings": { ... } }

Искам run във фонов режим и известие след края:
  POST /run
  Body: { "callback_url": "https://...", "settings": { ... } }

Искам да сменя само броя бусове:
  "vehicle_counts": { "internal_bus": 7, "vratza_bus": 3 }

Искам да сменя депата през API:
  "depots": {
    "main": [42.6957, 23.2316],
    "vratza": [43.2210, 23.5344]
  }

Искам да забраня setData при тест:
  "set_data": { "enable_set_data_upload": false }

Искам различна output папка за всеки run:
  "output": {
    "excel_output_dir": "H:/Run1",
    "routes_output_dir": "H:/Run1/Routes",
    "csv_output_file": "H:/Run1/routes.csv"
  }
                """,
                38,
            ),
            (
                "Как програмата взима решение",
                """
Solver-ът не разбира карта като човек.
Той вижда:
  - списък депа;
  - списък клиенти;
  - брой и капацитет на бусове;
  - матрица разстояние/време между всички точки;
  - ограничения;
  - глоби и разходи.

Целта е да намери допустимо решение с най-ниска цена.

В цената участват:
  - километри/време според матрицата;
  - глоби за пропуснати клиенти;
  - глоби за център зона;
  - fixed cost за използван бус;
  - ограничения за капацитет, време, работно време и брой клиенти.

Ако резултатът изглежда странен:
  1. Първо провери входните данни.
  2. После депата.
  3. После матрицата.
  4. После ограниченията.
  5. Накрая solver фините настройки.

Две депа:
  При две депа качеството зависи много от правилния ред на депата в матрицата,
  правилното start_location на бусовете и достатъчно време/seed-ове за PyVRP.
                """,
                31,
            ),
            (
                "Работен checklist преди пускане",
                """
Преди да пуснеш реален маршрут, провери тези неща в този ред:

Вход:
  - Източникът е правилен: Excel, HTTP JSON или API.
  - Датата е правилна.
  - Sklad и DoneFlag са правилни.
  - GPS полето се чете правилно.
  - Клиентите с липсващ GPS не влизат в solver-а.

Бусове:
  - Броят бусове е правилен.
  - Капацитетът е правилен.
  - Имената са разбираеми за отчетите.
  - Всеки тип бус тръгва от правилно депо.

Зони:
  - Главното депо е вярно.
  - Депо Враца е вярно, ако се използва.
  - Център зоната е вярна.
  - Трафик зоните са включени само ако трябва.

OSRM/Valhalla:
  - Routing engine работи.
  - Проверен е base_url.
  - chunk_size и timeout са реалистични.
  - Кешът е разбран: включен или изключен съзнателно.

Solver:
  - Избран е правилният solver.
  - time_limit_seconds е достатъчен.
  - При сравнение използвай еднакви входни данни и настройки.

setData:
  - В тестов режим enable_set_data_upload=false.
  - Преди реално изпращане URL, метод, DoneFlag и IdSkld са проверени.

След run:
  - Прегледай общи километри и време.
  - Прегледай необслужени клиенти.
  - Отвори HTML картите.
  - Провери дали route файловете са генерирани.
  - Провери capacity utilization в Excel.
  - Ако има API callback, провери дали е върнал success/error.
                """,
                44,
            ),
            (
                "1. Какво прави програмата",
                """
CVRP Optimizer прави маршрути за бусове.

Пълният процес е:
  1. Зарежда клиенти от Excel, HTTP JSON или API.
  2. Чете GPS, обем, документ, склад и работно време.
  3. Отделя невалидни/невъзможни заявки към склад/необслужени.
  4. Строи матрица с разстояния и времена чрез OSRM или Valhalla.
  5. Решава задачата с PyVRP, OR-Tools, VROOM или VRP-Rust.
  6. Избира най-доброто решение, когато има паралелни worker-и.
  7. Генерира Excel, CSV, HTML карти, route карти и графики.
  8. По желание изпраща готовите маршрути към Bizant чрез setData.
  9. В API режим може да върне JSON резултат или callback.

Постоянни настройки:
  Това са настройките, записани през GUI в config.py.

Временни API настройки:
  Подават се в settings към /run или /solve.
  Важат само за конкретната заявка и не презаписват config.py.

Основна API идея:
  Програмата може да се управлява без отваряне на GUI.
  /run стартира със записаните настройки.
  /solve приема клиенти и настройки в една заявка.
  settings важат само за конкретното стартиране.
                """,
                24,
            ),
            (
                "2. Кога кой режим се използва",
                """
Локален GUI run:
  Използва се, когато оператор настройва програмата ръчно.
  Натиска се Запази, после програмата се стартира локално.

GET /run:
  Най-простият API старт.
  Използва текущия input_source и текущите настройки.
  Връща веднага 202 started.

POST /run:
  API старт с временни настройки.
  Данните пак се зареждат от input_source, но solver, бусове, output, депа, setData и др. могат да се override-нат.

POST /run + return_result=true:
  API старт, който чака края и връща JSON резултата.
  Удобно за интеграция, ако външната система може да чака няколко минути.

POST /run + callback_url:
  Background run. API връща веднага started.
  След края праща POST към callback_url дали е успешно или не.

POST /solve:
  Външната система подава клиентите директно.
  Това е най-пълният режим: клиенти + settings -> JSON резултат.

POST /solve?cmd=run:
  Compatibility режим, ако външна система може да вика само /solve.
                """,
                29,
            ),
            (
                "3. Входни данни",
                """
Активен източник:
  http_json:
    Програмата сама взима клиентите от външен URL.
  excel:
    Програмата чете Excel файл.
  API /solve:
    Клиентите идват директно в заявката и input_source не е важен за тях.

HTTP JSON:
  json_url:
    URL за getData.
  json_http_method:
    GET или POST.
  json_command:
    cmd параметър, например getData.
  json_date_field:
    Име на date параметъра.
  json_sklad:
    Складове, например 106,128.
  json_done_flag:
    DoneFlag.
  json_extra_query:
    Допълнителни параметри.
  json_override_date:
    DD/MM/YYYY. Празно = автоматично следващ работен ден.

JSON/Excel field mapping:
  GPS, IdCust, CustName, Volume, IdDoc, IdPlasDoc, IdSkld.

Работно време:
  WorkTime: "08:00 - 16:00"
  Поддържа и "08:00-13:00". Ако са подадени два реда, напр. 08:00-13:00 и 16:00-18:00,
  програмата нормализира и подава и двата валидни прозореца към избрания CVRP solver.
  В JSON новият ред трябва да бъде escape \\n: "08:00-13:00\\n16:00-18:00".
  Ако липсва, клиентът се приема постоянно отворен.

Коментар доставка:
  DeliveryComment / DeliveryNote / Comment се запазва към клиента и се показва в route картите и отчетите.

Клиент без GPS:
  Не се дава на solver-а, защото би разместил matrix indices.
  Отива към необслужени/склад.
                """,
                35,
            ),
            (
                "4. Предварителна оптимизация и склад",
                """
Тази секция решава кои заявки изобщо стигат до solver-а.
Това е важно, защото solver-ът не трябва да се товари с клиенти, които физически не могат да бъдат обслужени от бус.

enable_warehouse:
  Включва логиката за склад/предварителен филтър.

sort_by_volume:
  Подрежда заявките по обем.
  Полезно е, когато големите заявки трябва да се проверят първи.

sort_by_distance:
  Подрежда по разстояние до депото.
  Полезно е, когато искаш по-далечните/по-близките заявки да се обработват по-ясно.

check_max_bus_capacity:
  Ако клиентът е над капацитета на най-големия бус, не се подава към solver-а.
  Така се избягват невъзможни задачи.

max_bus_customer_volume:
  Ръчен праг за максимален обем на клиент за бус.
  Клиенти над прага отиват към склад.

capacity_toleranse:
  Толеранс при капацитет.
  Използвай внимателно, защото твърде висок толеранс може да направи маршрути, които изглеждат допустими, но са неудобни реално.

Практично правило:
  Ако липсват клиенти в решението, първо провери тази секция и отчета за склад/необслужени.
                """,
                34,
            ),
            (
                "5. Превозни средства",
                """
Типове:
  internal_bus, center_bus, external_bus, special_bus, vratza_bus.

Важни полета:
  count:
    Брой налични бусове от типа.
  capacity:
    Максимален обем.
  name:
    Име в Excel, JSON и HTML route файловете.
  fixed_cost:
    Цена за използване на бус. По-висока стойност намалява желанието да се използва този тип.
  max_distance_km:
    Максимален общ дневен пробег на физическия бус. Празно = без лимит.
  max_time_hours:
    Максимално общо работно време за деня.
  service_time_minutes:
    Време за обслужване на клиент.
  enabled:
    Дали типът е активен.
  start_location:
    Координати на стартово депо в config/API. В GUI стартът се избира по име на депо.
  end_location:
    Крайна GPS точка. Празно = същото като стартовото депо.
  start_depot_name:
    API удобство: име от depots, вместо координати.
  end_depot_name:
    API удобство: име от depots за крайна точка.
  tsp_depot_location:
    Депо за финална route подредба.
  start_time_minutes:
    480 = 08:00.
  max_customers_per_day:
    Общ брой клиенти на физическия бус през всички курсове за деня.
  max_customers_per_route:
    Legacy fallback; използвай дневния лимит за нови настройки.
  reload_location / reload_depot_name:
    Депо за връщане между курсовете.
  reload_time_minutes:
    Време за презареждане между два курса.

API варианти:
  vehicle_counts:
    Само сменя бройките.
  vehicles:
    Patch по vehicle_type.
  replace_vehicles:
    Подменя всички бусове.
                """,
                36,
            ),
            (
                "6. Депа, център зона и трафик",
                """
Депа:
  main:
    Главно депо.
  vratza:
    Депо Враца.
  Допълнителни депа:
    Именувани точки, които могат да се използват от бусове.

API пример:
  "depots": {
    "main": [42.6957, 23.2316],
    "vratza": [43.2210, 23.5344],
    "north": [42.8000, 23.4000]
  }

Бус към депо:
  "vehicles": [
    { "vehicle_type": "internal_bus", "start_depot_name": "main", "end_location": [42.7000, 23.4000] },
    { "vehicle_type": "vratza_bus", "start_depot_name": "vratza", "end_depot_name": "vratza" }
  ]

Център зона:
  mode:
    circle или polygon.
  center:
    Център на зоната.
  radius_km:
    Радиус, ако mode=circle.
  polygon:
    Точки, ако mode=polygon.
  penalties:
    internal_bus_penalty, external_bus_penalty, special_bus_penalty, vratza_bus_penalty.
  center_bus_outside_penalty:
    Глоба за center bus извън центъра.

Допълнителни център зони:
  center_zones:
    Списък от независими зони. Всяка има собствен radius/polygon и собствени правила по тип бус.
  priority_vehicle_types:
    Кои бусове получават отстъпка вътре в тази зона.
  restricted_vehicle_types:
    Кои бусове получават глоба вътре в тази зона.
  vehicle_penalties:
    Глоба по тип бус, напр. {"internal_bus": 40000, "external_bus": 40000}.

Трафик:
  city_traffic:
    Общ градски multiplier.
  traffic_zones:
    Списък от допълнителни зони с center, radius_km, multiplier, enabled.

Center zone правилата са soft цени/глоби, не абсолютна забрана. В pure time режим
точният им ефект зависи от solver-а; за надеждно влияние на отстъпките използвай distance.
Трафик зоните се прилагат само когато глобалното enable_city_traffic_adjustment е включено.
                """,
                38,
            ),
            (
                "7. Solver настройки",
                """
solver_type:
  pyvrp:
    Стабилният PyVRP 0.13.x (минимум 0.13.4) и основният качествен solver.
    Добър при повече време и различни seed-ове.
  pyvrp_experimental:
    Изолиран PyVRP 0.14 sidecar за сравнение, без да заменя stable версията.
    Изисква работещ companion worker; при липса връща грешка, освен ако fallback е разрешен.
  or_tools:
    Стабилен solver за сравнение и контрол.
  vroom:
    Официалният VROOM 1.15 sidecar за еднокурсни маршрути.
  vrp:
    Експерименталният VRP-Rust sidecar за независимо сравнение с PyVRP.
    Поддържа повторни курсове и използва готовите OSRM/Valhalla матрици.

objective_metric:
  distance:
    Solver-ът търси най-къси километри.
  time:
    Solver-ът търси най-кратко време по OSRM/Valhalla duration матрицата.

time_objective_include_waiting:
  True:
    Минимизира целия работен ден: пътуване, обслужване, чакане и презареждане.
    Fitness е само времето в секунди; реалните метри остават за ограничения и отчети.
  False:
    Запазва старото поведение: пътуване, обслужване и презареждане без директна цена за чакането.

time_limit_seconds:
  Колко секунди solver-ът търси решение.
  Повече време често помага, но не гарантира подобрение.

allow_customer_skipping:
  True:
    Solver-ът може да пропуска клиенти срещу глоба.
  False:
    Всички клиенти трябва да бъдат обслужени, ако задачата е възможна.

distance_penalty_disjunction:
  Глоба за пропускане.
  По-висока стойност = по-трудно пропускане.

enable_customer_time_windows:
  Спазва работното време на клиентите.

Паралелно решаване:
  enable_parallel_solving:
    Пуска няколко worker-а.
  num_workers:
    -1 = автоматично.
  При PyVRP worker-ите използват различни seed-ове.
  Накрая се избира най-добрият fitness score.
                """,
                37,
            ),
            (
                "8. PyVRP, OR-Tools, VROOM и VRP-Rust настройки",
                """
PyVRP:
  pyvrp_seed:
    Точен seed за повторяем резултат.
  pyvrp_seed_base:
    База за seed-ове в паралелен режим.
  pyvrp_num_neighbours:
    Размер на neighbourhood. По-голямо = по-бавно, но може да е по-добро.
  pyvrp_ils_no_improvement:
    Колко дълго търпи липса на подобрение.
  pyvrp_ils_history_length:
    ILS история.
  pyvrp_exhaustive_on_best:
    Пуска по-задълбочено локално търсене при ново най-добро решение.
  pyvrp_use_extended_operators:
    По-тежки move operators.
  pyvrp_min_perturbations / pyvrp_max_perturbations:
    Сила на разбъркване при restart.
  pyvrp_display_progress:
    Показва прогрес лог.

PyVRP experimental sidecar:
  pyvrp_next_worker_path:
    Optional път до отделния 0.14 worker. Празно = автоматично откриване до приложението.
  pyvrp_next_worker_timeout_seconds:
    0 = автоматичен timeout; положителна стойност = твърд лимит за worker процеса.
  pyvrp_next_fallback_to_stable:
    Разрешава изрична подмяна със stable 0.13 само ако experimental worker-ът се провали.

VROOM sidecar:
  vroom_worker_path:
    Optional път до VROOM worker. Празно = автоматично откриване.
  vroom_worker_timeout_seconds:
    0 = автоматичен timeout; положителна стойност = твърд лимит.
  vroom_threads:
    0 = автоматичен брой вътрешни нишки.
  vroom_exploration_level:
    Качество 0..5; 5 е най-задълбоченото търсене.

VRP-Rust experimental sidecar:
  vrp_worker_path:
    Optional път до отделния VRP-Rust worker. Празно = автоматично откриване.
  vrp_worker_timeout_seconds:
    0 = автоматичен timeout; положителна стойност = твърд лимит за worker процеса.
  vrp_threads:
    0 = автоматичен брой нишки; положителна стойност = точен брой вътрешни нишки.
  vrp_max_generations:
    Горна граница на поколенията; времевият лимит остава основната граница на run-а.
  vrp_log_progress:
    Показва progress съобщенията от VRP-Rust worker-а.

OR-Tools:
  first_solution_strategy:
    Начална стратегия: PARALLEL_CHEAPEST_INSERTION, SAVINGS, PATH_CHEAPEST_ARC и др.
  local_search_metaheuristic:
    GUIDED_LOCAL_SEARCH, SIMULATED_ANNEALING, TABU_SEARCH.
  lns_time_limit_seconds:
    Микро лимит за LNS.
  lns_num_nodes / lns_num_arcs:
    Размер на LNS neighbourhood.
  use_full_propagation:
    По-стриктно propagation.
  search_lambda_coefficient:
    GLS lambda.
  log_search:
    OR-Tools search лог.
                """,
                49,
            ),
            (
                "9. Routing матрица, OSRM и Valhalla",
                """
Solver-ът работи с матрица, не с карта директно.
Матрицата съдържа разстояние и време между всички депа и клиенти.

OSRM:
  Бърз routing engine.
  Препоръчителен за големи матрици.

osrm.base_url:
  Например http://localhost:5000.
  Най-често OSRM е локален service на същия компютър, който решава маршрута.

osrm.profile:
  driving, walking, cycling.

osrm.chunk_size:
  Колко локации се пращат в една заявка.
  По-голямо = по-малко заявки, но повече риск от timeout.

osrm.timeout_seconds:
  Таймаут за OSRM.

osrm.fallback_to_public:
  Може да пробва публичен OSRM, ако локалният не работи.
  За продукция е по-добре локален OSRM.

Valhalla:
  Алтернатива с time-dependent routing.
  routing.engine трябва да е valhalla.
  routing.departure_time задава час на тръгване.

Кеш:
  Ако кешът е включен, внимавай при промени на депа/клиенти/OSRM.
                """,
                31,
            ),
            (
                "10. Изходни файлове",
                """
HTML карти:
  enable_interactive_map:
    Генерира общата и индивидуалните карти заедно; няма отделен switch за тях.
  map_output_file:
    Файл за общата карта.
  routes_output_dir:
    Папка за отделни route HTML файлове.
  route_maps_upload_mode:
    disabled = не качва, legacy = старото поведение, effect_upload = качва HTML маршрутите към upload endpoint.
  route_maps_upload_url:
    URL за качване, напр. https://example.com/upload-files.php
  route_maps_upload_token:
    Използвай конфигурирания token за upload endpoint-а; не го публикувай в примери/логове.
  route_maps_upload_file_field:
    Обикновено files[], за да стигне до PHP като $_FILES['files'].
  route_maps_upload_bus_id_field:
    Обикновено pData2[], за да стигне до PHP като $_POST['pData2'] масив. Редът съвпада с files[].
  map_provider:
    osm или google.
  google_maps_api_key:
    Нужен за Google Maps режим.

Excel:
  enable_excel_output:
    Генерира един cvrp_report_<date>.xlsx с отделни sheets.
  excel_output_dir:
    Папка за Excel.
  routes_excel_file:
    Legacy име за съвместимост; активният workflow използва общия workbook.
  warehouse_excel_file:
    Legacy име за съвместимост; необслужените са отделен sheet.
  efficiency_excel_file:
    Legacy име за съвместимост; обобщението е в общия workbook.

CSV:
  enable_csv_output:
    Генерира CSV.
  csv_output_file:
    Път до CSV.

Графики:
  enable_charts:
    Генерира PNG графики.
  charts_output_dir:
    Папка за графики.

Важно за output:
  Програмата създава липсващите подпапки, когато основният drive/share е достъпен.
  Тя трябва да има права за писане.
  Ако подадеш H:/..., този drive/share трябва да е достъпен от компютъра, който стартира програмата.
                """,
                34,
            ),
            (
                "11. setData",
                """
setData изпраща готовите маршрути обратно към Bizant.

enable_set_data_upload:
  True:
    След успешно решение изпраща маршрути.
  False:
    Не изпраща нищо.

set_data_url:
  Endpoint за Bizant.

set_data_http_method:
  GET или POST.

set_data_command:
  Обикновено setData.

set_data_done_flag:
  DoneFlag за setData.

set_data_id_skld / set_data_vratza_id_skld:
  Към кой склад се връща маршрутът според депото.

set_data_depot_id_skld_map:
  Ръчно мапване за повече депа.

IdGrafik/Bukva templates:
  Управляват как се маркират маршрутите към Bizant.

enable_unserved_set_data_upload:
  Дали да се изпращат необслужени клиенти.

set_data_unserved_done_flag:
  DoneFlag само за необслужени клиенти. Ако е празно, използва set_data_done_flag.

enable_make_group:
  След успешен setData може да изпрати makeGroup.

За тест:
  Винаги подавай:
  "set_data": { "enable_set_data_upload": false }
                """,
                36,
            ),
            (
                "12. Автоматично стартиране",
                """
Автоматичното стартиране е в Разширени -> Автоматично стартиране.

За какво служи:
  Стартира програмата по график чрез Windows Task Scheduler.

Кога е полезно:
  - Всеки ден сутрин да се пуска маршрут.
  - Да се пуска в точно зададен час.
  - Да се използва без оператор да натиска бутон.

Как работи:
  GUI създава Windows Scheduled Task.
  Task-ът стартира EXE или Python скрипта.
  Използва текущите записани настройки от config.py.

Важно:
  Ако искаш различни настройки за всеки run, API е по-подходящо от scheduler.
  Scheduler е добър за постоянен еднотипен график.

Преди да създадеш задача:
  1. Натисни Запази.
  2. Увери се, че input_source е правилен.
  3. Увери се, че output папките са валидни.
  4. Увери се, че setData е включен/изключен според нуждата.

Ако задачата не тръгва:
  - Провери Windows Task Scheduler.
  - Провери дали пътят до EXE/Python е валиден.
  - Провери правата на потребителя.
  - Провери лог файла.
                """,
                30,
            ),
            (
                "13. Как да провериш качеството на резултата",
                """
След всяко реално решение гледай резултата в няколко нива.

1. Обобщение:
  - Общи километри.
  - Общо време.
  - Брой използвани бусове.
  - Брой обслужени клиенти.
  - Брой необслужени клиенти.

2. Excel routes:
  - Капацитет използване (%).
  - Дали има твърде празни бусове.
  - Дали има твърде дълги маршрути.
  - Дали имената на бусовете са ясни.

3. HTML карта:
  - Има ли видима плетеница.
  - Има ли бус, който тръгва от грешно депо.
  - Има ли център бус извън центъра без причина.
  - Има ли Враца бус в София или обратно, ако не е желано.

4. Route HTML файлове:
  - Файлът трябва да е с име на превозното средство и дата.
  - Всеки route трябва да е удобен за шофьора.
  - Ако маршрутът е дълъг, гледай дали Google Maps частите са логични.

5. Solver сравнение:
  - PyVRP, OR-Tools, VROOM и VRP-Rust трябва да се сравняват върху едни и същи клиенти и ограничения.
  - Сравнявай километри, време, брой бусове и необслужени клиенти.
  - Не сравнявай само score, ако двата solver-а имат различни вътрешни скали.
                """,
                42,
            ),
            (
                "14. Безопасна работа със setData и API",
                """
setData е реално действие към външна система.
Не го включвай при тестове, ако не искаш да запишеш маршрути.

Тестов режим:
  "set_data": { "enable_set_data_upload": false }

Реален режим:
  1. Провери set_data_url.
  2. Провери set_data_http_method.
  3. Провери DoneFlag.
  4. Провери IdSkld за всяко депо.
  5. Провери IdPlasDoc във входните клиенти.
  6. Пусни първо без upload, провери отчетите, после включи upload.

API key:
  Ако API сървърът е достъпен от други компютри, задай api_key.
  Подай го с:
    X-CVRP-API-Key: <key>
  или:
    Authorization: Bearer <key>

Едновременно стартиране:
  /run, /run_saturday и Web пазят само един активен основен run.
  Синхронният /solve не участва в този lock и не трябва да се ползва като паралелна run queue.
  Ако върне 409, вече има текуща оптимизация.

Callback:
  Използвай callback_url, ако не искаш заявката да чака края.
  След края програмата изпраща success/error към този URL.
                """,
                39,
            ),
            (
                "15. API пример за пълен run",
                """
POST /solve с клиенти, настройки, депа, бусове, output и JSON резултат:

{
  "settings": {
    "solver_type": "pyvrp_experimental",
    "objective_metric": "distance",
    "time_limit_seconds": 180,
    "enable_multiple_trips": true,
    "depots": {
      "main": [42.6957, 23.2316],
      "vratza": [43.2210, 23.5344]
    },
    "vehicles": [
      { "vehicle_type": "internal_bus", "count": 7, "capacity": 385, "max_time_hours": 8, "max_customers_per_day": 45, "start_depot_name": "main", "reload_depot_name": "main", "reload_time_minutes": 30, "end_location": [42.7000, 23.4000] },
      { "vehicle_type": "vratza_bus", "count": 3, "capacity": 385, "start_depot_name": "vratza", "reload_depot_name": "vratza", "end_depot_name": "vratza" }
    ],
    "center_zone": {
      "mode": "circle",
      "center": [42.6977, 23.3219],
      "radius_km": 2.0,
      "internal_bus_penalty": 40000,
      "external_bus_penalty": 40000,
      "vratza_bus_penalty": 40000
    },
    "center_zones": [
      { "name": "Видин", "mode": "circle", "center": [43.99,22.87], "radius_km": 8, "priority_vehicle_types": ["special_bus"], "restricted_vehicle_types": ["internal_bus"], "discount_priority_vehicle": 0.8, "vehicle_penalties": {"internal_bus": 40000}, "enabled": true, "show_on_map": true }
    ],
    "traffic_zones": [
      { "name": "Center traffic", "center": [42.6977, 23.3219], "radius_km": 3.0, "multiplier": 1.3, "enabled": true, "show_on_map": false }
    ],
    "osrm": {
      "base_url": "http://localhost:5000",
      "chunk_size": 80,
      "timeout_seconds": 45
    },
    "output": {
      "enable_excel_output": true,
      "excel_output_dir": "H:/Out",
      "enable_csv_output": true,
      "csv_output_file": "H:/Out/routes.csv"
    },
    "set_data": {
      "enable_set_data_upload": false
    }
  },
  "customers": [
    { "IdCust": "1", "CustName": "Клиент", "GPS": "42.6977,23.3219", "Volume": 10, "WorkTime": "08:00-13:00\\n16:00-18:00", "ServiceTimeMinutes": 6, "Mandatory": true, "DeliveryComment": "Обади се 10 мин преди доставка" }
  ]
}
                """,
                45,
            ),
            (
                "16. Работни прозорци, обслужване и втори курсове",
                """
Няколко работни прозореца:
  WorkTime може да бъде "08:00-13:00\\n16:00-18:00".
  В JSON новият ред трябва да е escape \\n, а не суров control character.
  Всички нормализирани прозорци се подават към solver адаптера.

Обслужване при клиент:
  JSON полето се избира от input.json_service_time_field.
  Валидна клиентска стойност заменя времето от буса само за този клиент.
  При липса/празна стойност се използва service_time_minutes на буса.
  Това важи за пълния CVRP вход. /tsp използва една обща service_time_minutes стойност за заявката.

Задължителен клиент:
  JSON полето се избира от input.json_mandatory_field; default е Mandatory.
  Excel колоната се избира от input.mandatory_column; default е Задължителен.
  true/1/да забранява пропускането дори когато allow_customer_skipping е включено.
  Ако такъв клиент е физически невъзможен, run-ът се отхвърля вместо да го прати тихо към склада.

Втори курсове:
  cvrp.enable_multiple_trips включва динамични допълнителни курсове.
  Капацитетът е за курс; време, километри и max_customers_per_day са общи за деня.
  reload_location и reload_time_minutes описват връщането между курсовете.
  Броят курсове се определя от оставащите дневни лимити.
  VROOM 1.15 изисква вторите курсове да са изключени.
  VRP-Rust отказва комбинацията multiple trips + max_customers_per_day.
  PyVRP използва точния OR-Tools backend при активен дневен клиентски лимит,
  както и при time objective с реален max_distance_km лимит.

Изход:
  Един физически бус получава една индивидуална карта с филтър по курсове.
  Всеки курс има отделен output номер и собствен списък клиенти.
  Основният Excel добавя само колоната Курс; техническият CSV пази и ключове.
                """,
                31,
            ),
            (
                "17. Web интерфейс и записване",
                """
Web GUI:
  Отваря се от api.web_gui_endpoint, например /hell.
  Потребителите се добавят/изтриват от Desktop Settings и се пазят в
  data/web_gui_auth.json. Промяната им влиза веднага.

Четири основни действия:
  Стартирай с текущите настройки:
    Пуска изолиран Web run и НЕ записва config.py.
  Запази глобални настройки:
    Записва позволените solver/output/setData полета в config.py без бусове.
  Запази бусовете:
    Записва само VehicleConfig редовете и не променя другите секции.
  Отхвърли промените и зареди глобалните:
    Връща Web формата към последно записаните стойности от config.py.

Важно:
  Временният Web run използва последно записаните input, routing, депа, зони и бусове.
  Незапазени редакции по бусове не участват — първо използвай отделното им записване.
  Legacy run-defaults поле/endpoint съществува, но текущата страница не го прилага.

Web GUI показва status/log tail и може да спре активния child process.
За достъп извън доверена LAN използвай HTTPS reverse proxy.
                """,
                28,
            ),
            (
                "18. Съботен run и видимост на зоните",
                """
POST/GET /run_saturday:
  Автоматично използва съботата от текущата седмица.
  Нормалното номериране използва output.saturday_excel_bus_number_prefix и *_digits.
  При включено center_bus_numbering_enabled CENTER_BUS запазва отделната серия
  от center_bus_numbering_start_id; тя има предимство пред съботния prefix.
  Добавя _събота след YYYY-MM-DD в картите и Excel файла.
  CSV и chart имената запазват собствените си правила и не получават задължително suffix.
  Настройките са request-local; нормалният prefix в config.py не се заменя.

Зони:
  enabled управлява дали правилото на допълнителна зона е активно.
  Основната legacy center зона се изключва чрез изключване и на priority, и на restrictions.
  show_on_map / Показвай на картата управлява само визуализацията.
  Скритата зона продължава да дава отстъпка/penalty или traffic multiplier.

За нормален run използвай /run. За клиенти директно в body използвай /solve.
                """,
                22,
            ),
            (
                "19. Диагностика",
                """
API не отговаря:
  - Провери дали сървърът е стартиран.
  - Провери /health.
  - Провери host и активния api.api_port; виж точния URL в /health.
  - Провери firewall, VPN или Cloudflare tunnel.

Връща 401:
  - Липсва или е грешен API key.

Връща 409:
  - Вече има активен run.
  - Провери /health.

Няма клиенти:
  - Провери JSON структурата.
  - Провери customers/data/records ключа.
  - Провери GPS field mapping.
  - Провери Sklad, DoneFlag и Date.

Лоши маршрути:
  - Провери депата.
  - Провери дали бусът тръгва от правилното депо.
  - Провери OSRM матрицата.
  - Провери center_zone и traffic_zones.
  - Увеличи time_limit_seconds.
  - Пробвай различен pyvrp_seed.

Липсва solver worker:
  - Провери pyvrp-next, vroom и vrp-rust директориите до EXE-то.
  - Стартирай съответния --self-check.
  - Разпространявай цялата dist папка, не само Bizant.exe.

Недостатъчно памет/pagefile:
  - Намали cvrp.num_workers до конкретен безопасен брой.
  - Автоматичният -1 използва CPU ядрата минус едно.

Кандидатът е отхвърлен от hard-constraint audit:
  - Виж точната причина в run log-а.
  - Провери работни прозорци, дневно време, край и reload настройки.

Липсва output:
  - Провери output.enable_*.
  - Провери дали папката съществува.
  - Провери права за писане.

setData не трябва да се пуска:
  - Задай set_data.enable_set_data_upload=false.
                """,
                36,
            ),
        ]

    def _add_guide_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 📘 Ръководство ")
        f = self._make_scrollable_frame(tab)

        intro, row = self._add_group(
            f,
            "Пълно ръководство",
            "Подробно описание на програмата, настройките, API режимите, output-а, setData и диагностиката.",
        )
        row = self._add_readonly_text(
            intro,
            row,
            "Накратко:",
            """
Това ръководство е вградено в GUI-то, за да може програмата да се настройва, пуска и поддържа без отделен документ.
То е написано като практически наръчник: първо работни сценарии, после настройки, API, setData, output и диагностика.
Всяко поле по-долу има собствен scrollbar и може да се скролва вътрешно с мишката.
            """,
            height=6,
        )

        guide, row = self._add_group(
            f,
            "Глави",
            "Чети секциите според задачата: локален run, API run, входни данни, бусове, solver, output, setData или диагностика.",
        )
        for title, body, height in self._program_guide_blocks():
            row = self._add_readonly_text(guide, row, title, body, height=height)

    def _add_advanced_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" ⋯ Разширени ")

        wrapper = ttk.Frame(tab, padding=(10, 10))
        wrapper.pack(fill="both", expand=True)
        ttk.Label(
            wrapper,
            text="API, автоматично стартиране и ръководство са прибрани тук, за да не пречат на основните настройки.",
            style="Hint.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=(0, 8))

        inner = ttk.Notebook(wrapper)
        inner.pack(fill="both", expand=True)
        self._add_api_tab(inner)
        self._add_set_data_tab(inner)
        self._add_scheduler_tab(inner)
        self._add_guide_tab(inner)

    # ── Tab: Входни данни ────────────────────────────────────

    def _add_input_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 📥 Входни данни ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        inp = self.cfg.input
        r = 0
        source, gr = self._add_grid_group(
            f,
            r,
            "Източник",
            "Избери откъде идват клиентите. Останалите секции само описват как да се прочетат данните.",
        ); r += 1
        self._add_field(source, gr, "input.input_source", "Активен източник:", inp.input_source,
                         "combo", ["http_json", "excel"],
                         tooltip="http_json = зарежда от Bizant/API. excel = зарежда от файл."); gr += 1
        self._add_field(source, gr, "input.excel_file_path", "Excel файл:", inp.excel_file_path,
                        tooltip="Използва се само когато активният източник е excel."); gr += 1
        self._add_field(source, gr, "input.enable_customer_document_grouping", "Групирай документи:", getattr(inp, "enable_customer_document_grouping", True), "bool",
                        tooltip="Ако един и същ клиент/GPS има няколко документа, solver-ът го посещава веднъж със сборен обем. Изключи, ако искаш всеки документ да е отделен стоп."); gr += 1

        http, gr = self._add_grid_group(
            f,
            r,
            "HTTP JSON",
            "Настройки за автоматично зареждане от външен URL. Това е основният режим при API trigger /run.",
        ); r += 1
        self._add_field(http, gr, "input.json_url", "URL:", inp.json_url); gr += 1
        self._add_field(http, gr, "input.json_http_method", "Метод:", getattr(inp, "json_http_method", "GET"),
                         "combo", ["GET", "POST"]); gr += 1
        self._add_field(http, gr, "input.json_command", "cmd:", getattr(inp, "json_command", "getData")); gr += 1
        self._add_field(http, gr, "input.json_date_field", "Поле за дата:", getattr(inp, "json_date_field", "date")); gr += 1
        self._add_field(http, gr, "input.json_sklad", "Sklad:", getattr(inp, "json_sklad", "106")); gr += 1
        self._add_field(http, gr, "input.json_done_flag", "DoneFlag:", getattr(inp, "json_done_flag", "1973")); gr += 1
        self._add_field(http, gr, "input.json_extra_query", "Допълнителни параметри:", getattr(inp, "json_extra_query", ""),
                         tooltip="Формат: key=value&key2=value2. По желание."); gr += 1
        self._add_field(http, gr, "input.json_override_date", "Конкретна дата:", inp.json_override_date,
                         tooltip="Формат DD/MM/YYYY. Празно = автоматично следващ работен ден."); gr += 1
        self._add_field(http, gr, "input.json_timeout_seconds", "Таймаут (сек):", inp.json_timeout_seconds); gr += 1

        json_fields, gr = self._add_grid_group(
            f,
            r,
            "JSON полета",
            "Имената на полетата в отговора. Ако външният JSON се смени, обикновено се пипа само тази секция.",
        ); r += 1
        self._add_field(json_fields, gr, "input.json_gps_field", "GPS:", inp.json_gps_field); gr += 1
        self._add_field(json_fields, gr, "input.json_client_id_field", "Клиентски номер:", inp.json_client_id_field); gr += 1
        self._add_field(json_fields, gr, "input.json_client_name_field", "Име на клиент:", inp.json_client_name_field); gr += 1
        self._add_field(json_fields, gr, "input.json_volume_field", "Обем:", inp.json_volume_field); gr += 1
        self._add_field(json_fields, gr, "input.json_document_field", "Документ:", inp.json_document_field); gr += 1
        self._add_field(json_fields, gr, "input.json_plas_doc_field", "IdPlasDoc:", getattr(inp, "json_plas_doc_field", "IdPlasDoc")); gr += 1
        self._add_field(json_fields, gr, "input.json_id_skld_field", "IdSkld:", getattr(inp, "json_id_skld_field", "IdSkld")); gr += 1
        self._add_field(json_fields, gr, "input.json_time_window_field", "Работно време:", getattr(inp, "json_time_window_field", "WorkTime"),
                        tooltip="JSON поле за GET/POST отговор във формат 08:00 - 16:00. Празно/липсващо = работи постоянно."); gr += 1
        self._add_field(json_fields, gr, "input.json_delivery_comment_field", "Коментар доставка:", getattr(inp, "json_delivery_comment_field", "DeliveryComment"),
                        tooltip="JSON поле с коментар/инструкция към шофьора. Поддържат се и DeliveryNote, Comment, Note като fallback."); gr += 1
        self._add_field(json_fields, gr, "input.json_service_time_field", "Обслужване (минути):", getattr(inp, "json_service_time_field", "ServiceTimeMinutes"),
                        tooltip="Optional JSON поле за време при клиента. Ако липсва/е празно, се използва времето от избрания бус."); gr += 1
        self._add_field(json_fields, gr, "input.json_mandatory_field", "Задължителен клиент:", getattr(inp, "json_mandatory_field", "Mandatory"),
                        tooltip="JSON поле за абсолютно задължително посещение. Приема true/false, 1/0 или да/не. Задължителен клиент не може да бъде пропуснат."); gr += 1

        excel_fields, gr = self._add_grid_group(
            f,
            r,
            "Excel колони",
            "Имената на колоните при ръчен Excel вход.",
        ); r += 1
        self._add_field(excel_fields, gr, "input.gps_column", "GPS:", inp.gps_column); gr += 1
        self._add_field(excel_fields, gr, "input.client_id_column", "ID:", inp.client_id_column); gr += 1
        self._add_field(excel_fields, gr, "input.client_name_column", "Име:", inp.client_name_column); gr += 1
        self._add_field(excel_fields, gr, "input.volume_column", "Обем:", inp.volume_column); gr += 1
        self._add_field(excel_fields, gr, "input.document_column", "Документ:", inp.document_column); gr += 1
        self._add_field(excel_fields, gr, "input.time_window_column", "Работно време:", getattr(inp, "time_window_column", "Работно време"),
                        tooltip="Excel колона във формат 08:00 - 16:00. Празно/липсващо = работи постоянно."); gr += 1
        self._add_field(excel_fields, gr, "input.delivery_comment_column", "Коментар доставка:", getattr(inp, "delivery_comment_column", "Коментар доставка"),
                        tooltip="Excel колона с коментар/инструкция към шофьора."); gr += 1
        self._add_field(excel_fields, gr, "input.mandatory_column", "Задължителен клиент:", getattr(inp, "mandatory_column", "Задължителен"),
                        tooltip="Optional Excel колона. Приема true/false, 1/0 или да/не."); gr += 1

    # ── Tab: Превозни средства ───────────────────────────────

    def _add_vehicles_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 🚐 Превозни средства ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)

        if not self.cfg.vehicles:
            ttk.Label(f, text="Няма конфигурирани превозни средства").grid(row=0, column=0)
            return

        r = 0
        depot_options = list(self._named_depots().keys())
        self.vehicle_tab_frame = f
        self.vehicle_depot_options = depot_options

        summary_box, gr = self._add_grid_group(
            f,
            r,
            "Бусове",
            "Табличен преглед на активните редове. За промяна избери реда по-долу във формите; за изтриване маркирай избрания ред и натисни Запази.",
        )
        self._add_vehicle_summary_table(summary_box, gr)
        r += 1

        add_box, gr = self._add_grid_group(
            f,
            r,
            "Добавяне",
            "Избери тип и добави нов ред. Промените влизат в config.py при Запази.",
        )
        self._add_field(
            add_box,
            gr,
            "vehicle_new.type",
            "Тип:",
            "internal_bus",
            "combo",
            self._vehicle_type_options(),
        )
        ttk.Button(add_box, text="Добави превозно средство", command=self._add_new_vehicle_row).grid(
            row=gr + 1, column=1, sticky="w", padx=6, pady=(4, 0)
        )
        r += 1

        for i, v in enumerate(self.cfg.vehicles):
            vtype = v.vehicle_type.value
            label_map = {
                "internal_bus": "🚐 Вътрешен бус",
                "center_bus": "🚐 Център бус",
                "external_bus": "🚐 Външен бус",
                "special_bus": "🚐 Специален бус",
                "vratza_bus": "🚐 Враца бус",
            }
            header = label_map.get(vtype, vtype)
            vehicle_box, gr = self._add_grid_group(
                f,
                r,
                header,
                "Име, брой, капацитет и ограничения за този тип бус.",
            )
            r += 1

            prefix = f"vehicle.{i}"
            self.widgets[f"{prefix}.vehicle_type"] = tk.StringVar(value=vtype)
            self.widgets[f"{prefix}.max_distance_km"] = tk.StringVar(
                value="" if v.max_distance_km is None else str(v.max_distance_km)
            )
            self._add_field(vehicle_box, gr, f"{prefix}.name", "Име на бус:", getattr(v, "name", ""),
                            tooltip="По желание. При Брой > 1 отчетът добавя номер: Име 1, Име 2..."); gr += 1
            self._add_field(
                vehicle_box,
                gr,
                f"{prefix}.config_id",
                "Стабилно ID:",
                getattr(v, "config_id", "") or f"{vtype}_{i + 1}",
                tooltip="Уникален технически ID за този ред. Използва се за един и същ физически бус при няколко курса.",
            ); gr += 1
            self._add_field(
                vehicle_box,
                gr,
                f"{prefix}.remove",
                "Премахни при запис:",
                False,
                "bool",
                tooltip="Ако е включено, този ред ще бъде изтрит от списъка при следващо Запази.",
            ); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.enabled", "Активен:", v.enabled, "bool"); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.count", "Брой:", v.count); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.fixed_cost", "Цена за използване:", getattr(v, "fixed_cost", 40000),
                            tooltip="Глоба за всеки използван бус. 40000 е приблизително като 40 км."); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.capacity", "Капацитет (ст.):", v.capacity); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.max_distance_km", "Макс. км:",
                            "" if v.max_distance_km is None else v.max_distance_km,
                            tooltip="Празно = без лимит. При повторни курсове лимитът е общ за целия ден."); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.max_time_hours", "Работно време за деня (ч.):", v.max_time_hours,
                            tooltip="Общото време включва движение, обслужване, чакане, връщане и презареждане."); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.service_time_minutes", "Обслужване (мин):", v.service_time_minutes); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.max_customers_per_day", "Макс. клиенти за деня:",
                             getattr(v, "max_customers_per_day", None) or getattr(v, "max_customers_per_route", None) or "",
                             tooltip="Общ лимит за всички курсове на физическия бус. Празно = без ограничение."); gr += 1
            self._add_depot_choice_field(vehicle_box, gr, f"{prefix}.start_depot_name", "Депо тръгване:",
                                         self._depot_name_for_coords(v.start_location),
                                         depot_options,
                                         tooltip="Избери депо по име. Ако добавиш ново депо, можеш да напишеш името му тук след запис/презареждане."); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.end_location", "Крайна точка GPS:",
                            self._format_optional_coords(getattr(v, "end_location", None)),
                            tooltip="По желание във формат lat, lon. Празно = маршрутът завършва в депото на тръгване."); gr += 1
            self._add_depot_choice_field(
                vehicle_box,
                gr,
                f"{prefix}.reload_depot_name",
                "Депо за презареждане:",
                self._depot_name_for_coords(getattr(v, "reload_location", None) or v.start_location),
                depot_options,
                tooltip="При нужда от нов товар бусът се връща тук. Обикновено е същото като депото на тръгване.",
            ); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.reload_time_minutes", "Презареждане (мин):",
                            getattr(v, "reload_time_minutes", 30),
                            tooltip="Времето се начислява само между два курса и влиза в работния ден."); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.start_time_minutes", "Старт (мин от 00:00):", v.start_time_minutes,
                             tooltip="480 = 08:00"); gr += 1

        self.vehicle_next_index = len(self.cfg.vehicles)
        self.vehicle_next_row = r
        self._sync_vehicle_tree_from_widgets()

    # ── Tab: Склад ───────────────────────────────────────────

    def _vehicle_type_options(self):
        return [
            vehicle_type.value
            for vehicle_type in config.VehicleType
            if vehicle_type not in (config.VehicleType.WAREHOUSE, config.VehicleType.DISABLED)
        ]

    def _vehicle_label(self, vehicle_type_value):
        labels = {
            "internal_bus": "Вътрешен бус",
            "center_bus": "Център бус",
            "external_bus": "Външен бус",
            "special_bus": "Специален бус",
            "vratza_bus": "Враца бус",
        }
        return labels.get(str(vehicle_type_value), str(vehicle_type_value))

    def _add_vehicle_summary_table(self, parent, row):
        parent.columnconfigure(0, weight=1)
        self.vehicle_tree = ttk.Treeview(
            parent,
            columns=("type", "name", "count", "capacity", "depot", "end", "status"),
            show="headings",
            height=7,
            selectmode="browse",
        )
        for column, title, width in (
            ("type", "Тип", 120),
            ("name", "Име", 130),
            ("count", "Брой", 60),
            ("capacity", "Капацитет", 80),
            ("depot", "Депо", 130),
            ("end", "Край", 150),
            ("status", "Статус", 95),
        ):
            self.vehicle_tree.heading(column, text=title)
            self.vehicle_tree.column(column, width=width, minwidth=55, stretch=True)
        self.vehicle_tree.grid(row=row, column=0, columnspan=3, sticky="we", padx=8, pady=6)

        actions = ttk.Frame(parent, style="Surface.TFrame")
        actions.grid(row=row + 1, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        ttk.Button(actions, text="Добави", command=self._add_new_vehicle_row).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Изтрий избрания", command=self._mark_selected_vehicle_for_remove).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Покажи депото", command=self._show_selected_vehicle_depot_on_map).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Обнови таблицата", command=self._sync_vehicle_tree_from_widgets).pack(side="left")
        return row + 2

    def _vehicle_widget_indices(self):
        import re

        return sorted({
            int(match.group(1))
            for key in self.widgets
            for match in [re.match(r"vehicle\.(\d+)\.vehicle_type$", key)]
            if match
        })

    def _sync_vehicle_tree_from_widgets(self):
        if self.vehicle_tree is None:
            return

        for item in self.vehicle_tree.get_children():
            self.vehicle_tree.delete(item)

        for index in self._vehicle_widget_indices():
            prefix = f"vehicle.{index}"
            vehicle_type = str(self._widget_value(self.widgets.get(f"{prefix}.vehicle_type"), "internal_bus"))
            name = str(self._widget_value(self.widgets.get(f"{prefix}.name"), "") or "")
            count = str(self._widget_value(self.widgets.get(f"{prefix}.count"), ""))
            capacity = str(self._widget_value(self.widgets.get(f"{prefix}.capacity"), ""))
            depot = str(self._widget_value(self.widgets.get(f"{prefix}.start_depot_name"), ""))
            end_location = str(self._widget_value(self.widgets.get(f"{prefix}.end_location"), "") or "").strip()
            enabled = self._parse_bool_value(self._widget_value(self.widgets.get(f"{prefix}.enabled"), True))
            remove = self._parse_bool_value(self._widget_value(self.widgets.get(f"{prefix}.remove"), False))
            status = "за изтриване" if remove else ("активен" if enabled else "изключен")
            self.vehicle_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    self._vehicle_label(vehicle_type),
                    name or "-",
                    count,
                    capacity,
                    depot or "Главно депо",
                    end_location or "депото",
                    status,
                ),
            )

    def _selected_vehicle_prefix(self):
        index = self._selected_tree_index(self.vehicle_tree)
        if index is None:
            messagebox.showinfo("Няма избран бус", "Избери ред от таблицата с бусовете.")
            return None
        return f"vehicle.{index}"

    def _mark_selected_vehicle_for_remove(self):
        prefix = self._selected_vehicle_prefix()
        if not prefix:
            return
        remove_widget = self.widgets.get(f"{prefix}.remove")
        if remove_widget is None:
            return
        remove_widget.set(True)
        self._sync_vehicle_tree_from_widgets()
        self.status_var.set("Бусът е маркиран за изтриване. Натисни Запази, за да се премахне от config.py.")

    def _show_selected_vehicle_depot_on_map(self):
        prefix = self._selected_vehicle_prefix()
        if not prefix:
            return
        values = self._collect_values()
        depot_name = str(values.get(f"{prefix}.start_depot_name", "") or "Главно депо").strip()
        depots = self._depot_lookup_for_values(values)
        coords = depots.get(depot_name)
        if not coords:
            messagebox.showinfo("Няма GPS", "Не намирам координати за избраното депо.")
            return
        self._open_point_editor([float(coords[0]), float(coords[1])], lambda selected: None, f"Депо: {depot_name}")

    def _new_vehicle_template(self, vehicle_type_value):
        for vehicle in reversed(self.cfg.vehicles or []):
            if vehicle.vehicle_type.value == vehicle_type_value:
                clone = copy.deepcopy(vehicle)
                clone.count = 1
                clone.enabled = True
                clone.name = ""
                clone.end_location = None
                clone.config_id = ""
                return clone

        try:
            vehicle_type = config.VehicleType(vehicle_type_value)
        except ValueError:
            vehicle_type = config.VehicleType.INTERNAL_BUS

        depot = self.cfg.locations.depot_location
        return config.VehicleConfig(
            vehicle_type=vehicle_type,
            capacity=320,
            count=1,
            fixed_cost=0,
            max_distance_km=None,
            max_time_hours=8,
            service_time_minutes=8,
            enabled=True,
            max_customers_per_route=None,
            config_id="",
            reload_location=depot,
            reload_time_minutes=30,
            max_customers_per_day=None,
            start_location=depot,
            end_location=None,
            start_time_minutes=480,
            tsp_depot_location=depot,
        )

    def _add_new_vehicle_row(self):
        if self.vehicle_tab_frame is None:
            return

        type_widget = self.widgets.get("vehicle_new.type")
        vehicle_type_value = type_widget.get() if type_widget else "internal_bus"
        vehicle = self._new_vehicle_template(vehicle_type_value)

        row = getattr(self, "vehicle_next_row", 0)
        index = getattr(self, "vehicle_next_index", 0)
        prefix = f"vehicle.{index}"

        vehicle_box, gr = self._add_grid_group(
            self.vehicle_tab_frame,
            row,
            f"Ново: {self._vehicle_label(vehicle.vehicle_type.value)}",
            "Попълни полетата и натисни Запази, за да влезе новият бус в config.py.",
        )

        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.vehicle_type",
            "Тип:",
            vehicle.vehicle_type.value,
            "combo",
            self._vehicle_type_options(),
        ); gr += 1
        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.name",
            "Име на бус:",
            getattr(vehicle, "name", ""),
            tooltip="По желание. При Брой > 1 отчетът добавя номер: Име 1, Име 2...",
        ); gr += 1
        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.config_id",
            "Стабилно ID:",
            getattr(vehicle, "config_id", "") or f"{vehicle.vehicle_type.value}_{index + 1}",
            tooltip="Уникален технически ID за физическите бусове от този ред.",
        ); gr += 1
        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.remove",
            "Премахни при запис:",
            False,
            "bool",
            tooltip="Ако добавянето е грешка, включи това и при Запази редът няма да влезе в config.py.",
        ); gr += 1
        self._add_field(vehicle_box, gr, f"{prefix}.enabled", "Активен:", vehicle.enabled, "bool"); gr += 1
        self._add_field(vehicle_box, gr, f"{prefix}.count", "Брой:", vehicle.count); gr += 1
        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.fixed_cost",
            "Цена за използване:",
            getattr(vehicle, "fixed_cost", 0),
            tooltip="Глоба за всеки използван бус. 0 = solver-ът може да използва буса свободно.",
        ); gr += 1
        self._add_field(vehicle_box, gr, f"{prefix}.capacity", "Капацитет (ст.):", vehicle.capacity); gr += 1
        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.max_distance_km",
            "Макс. км:",
            "" if vehicle.max_distance_km is None else vehicle.max_distance_km,
            tooltip="Празно = без лимит. При повторни курсове лимитът е общ за деня.",
        ); gr += 1
        self._add_field(vehicle_box, gr, f"{prefix}.max_time_hours", "Работно време за деня (ч.):", vehicle.max_time_hours,
                        tooltip="Включва движение, обслужване, чакане, връщане и презареждане."); gr += 1
        self._add_field(vehicle_box, gr, f"{prefix}.service_time_minutes", "Обслужване (мин):", vehicle.service_time_minutes); gr += 1
        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.max_customers_per_day",
            "Макс. клиенти за деня:",
            getattr(vehicle, "max_customers_per_day", None) or getattr(vehicle, "max_customers_per_route", None) or "",
            tooltip="Общ лимит за всички курсове. Празно = без ограничение.",
        ); gr += 1
        self._add_depot_choice_field(
            vehicle_box,
            gr,
            f"{prefix}.start_depot_name",
            "Депо тръгване:",
            self._depot_name_for_coords(vehicle.start_location),
            getattr(self, "vehicle_depot_options", list(self._named_depots().keys())),
            tooltip="Избери депото, от което тръгва този бус.",
        ); gr += 1
        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.end_location",
            "Крайна точка GPS:",
            self._format_optional_coords(getattr(vehicle, "end_location", None)),
            tooltip="По желание във формат lat, lon. Празно = маршрутът завършва в депото на тръгване.",
        ); gr += 1
        self._add_depot_choice_field(
            vehicle_box,
            gr,
            f"{prefix}.reload_depot_name",
            "Депо за презареждане:",
            self._depot_name_for_coords(getattr(vehicle, "reload_location", None) or vehicle.start_location),
            getattr(self, "vehicle_depot_options", list(self._named_depots().keys())),
            tooltip="Депото, в което бусът се връща за следващ товар.",
        ); gr += 1
        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.reload_time_minutes",
            "Презареждане (мин):",
            getattr(vehicle, "reload_time_minutes", 30),
            tooltip="Начислява се само между два курса.",
        ); gr += 1
        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.start_time_minutes",
            "Старт (мин от 00:00):",
            vehicle.start_time_minutes,
            tooltip="480 = 08:00",
        ); gr += 1

        self.vehicle_next_index = index + 1
        self.vehicle_next_row = row + 1
        self._sync_vehicle_tree_from_widgets()
        self.status_var.set("Добавен е нов ред за превозно средство. Натисни Запази, за да влезе в config.py.")

    def _add_warehouse_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 🏭 Предварителна оптимизация")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        wh = self.cfg.warehouse
        r = 0
        warehouse, gr = self._add_grid_group(
            f,
            r,
            "Склад и предварителен филтър",
            "Тук се решава кои заявки изобщо да стигнат до solver-а и кои да останат за склад.",
        )
        self._add_field(warehouse, gr, "warehouse.enable_warehouse", "Включен:", wh.enable_warehouse, "bool"); gr += 1
        self._add_field(warehouse, gr, "warehouse.sort_by_volume", "Сортирай по обем:", wh.sort_by_volume, "bool"); gr += 1
        self._add_field(warehouse, gr, "warehouse.sort_by_distance", "Сортирай по разстояние:", wh.sort_by_distance, "bool"); gr += 1
        self._add_field(warehouse, gr, "warehouse.check_max_bus_capacity", "Проверка капацитет:", wh.check_max_bus_capacity, "bool",
                        tooltip="Клиенти над капацитета на най-големия бус се оставят за склад."); gr += 1
        self._add_field(warehouse, gr, "warehouse.max_bus_customer_volume", "Макс. обем за бус:", wh.max_bus_customer_volume,
                        tooltip="Клиенти над този обем се пращат към склада."); gr += 1
        self._add_field(warehouse, gr, "warehouse.capacity_toleranse", "Толеранс капацитет:", wh.capacity_toleranse); gr += 1

    # ── Tab: Солвър ──────────────────────────────────────────

    def _add_solver_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" ⚙️ Солвър ")
        f = self._make_scrollable_frame(tab)
        c = self.cfg.cvrp

        basic, r = self._add_group(
            f,
            "Основни настройки",
            "Избор на решител и общ лимит за търсене. VROOM и експерименталният VRP-Rust са отделни open-source алтернативи за сравнение с PyVRP.",
        )
        solver_options = [
            self.SOLVER_LABELS.get(value, value)
            for value in config.CVRP_SOLVER_TYPES
        ]
        self._add_field(
            basic,
            r,
            "cvrp.solver_type",
            "Тип солвър:",
            self.SOLVER_LABELS.get(c.solver_type, c.solver_type),
            "combo",
            solver_options,
                         tooltip="pyvrp = стабилната линия 0.13.x (минимум 0.13.4); pyvrp_experimental = 0.14 sidecar; or_tools = OR-Tools; vroom = official VROOM 1.15 sidecar (без повторни курсове); vrp = VRP-Rust experimental sidecar."); r += 1
        self._add_field(basic, r, "cvrp.objective_metric", "Цел на оптимизацията:", getattr(c, "objective_metric", "distance"),
                         "combo", ["distance", "time"],
                         tooltip="distance = най-къси километри. time = най-кратко време по OSRM/Valhalla duration матрицата."); r += 1
        self._add_field(
            basic,
            r,
            "cvrp.time_objective_include_waiting",
            "Включи чакането в целта:",
            getattr(c, "time_objective_include_waiting", True),
            "bool",
            tooltip=(
                "Важи при цел time. Включено = минимизира целия работен ден: пътуване, "
                "обслужване, чакане и презареждане. Fitness е само времето в секунди; "
                "реалните метри остават за ограниченията и отчетите. "
                "Изключено = старото поведение без директна цена за чакането. "
                "VROOM спазва и отчита чакането, но native objective-ът му не може да го цени директно."
            ),
        ); r += 1
        self._add_field(
            basic,
            r,
            "cvrp.enable_multiple_trips",
            "Разреши повторни курсове:",
            getattr(c, "enable_multiple_trips", False),
            "bool",
            tooltip="Обща настройка за всички бусове. Изключено = максимум един курс; включено = връщане и презареждане, докато дневните лимити позволяват.",
        ); r += 1
        self._add_field(basic, r, "cvrp.time_limit_seconds", "Време за решение (сек):", c.time_limit_seconds); r += 1

        pyvrp_quality, r = self._add_group(
            f,
            "PyVRP качество",
            "Фини настройки само за PyVRP. По-високите стойности често помагат при две депа, но забавят търсенето.",
        )
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_seed", "Seed:", getattr(c, "pyvrp_seed", None),
                        tooltip="Празно поле = използва seed базата. В единичен режим това е точният seed."); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_num_neighbours", "Съседи:", getattr(c, "pyvrp_num_neighbours", 100),
                        tooltip="Практичен диапазон при две депа: 80-120."); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_weight_wait_time", "Тежест на чакането:", getattr(c, "pyvrp_weight_wait_time", 0.2),
                        tooltip="Участва в proximity оценката за neighbourhood-а. PyVRP default: 0.2."); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_symmetric_proximity", "Симетрична proximity:", getattr(c, "pyvrp_symmetric_proximity", True), "bool"); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_ils_no_improvement", "ILS без подобрение:", getattr(c, "pyvrp_ils_no_improvement", 300000)); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_ils_history_length", "ILS история:", getattr(c, "pyvrp_ils_history_length", 500)); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_exhaustive_on_best", "Exhaustive при best:", getattr(c, "pyvrp_exhaustive_on_best", True), "bool",
                        tooltip="Пуска по-скъпо пълно локално търсене при всяко ново най-добро решение."); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_use_extended_operators", "Разширени оператори:", getattr(c, "pyvrp_use_extended_operators", True), "bool"); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_min_perturbations", "Мин. perturbations:", getattr(c, "pyvrp_min_perturbations", 1)); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_max_perturbations", "Макс. perturbations:", getattr(c, "pyvrp_max_perturbations", 40)); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_display_progress", "PyVRP progress лог:", getattr(c, "pyvrp_display_progress", False), "bool"); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_display_interval_seconds", "Progress интервал (сек):", getattr(c, "pyvrp_display_interval_seconds", 5.0)); r += 1

        pyvrp_penalty, r = self._add_group(
            f,
            "PyVRP penalty manager",
            "Penalty manager-ът балансира търсенето между валидни и временно невалидни решения. Library defaults са препоръчителната начална точка.",
        )
        self._add_field(pyvrp_penalty, r, "cvrp.pyvrp_use_library_penalty_defaults", "Library defaults:", getattr(c, "pyvrp_use_library_penalty_defaults", True), "bool",
                        tooltip="Включено = всяка версия използва собствените си проверени defaults. Изключи за ръчен контрол на полетата отдолу."); r += 1
        self._add_field(pyvrp_penalty, r, "cvrp.pyvrp_penalty_solutions_between_updates", "Решения между updates:", getattr(c, "pyvrp_penalty_solutions_between_updates", 500)); r += 1
        self._add_field(pyvrp_penalty, r, "cvrp.pyvrp_penalty_increase", "Увеличение:", getattr(c, "pyvrp_penalty_increase", 1.5)); r += 1
        self._add_field(pyvrp_penalty, r, "cvrp.pyvrp_penalty_decrease", "Намаление:", getattr(c, "pyvrp_penalty_decrease", 0.9)); r += 1
        self._add_field(pyvrp_penalty, r, "cvrp.pyvrp_penalty_target_feasible", "Target feasible:", getattr(c, "pyvrp_penalty_target_feasible", 0.65)); r += 1
        self._add_field(pyvrp_penalty, r, "cvrp.pyvrp_penalty_feas_tolerance", "Feasible tolerance:", getattr(c, "pyvrp_penalty_feas_tolerance", 0.05)); r += 1
        self._add_field(pyvrp_penalty, r, "cvrp.pyvrp_penalty_min", "Минимална penalty:", getattr(c, "pyvrp_penalty_min", 0.1)); r += 1
        self._add_field(pyvrp_penalty, r, "cvrp.pyvrp_penalty_max", "Максимална penalty:", getattr(c, "pyvrp_penalty_max", 100000.0),
                        tooltip="Не задавай прекалено голяма стойност: PyVRP предупреждава за риск от integer overflow."); r += 1

        pyvrp_next, r = self._add_group(
            f,
            "PyVRP 0.14 experimental sidecar",
            "Стабилният PyVRP 0.13.x остава в приложението. Тези настройки се използват само при solver_type=pyvrp_experimental.",
        )
        self._add_field(
            pyvrp_next,
            r,
            "cvrp.pyvrp_next_worker_path",
            "Worker път:",
            getattr(c, "pyvrp_next_worker_path", ""),
            tooltip="Празно = търси стандартния companion worker до приложението. Може да е отделен Python/EXE worker за PyVRP 0.14.",
        ); r += 1
        self._add_field(
            pyvrp_next,
            r,
            "cvrp.pyvrp_next_worker_timeout_seconds",
            "Worker timeout (сек):",
            getattr(c, "pyvrp_next_worker_timeout_seconds", 0),
            tooltip="0 = лимитът на решителя плюс автоматичен резерв за стартиране и IPC.",
        ); r += 1
        self._add_field(
            pyvrp_next,
            r,
            "cvrp.pyvrp_next_fallback_to_stable",
            "Fallback към stable:",
            getattr(c, "pyvrp_next_fallback_to_stable", False),
            "bool",
            tooltip="Ако experimental worker-ът липсва или се провали, използва стабилния PyVRP 0.13. Изключено = ясна грешка без скрита подмяна.",
        ); r += 1

        vroom_group, r = self._add_group(
            f,
            "VROOM 1.15",
            "Официалният VROOM работи в изолиран companion worker и използва готовите OSRM/Valhalla матрици. Поддържа еднокурсни маршрути; при повторни курсове приложението спира с ясна грешка.",
        )
        self._add_field(
            vroom_group,
            r,
            "cvrp.vroom_worker_path",
            "Worker път:",
            getattr(c, "vroom_worker_path", ""),
            tooltip="Празно = .venv-vroom при source run или dist\\vroom\\CVRP_VROOM_Worker при EXE.",
        ); r += 1
        self._add_field(
            vroom_group,
            r,
            "cvrp.vroom_worker_timeout_seconds",
            "Worker timeout (сек):",
            getattr(c, "vroom_worker_timeout_seconds", 0),
            tooltip="0 = общият solver time limit плюс автоматичен резерв за startup и JSON обмен.",
        ); r += 1
        self._add_field(
            vroom_group,
            r,
            "cvrp.vroom_threads",
            "Вътрешни threads:",
            getattr(c, "vroom_threads", 0),
            tooltip="0 = автоматично използва логическите ядра без едно. VROOM не стартира външни паралелни workers.",
        ); r += 1
        self._add_field(
            vroom_group,
            r,
            "cvrp.vroom_exploration_level",
            "Exploration (0-5):",
            getattr(c, "vroom_exploration_level", 5),
            tooltip="5 = максимално качество според официалния VROOM параметър; по-ниско е по-бързо.",
        ); r += 1

        vrp_group, r = self._add_group(
            f,
            "VRP-Rust experimental",
            "Експериментален open-source sidecar за независимо сравнение с PyVRP. Използва готовите OSRM/Valhalla матрици и поддържа повторни курсове. В distance режим зоните влияят на избора; pure time режимът минимизира само продължителността.",
        )
        self._add_field(
            vrp_group,
            r,
            "cvrp.vrp_worker_path",
            "Worker път:",
            getattr(c, "vrp_worker_path", ""),
            tooltip="Празно = автоматично откриване на .venv-vrp-rust при source run или companion worker до приложението.",
        ); r += 1
        self._add_field(
            vrp_group,
            r,
            "cvrp.vrp_worker_timeout_seconds",
            "Worker timeout (сек):",
            getattr(c, "vrp_worker_timeout_seconds", 0),
            tooltip="0 = общият solver time limit плюс автоматичен резерв за startup и JSON обмен.",
        ); r += 1
        self._add_field(
            vrp_group,
            r,
            "cvrp.vrp_threads",
            "Вътрешни threads:",
            getattr(c, "vrp_threads", 0),
            tooltip="0 = автоматичен избор според наличните логически ядра; положителна стойност задава точен брой нишки.",
        ); r += 1
        try:
            vrp_max_generations_value = int(getattr(c, "vrp_max_generations", 1_000_000) or 1_000_000)
        except (TypeError, ValueError):
            vrp_max_generations_value = 1_000_000
        # Migrate the short-lived out-of-range default transparently so an old
        # config can be opened and saved without forcing a manual correction.
        vrp_max_generations_value = min(max(vrp_max_generations_value, 1), 1_000_000_000_000)
        self._add_field(
            vrp_group,
            r,
            "cvrp.vrp_max_generations",
            "Макс. поколения:",
            vrp_max_generations_value,
            tooltip="Горна граница на търсенето. Общият времеви лимит може да прекрати run-а по-рано.",
        ); r += 1
        self._add_field(
            vrp_group,
            r,
            "cvrp.vrp_log_progress",
            "VRP-Rust progress лог:",
            getattr(c, "vrp_log_progress", True),
            "bool",
            tooltip="Показва progress съобщенията от отделния VRP-Rust worker.",
        ); r += 1

        ortools, r = self._add_group(
            f,
            "OR-Tools качество",
            "Фини настройки само за OR-Tools. Държим ги отделно от PyVRP, за да не се бъркат двата решителя.",
        )
        self._add_field(ortools, r, "cvrp.first_solution_strategy", "First solution:", c.first_solution_strategy,
                         "combo", ["AUTOMATIC", "PATH_CHEAPEST_ARC", "SAVINGS", "SWEEP", "CHRISTOFIDES",
                                   "PARALLEL_CHEAPEST_INSERTION"]); r += 1
        self._add_field(ortools, r, "cvrp.local_search_metaheuristic", "Метаевристика:", c.local_search_metaheuristic,
                         "combo", ["AUTOMATIC", "GUIDED_LOCAL_SEARCH", "SIMULATED_ANNEALING", "TABU_SEARCH"]); r += 1
        self._add_field(ortools, r, "cvrp.lns_time_limit_seconds", "LNS лимит:", getattr(c, "lns_time_limit_seconds", 15),
                        tooltip="Микро лимит за OR-Tools LNS стъпките, в секунди."); r += 1
        self._add_field(ortools, r, "cvrp.lns_num_nodes", "LNS близки възли:", getattr(c, "lns_num_nodes", 120)); r += 1
        self._add_field(ortools, r, "cvrp.lns_num_arcs", "LNS скъпи ребра:", getattr(c, "lns_num_arcs", 110)); r += 1
        self._add_field(ortools, r, "cvrp.use_full_propagation", "Full propagation:", getattr(c, "use_full_propagation", True), "bool"); r += 1
        self._add_field(ortools, r, "cvrp.search_lambda_coefficient", "GLS lambda:", getattr(c, "search_lambda_coefficient", 0.8),
                        tooltip="Guided Local Search lambda coefficient."); r += 1
        self._add_field(ortools, r, "cvrp.log_search", "OR-Tools search лог:", getattr(c, "log_search", True), "bool"); r += 1
        self._add_field(ortools, r, "cvrp.enable_start_time_tracking", "Проследяване старт. време:", c.enable_start_time_tracking, "bool"); r += 1
        self._add_field(ortools, r, "cvrp.global_start_time_minutes", "Глобално старт. време (мин):", c.global_start_time_minutes,
                         tooltip="480 = 08:00"); r += 1

        time_windows, r = self._add_group(
            f,
            "Работно време на клиенти",
            "Когато е включено, solver-ите спазват входното поле 'Работно време'. Празна стойност означава постоянно отворен обект.",
        )
        self.solver_fine_anchor = time_windows
        self._add_field(time_windows, r, "cvrp.enable_customer_time_windows", "Спазвай работно време:", getattr(c, "enable_customer_time_windows", False), "bool",
                        tooltip="Изключено = маршрутите се решават както досега."); r += 1

        routing, r = self._add_group(
            f,
            "Routing матрица",
            "Настройки за пътната матрица, която се подава към solver-а.",
        )
        routing_cfg = self.cfg.routing
        engine_value = getattr(getattr(routing_cfg, "engine", "osrm"), "value", getattr(routing_cfg, "engine", "osrm"))
        self._add_field(routing, r, "routing.engine", "Routing engine:", engine_value,
                         "combo", ["osrm", "valhalla"],
                         tooltip="OSRM = бърза матрица. Valhalla = по-гъвкав engine с time-dependent routing."); r += 1
        self._add_field(routing, r, "routing.enable_time_dependent", "Valhalla time-dependent:", getattr(routing_cfg, "enable_time_dependent", True), "bool",
                        tooltip="Използва час на тръгване при Valhalla."); r += 1
        self._add_field(routing, r, "routing.departure_time", "Час на тръгване:", getattr(routing_cfg, "departure_time", "08:00"),
                        tooltip="Формат HH:MM, например 08:00."); r += 1
        self._add_field(routing, r, "routing.enable_curbside_approach", "Спазвай страна на улицата:", getattr(routing_cfg, "enable_curbside_approach", False), "bool",
                        tooltip="При OSRM добавя approaches=curb. При Valhalla добавя preferred_side към локациите."); r += 1
        self._add_field(routing, r, "routing.valhalla_preferred_side", "Valhalla preferred side:", getattr(routing_cfg, "valhalla_preferred_side", "same"),
                         "combo", ["same", "either", "opposite"],
                         tooltip="same = клиентът да е от страната на движение. За България това обикновено значи отдясно."); r += 1
        osrm_cfg = self.cfg.osrm
        self._add_field(routing, r, "osrm.base_url", "OSRM URL:", getattr(osrm_cfg, "base_url", "http://localhost:5000"),
                        tooltip="Адрес на OSRM сървъра."); r += 1
        self._add_field(routing, r, "osrm.profile", "OSRM profile:", getattr(osrm_cfg, "profile", "driving"),
                         "combo", ["driving", "walking", "cycling"],
                         tooltip="Профил за OSRM."); r += 1
        self._add_field(routing, r, "osrm.chunk_size", "OSRM chunk size:", getattr(osrm_cfg, "chunk_size", 80),
                        tooltip="Колко локации да се пращат в една OSRM заявка. По-голямо = по-малко заявки, но повече RAM/риск от timeout."); r += 1
        self._add_field(routing, r, "osrm.timeout_seconds", "OSRM timeout (сек):", getattr(osrm_cfg, "timeout_seconds", 45)); r += 1
        self._add_field(routing, r, "osrm.retry_attempts", "OSRM опити:", getattr(osrm_cfg, "retry_attempts", 3)); r += 1
        self._add_field(routing, r, "osrm.average_speed_kmh", "Fallback скорост (км/ч):", getattr(osrm_cfg, "average_speed_kmh", 40.0),
                        tooltip="Използва се само ако OSRM не върне време."); r += 1
        self._add_field(routing, r, "osrm.fallback_to_public", "Fallback публичен OSRM:", getattr(osrm_cfg, "fallback_to_public", True), "bool",
                        tooltip="Ако локалният OSRM не отговаря, опитва публичния router.project-osrm.org."); r += 1
        self._add_field(routing, r, "osrm.max_locations_for_osrm", "Макс. OSRM локации:", getattr(osrm_cfg, "max_locations_for_osrm", 50),
                        tooltip="Над този брой програмата може да мине към приблизителна матрица според OSRM логиката."); r += 1
        self._add_field(routing, r, "osrm.enable_smart_chunking", "Smart chunking:", getattr(osrm_cfg, "enable_smart_chunking", True), "bool"); r += 1
        valhalla_cfg = self.cfg.valhalla
        self._add_field(routing, r, "valhalla.base_url", "Valhalla URL:", getattr(valhalla_cfg, "base_url", "http://localhost:8002"),
                        tooltip="Адрес на Valhalla сървъра, например http://localhost:8002."); r += 1
        self._add_field(routing, r, "valhalla.costing", "Valhalla costing:", getattr(valhalla_cfg, "costing", "auto"),
                         "combo", ["auto", "truck", "bicycle", "pedestrian"],
                         tooltip="За бусове използвай auto, освен ако не настройваме truck профил."); r += 1
        self._add_field(routing, r, "valhalla.timeout_seconds", "Valhalla timeout (сек):", getattr(valhalla_cfg, "timeout_seconds", 60)); r += 1

        dropping, r = self._add_group(
            f,
            "Пропускане на заявки",
            "Когато всички заявки не могат да влязат в ограниченията, тези настройки управляват кои клиенти са по-лесни за оставяне за склад.",
        )
        self._add_field(dropping, r, "cvrp.allow_customer_skipping", "Позволи пропускане:", c.allow_customer_skipping, "bool"); r += 1
        self._add_field(dropping, r, "cvrp.distance_penalty_disjunction", "Базова глоба:", c.distance_penalty_disjunction,
                        tooltip="Използва се като fallback, когато приоритетното изхвърляне е изключено."); r += 1
        self._add_field(dropping, r, "cvrp.enable_priority_dropping", "Приоритетно изхвърляне:", c.enable_priority_dropping, "bool"); r += 1
        self._add_field(dropping, r, "cvrp.drop_volume_weight", "Тежест обем:", c.drop_volume_weight,
                        tooltip="По-висока стойност пази по-малките заявки и прави големите по-лесни за пропускане."); r += 1
        self._add_field(dropping, r, "cvrp.drop_closeness_weight", "Тежест близост:", c.drop_closeness_weight,
                        tooltip="По-висока стойност прави близките до депото заявки по-лесни за пропускане."); r += 1
        self._add_field(dropping, r, "cvrp.min_customer_drop_penalty", "Мин. глоба клиент:", c.min_customer_drop_penalty,
                        tooltip="Най-лесните за пропускане клиенти получават тази защита."); r += 1
        self._add_field(dropping, r, "cvrp.max_customer_drop_penalty", "Макс. глоба клиент:", c.max_customer_drop_penalty,
                        tooltip="Най-защитените клиенти получават тази стойност."); r += 1

        parallel, r = self._add_group(
            f,
            "Паралелно търсене",
            "Тази секция се показва само за PyVRP и OR-Tools. Общите полета управляват външните процеси, а долната част се сменя според избрания solver.",
        )
        self._add_field(parallel, r, "cvrp.enable_parallel_solving", "Включено:", c.enable_parallel_solving, "bool"); r += 1
        self._add_field(parallel, r, "cvrp.num_workers", "Брой процеси:", c.num_workers,
                         tooltip="-1 = всички ядра без едно"); r += 1

        self.pyvrp_parallel_specific = ttk.Frame(parallel, style="Surface.TFrame")
        self.pyvrp_parallel_specific.grid(row=r, column=0, columnspan=3, sticky="we")
        self.pyvrp_parallel_specific.columnconfigure(1, weight=1)
        self._add_field(
            self.pyvrp_parallel_specific,
            0,
            "cvrp.pyvrp_seed_base",
            "PyVRP seed база:",
            getattr(c, "pyvrp_seed_base", 42),
            tooltip="При PyVRP worker-ите използват seed база + номер на worker.",
        )
        r += 1

        self.ortools_parallel_specific = ttk.Frame(parallel, style="Surface.TFrame")
        self.ortools_parallel_specific.grid(row=r, column=0, columnspan=3, sticky="we")
        self.ortools_parallel_specific.columnconfigure(1, weight=1)
        self._add_list_field(
            self.ortools_parallel_specific,
            0,
            "cvrp.parallel_first_solution_strategies",
            "First solution стратегии:",
            c.parallel_first_solution_strategies,
        )
        self._add_list_field(
            self.ortools_parallel_specific,
            1,
            "cvrp.parallel_local_search_metaheuristics",
            "Метаевристики:",
            c.parallel_local_search_metaheuristics,
        )

        self.solver_fine_panels = {
            "pyvrp": (pyvrp_quality, pyvrp_penalty, parallel),
            "pyvrp_experimental": (pyvrp_quality, pyvrp_penalty, pyvrp_next, parallel),
            "or_tools": (ortools, parallel),
            "vroom": (vroom_group,),
            "vrp": (vrp_group,),
        }

    # ── Tab: Локации ─────────────────────────────────────────

    def _add_center_zones_editor(self, parent, row, loc):
        self._editing_center_zone_index = None
        self._center_zone_unmapped_penalties = {}
        self._center_zone_loaded_penalty_keys = set()
        box = ttk.LabelFrame(parent, text="Допълнителни център зони", padding=(14, 12))
        box.grid(row=row, column=0, columnspan=3, sticky="we", padx=2, pady=(6, 14))
        box.columnconfigure(0, weight=1)

        form = ttk.LabelFrame(box, text="Нова или редакция", padding=(12, 10))
        form.grid(row=0, column=0, sticky="nsew", pady=(2, 8))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(2, weight=0)

        list_frame = ttk.LabelFrame(box, text="Създадени зони", padding=(12, 10))
        list_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 2))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)

        ttk.Label(form, text="Име", style="Surface.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        name_var = tk.StringVar(value="")
        name_entry = ttk.Entry(form, textvariable=name_var, width=28)
        name_entry.grid(row=0, column=1, columnspan=2, sticky="we", padx=(0, 0), pady=5)
        self._bind_text_editing(name_entry)
        self.widgets["locations.new_center_zone_name"] = name_var

        ttk.Label(form, text="Тип", style="Surface.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        mode_var = tk.StringVar(value="circle")
        mode_combo = ttk.Combobox(form, textvariable=mode_var, values=["circle", "polygon"], width=12, state="readonly")
        mode_combo.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=5)
        mode_combo.bind("<<ComboboxSelected>>", self._update_center_zone_shape_fields)
        self.widgets["locations.new_center_zone_mode"] = mode_var
        ttk.Label(form, text="circle = радиус, polygon = начертана зона", style="Hint.TLabel").grid(
            row=1, column=2, sticky="w", padx=(0, 0), pady=5
        )

        self._center_zone_coords_label = ttk.Label(form, text="Център (lat, lon)", style="Surface.TLabel")
        self._center_zone_coords_label.grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=5)
        coords_text = tk.Text(form, height=4, width=46, font=("Segoe UI", 9), wrap="none", relief="solid", borderwidth=1)
        coords_text.grid(row=2, column=1, columnspan=2, sticky="we", padx=(0, 0), pady=5)
        self._bind_text_editing(coords_text)
        self._bind_text_scrolling(coords_text)
        self.widgets["locations.new_center_zone_coords"] = coords_text
        coord_actions = ttk.Frame(form, style="Surface.TFrame")
        coord_actions.grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(coord_actions, text="Постави координати", command=lambda: self._paste_to_text_widget(coords_text)).pack(side="left", padx=(0, 6))
        self._center_zone_map_button = ttk.Button(
            coord_actions,
            text="Избери кръг на карта",
            command=self._open_new_center_zone_editor,
        )
        self._center_zone_map_button.pack(side="left")
        self._center_zone_geometry_hint_var = tk.StringVar(value="Една GPS точка за центъра на кръга.")
        ttk.Label(
            form,
            textvariable=self._center_zone_geometry_hint_var,
            style="Hint.TLabel",
        ).grid(row=3, column=0, sticky="w", padx=(0, 8), pady=(0, 8))

        radius_label = ttk.Label(form, text="Радиус км", style="Surface.TLabel")
        radius_label.grid(row=4, column=0, sticky="w", padx=(0, 8), pady=5)
        radius_var = tk.StringVar(value="1.5")
        radius_combo = ttk.Combobox(form, textvariable=radius_var, values=["0.5", "1", "1.5", "2", "3", "5"], width=10, state="normal")
        radius_combo.grid(row=4, column=1, sticky="w", padx=(0, 8), pady=5)
        self._bind_text_editing(radius_combo)
        self.widgets["locations.new_center_zone_radius"] = radius_var
        radius_hint = ttk.Label(form, text="трябва да е по-голям от 0", style="Hint.TLabel")
        radius_hint.grid(row=4, column=2, sticky="w", pady=5)
        self._center_zone_radius_widgets = (radius_label, radius_combo, radius_hint)

        ttk.Label(form, text="Състояние", style="Surface.TLabel").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=5)
        state_frame = ttk.Frame(form, style="Surface.TFrame")
        state_frame.grid(row=5, column=1, columnspan=2, sticky="w", pady=5)
        enabled_var = tk.BooleanVar(value=True)
        show_on_map_var = tk.BooleanVar(value=True)
        enable_priority_var = tk.BooleanVar(value=True)
        enable_restrictions_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(state_frame, text="Активна", variable=enabled_var).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(state_frame, text="Показвай на картата", variable=show_on_map_var).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(
            state_frame,
            text="Приоритет",
            variable=enable_priority_var,
            command=self._update_center_zone_rule_fields,
        ).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(
            state_frame,
            text="Ограничения",
            variable=enable_restrictions_var,
            command=self._update_center_zone_rule_fields,
        ).pack(side="left")
        self.widgets["locations.new_center_zone_enabled"] = enabled_var
        self.widgets["locations.new_center_zone_show_on_map"] = show_on_map_var
        self.widgets["locations.new_center_zone_enable_priority"] = enable_priority_var
        self.widgets["locations.new_center_zone_enable_restrictions"] = enable_restrictions_var

        ttk.Label(form, text="Правило за бусове", style="Surface.TLabel").grid(row=6, column=0, sticky="w", padx=(0, 8), pady=5)
        preset_var = tk.StringVar(value="center_bus")
        preset_combo = ttk.Combobox(
            form,
            textvariable=preset_var,
            values=["center_bus", "internal_bus", "external_bus", "special_bus", "vratza_bus", "custom"],
            width=18,
            state="readonly",
        )
        preset_combo.grid(row=6, column=1, sticky="w", padx=(0, 8), pady=5)
        preset_combo.bind("<<ComboboxSelected>>", self._apply_center_zone_rule_preset)
        self._center_zone_rule_preset_combo = preset_combo
        self.widgets["locations.new_center_zone_rule_preset"] = preset_var
        ttk.Label(form, text="избира кой бус е приоритетен; custom оставя ръчните отметки", style="Hint.TLabel").grid(
            row=6, column=2, sticky="w", pady=5
        )

        rules = ttk.Frame(form, style="Surface.TFrame")
        rules.grid(row=7, column=0, columnspan=3, sticky="we", pady=(8, 6))
        ttk.Label(rules, text="Тип бус", style="Surface.TLabel", width=18).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 4))
        ttk.Label(rules, text="Приоритет", style="Surface.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 22), pady=(0, 4))
        ttk.Label(rules, text="Ограничен", style="Surface.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 12), pady=(0, 4))
        ttk.Label(rules, text="Глоба", style="Surface.TLabel").grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(0, 4))
        vehicle_labels = [
            ("center_bus", "Център бус"),
            ("internal_bus", "Вътрешен бус"),
            ("external_bus", "Външен бус"),
            ("special_bus", "Специален бус"),
            ("vratza_bus", "Враца бус"),
        ]
        self.center_zone_priority_vars = {}
        self.center_zone_restricted_vars = {}
        self.center_zone_penalty_vars = {}
        self._center_zone_priority_checkbuttons = {}
        self._center_zone_restricted_checkbuttons = {}
        self._center_zone_penalty_entries = {}
        penalty_defaults = {
            "center_bus": 40000.0,
            "internal_bus": float(getattr(loc, "internal_bus_center_penalty", 40000.0) or 40000.0),
            "external_bus": float(getattr(loc, "external_bus_center_penalty", 40000.0) or 40000.0),
            "special_bus": float(getattr(loc, "special_bus_center_penalty", 40000.0) or 40000.0),
            "vratza_bus": float(getattr(loc, "vratza_bus_center_penalty", 40000.0) or 40000.0),
        }
        for idx, (vehicle_type, label) in enumerate(vehicle_labels, start=1):
            ttk.Label(rules, text=label, style="Surface.TLabel").grid(row=idx, column=0, sticky="w", padx=(0, 12), pady=2)
            priority_var = tk.BooleanVar(value=(vehicle_type == "center_bus"))
            restricted_var = tk.BooleanVar(value=(vehicle_type != "center_bus"))
            priority_check = ttk.Checkbutton(rules, variable=priority_var)
            priority_check.grid(row=idx, column=1, sticky="w", padx=(0, 22), pady=2)
            restricted_check = ttk.Checkbutton(
                rules,
                variable=restricted_var,
                command=self._update_center_zone_rule_fields,
            )
            restricted_check.grid(row=idx, column=2, sticky="w", padx=(0, 12), pady=2)
            penalty_var = tk.StringVar(value=f"{penalty_defaults[vehicle_type]:g}")
            penalty_entry = ttk.Entry(rules, textvariable=penalty_var, width=12)
            penalty_entry.grid(row=idx, column=3, sticky="w", padx=(0, 8), pady=2)
            self._bind_text_editing(penalty_entry)
            self.center_zone_priority_vars[vehicle_type] = priority_var
            self.center_zone_restricted_vars[vehicle_type] = restricted_var
            self.center_zone_penalty_vars[vehicle_type] = penalty_var
            self._center_zone_priority_checkbuttons[vehicle_type] = priority_check
            self._center_zone_restricted_checkbuttons[vehicle_type] = restricted_check
            self._center_zone_penalty_entries[vehicle_type] = penalty_entry

        ttk.Label(form, text="Отстъпка", style="Surface.TLabel").grid(row=8, column=0, sticky="w", padx=(0, 8), pady=5)
        discount_var = tk.StringVar(value="0.9")
        discount_combo = ttk.Combobox(form, textvariable=discount_var, values=["0.7", "0.8", "0.9", "1.0"], width=10, state="normal")
        discount_combo.grid(row=8, column=1, sticky="w", padx=(0, 8), pady=5)
        self._bind_text_editing(discount_combo)
        self.widgets["locations.new_center_zone_discount"] = discount_var
        ttk.Label(form, text="0.9 = 10% по-ниска цена", style="Hint.TLabel").grid(row=8, column=2, sticky="w", pady=5)

        ttk.Label(form, text="Глоба навън", style="Surface.TLabel").grid(row=9, column=0, sticky="w", padx=(0, 8), pady=5)
        outside_var = tk.StringVar(value="0")
        outside_entry = ttk.Entry(form, textvariable=outside_var, width=12)
        outside_entry.grid(row=9, column=1, sticky="w", padx=(0, 8), pady=5)
        self._bind_text_editing(outside_entry)
        self.widgets["locations.new_center_zone_outside_penalty"] = outside_var
        self._center_zone_priority_value_widgets = (discount_combo, outside_entry)

        buttons = ttk.Frame(form, style="Surface.TFrame")
        buttons.grid(row=10, column=1, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Добави / обнови", command=self._append_center_zone_from_fields).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Нова празна форма", command=self._clear_center_zone_form).pack(side="left")

        ttk.Label(
            list_frame,
            text="Избери зона, за да я заредиш във формата. После можеш да я обновиш или премахнеш.",
            style="Hint.TLabel",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 8))
        self.center_zone_tree = ttk.Treeview(
            list_frame,
            columns=("name", "mode", "geometry", "priority", "restricted", "status", "map_visibility"),
            show="headings",
            height=7,
            selectmode="browse",
        )
        for column, title, width in (
            ("name", "Име", 130),
            ("mode", "Тип", 70),
            ("geometry", "GPS/полигон", 180),
            ("priority", "Приоритет", 110),
            ("restricted", "Глоба за", 140),
            ("status", "Статус", 80),
            ("map_visibility", "На карта", 85),
        ):
            self.center_zone_tree.heading(column, text=title)
            self.center_zone_tree.column(column, width=width, minwidth=60, stretch=True)
        self.center_zone_tree.grid(row=1, column=0, sticky="nsew")
        center_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.center_zone_tree.yview)
        center_scroll.grid(row=1, column=1, sticky="ns")
        self.center_zone_tree.configure(yscrollcommand=center_scroll.set)
        self.center_zone_tree.bind("<<TreeviewSelect>>", self._load_selected_center_zone)
        zone_actions = ttk.Frame(list_frame, style="Surface.TFrame")
        zone_actions.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Button(zone_actions, text="Покажи на карта", command=self._show_selected_center_zone_on_map).pack(side="left", padx=(0, 6))
        ttk.Button(zone_actions, text="Покажи / скрий в картите", command=self._toggle_selected_center_zone_visibility).pack(side="left", padx=(0, 6))
        ttk.Button(zone_actions, text="Премахни избраната", command=self._remove_selected_center_zone).pack(side="left")

        hidden_zones = tk.Text(box, width=1, height=1)
        self.widgets["locations.center_zones"] = hidden_zones
        self._sync_center_zone_widgets(getattr(loc, "center_zones", []) or [])
        self._update_center_zone_shape_fields()
        self._update_center_zone_rule_fields()

    def _add_locations_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 📍 Локации ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        loc = self.cfg.locations
        r = 0
        main_locations, gr = self._add_grid_group(
            f,
            r,
            "Основни точки",
            "Главните координати, които се използват за депа и център зона.",
        ); r += 1
        self._add_field(main_locations, gr, "locations.depot_location", "Главно депо:",
                         f"{loc.depot_location[0]}, {loc.depot_location[1]}"); gr += 1
        self._add_field(main_locations, gr, "locations.center_location", "Център:",
                         f"{loc.center_location[0]}, {loc.center_location[1]}"); gr += 1
        self._add_field(main_locations, gr, "locations.vratza_depot_location", "Враца депо:",
                         f"{loc.vratza_depot_location[0]}, {loc.vratza_depot_location[1]}"); gr += 1

        depot_add, gr = self._add_grid_group(
            f,
            r,
            "Депа",
            "Добави депо с име и координати. След Запази депото може да се избира при бусове.",
        ); r += 1
        depot_add.columnconfigure(0, weight=1)
        depot_add.columnconfigure(1, weight=1)
        depot_form = ttk.LabelFrame(depot_add, text="Ново или редакция", padding=(12, 10))
        depot_form.grid(row=gr, column=0, sticky="nsew", padx=(0, 8), pady=2)
        depot_form.columnconfigure(1, weight=1)
        depot_list_frame = ttk.LabelFrame(depot_add, text="Допълнителни депа", padding=(12, 10))
        depot_list_frame.grid(row=gr, column=1, columnspan=2, sticky="nsew", padx=(8, 0), pady=2)
        depot_list_frame.columnconfigure(0, weight=1)

        ttk.Label(depot_form, text="Име", style="Surface.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        depot_name_var = tk.StringVar(value="")
        depot_name_entry = ttk.Entry(depot_form, textvariable=depot_name_var, width=28)
        depot_name_entry.grid(row=0, column=1, sticky="we", pady=5)
        self._bind_text_editing(depot_name_entry)
        self.widgets["locations.new_depot_name"] = depot_name_var

        ttk.Label(depot_form, text="Координати", style="Surface.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        depot_coords_var = tk.StringVar(value="")
        depot_coords_entry = ttk.Entry(depot_form, textvariable=depot_coords_var, width=28)
        depot_coords_entry.grid(row=1, column=1, sticky="we", pady=5)
        self._bind_text_editing(depot_coords_entry)
        self.widgets["locations.new_depot_coords"] = depot_coords_var

        depot_buttons = ttk.Frame(depot_form, style="Surface.TFrame")
        depot_buttons.grid(row=2, column=1, sticky="w", pady=(2, 8))
        ttk.Button(depot_buttons, text="Постави", command=lambda: self._paste_to_var(depot_coords_var)).pack(side="left", padx=(0, 6))
        ttk.Button(depot_buttons, text="Избери на карта", command=self._open_new_depot_editor).pack(side="left")

        depot_actions = ttk.Frame(depot_form, style="Surface.TFrame")
        depot_actions.grid(row=3, column=1, sticky="w", pady=(8, 0))
        ttk.Button(depot_actions, text="Добави / обнови", command=self._append_depot_from_fields).pack(side="left", padx=(0, 6))
        ttk.Button(depot_actions, text="Нова празна форма", command=self._clear_depot_form).pack(side="left")

        ttk.Label(
            depot_list_frame,
            text="Избери депо, за да го заредиш във формата. Главно депо, Център и Враца се настройват горе.",
            style="Hint.TLabel",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 8))
        self.depot_tree = ttk.Treeview(
            depot_list_frame,
            columns=("name", "gps"),
            show="headings",
            height=6,
            selectmode="browse",
        )
        self.depot_tree.heading("name", text="Име")
        self.depot_tree.heading("gps", text="GPS")
        self.depot_tree.column("name", width=170, minwidth=100, stretch=True)
        self.depot_tree.column("gps", width=190, minwidth=130, stretch=True)
        self.depot_tree.grid(row=1, column=0, sticky="nsew")
        depot_scroll = ttk.Scrollbar(depot_list_frame, orient="vertical", command=self.depot_tree.yview)
        depot_scroll.grid(row=1, column=1, sticky="ns")
        self.depot_tree.configure(yscrollcommand=depot_scroll.set)
        self.depot_tree.bind("<<TreeviewSelect>>", self._load_selected_depot)
        depot_table_actions = ttk.Frame(depot_list_frame, style="Surface.TFrame")
        depot_table_actions.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Button(depot_table_actions, text="Покажи на карта", command=self._show_selected_depot_on_map).pack(side="left", padx=(0, 6))
        ttk.Button(depot_table_actions, text="Премахни избраното", command=self._remove_selected_depot).pack(side="left")

        hidden_depots = tk.Text(depot_add, width=1, height=1)
        self.widgets["locations.depot_locations"] = hidden_depots
        self._sync_depot_widgets(getattr(loc, "depot_locations", {}) or {})

        center_zone, gr = self._add_grid_group(
            f,
            r,
            "Център зона",
            "Правилата, които насочват CENTER_BUS към центъра и пазят другите бусове от центъра.",
        ); r += 1
        self._add_field(center_zone, gr, "locations.center_zone_mode", "Тип зона:", getattr(loc, "center_zone_mode", "circle"),
                         "combo", ["circle", "polygon"]); gr += 1
        self._add_field(center_zone, gr, "locations.center_zone_radius_km", "Радиус (км):", loc.center_zone_radius_km); gr += 1
        self._add_polygon_field(center_zone, gr, "locations.center_zone_polygon", "Начертана зона:", getattr(loc, "center_zone_polygon", [])); gr += 1
        self._add_field(center_zone, gr, "locations.show_center_zone_on_map", "Показвай на картата:",
                        getattr(loc, "show_center_zone_on_map", True), "bool"); gr += 1
        self._add_field(center_zone, gr, "locations.enable_center_zone_priority", "Приоритет:", loc.enable_center_zone_priority, "bool"); gr += 1
        self._add_field(center_zone, gr, "locations.enable_center_zone_restrictions", "Ограничения:", loc.enable_center_zone_restrictions, "bool"); gr += 1
        self._add_field(center_zone, gr, "locations.discount_center_bus", "Отстъпка CENTER_BUS:", loc.discount_center_bus,
                         tooltip="0.5 = плаща 50% от разстоянието"); gr += 1
        self._add_field(center_zone, gr, "locations.center_bus_outside_center_penalty", "Глоба CENTER_BUS навън:", getattr(loc, "center_bus_outside_center_penalty", 50000.0),
                         tooltip="Добавя се към цената, когато CENTER_BUS обслужва клиент извън център зоната."); gr += 1
        self._add_center_zones_editor(center_zone, gr, loc); gr += 1

        center_penalties, gr = self._add_grid_group(
            f,
            r,
            "Глоби за влизане в центъра",
            "Колко да се оскъпява обслужване на център клиент от нецентрален бус.",
        ); r += 1
        self._add_field(center_penalties, gr, "locations.internal_bus_center_penalty", "Вътрешен бус:", loc.internal_bus_center_penalty); gr += 1
        self._add_field(center_penalties, gr, "locations.external_bus_center_penalty", "Външен бус:", loc.external_bus_center_penalty); gr += 1
        self._add_field(center_penalties, gr, "locations.special_bus_center_penalty", "Специален бус:", loc.special_bus_center_penalty); gr += 1
        self._add_field(center_penalties, gr, "locations.vratza_bus_center_penalty", "Враца бус:", loc.vratza_bus_center_penalty); gr += 1

        city_traffic, gr = self._add_grid_group(
            f,
            r,
            "Основна трафик зона",
            "Тази зона умножава времето за отсечки вътре в нея.",
        ); r += 1
        self._add_field(city_traffic, gr, "locations.enable_city_traffic_adjustment", "Включена:", loc.enable_city_traffic_adjustment, "bool"); gr += 1
        self._add_field(city_traffic, gr, "locations.city_center_coords", "Център:",
                         f"{loc.city_center_coords[0]}, {loc.city_center_coords[1]}"); gr += 1
        self._add_field(city_traffic, gr, "locations.city_traffic_radius_km", "Радиус (км):", loc.city_traffic_radius_km); gr += 1
        self._add_field(city_traffic, gr, "locations.city_traffic_duration_multiplier", "Множител:", loc.city_traffic_duration_multiplier,
                         tooltip="1.4 = +40% заради трафик"); gr += 1
        self._add_field(city_traffic, gr, "locations.show_city_traffic_zone_on_map", "Показвай на картата:",
                        getattr(loc, "show_city_traffic_zone_on_map", False), "bool"); gr += 1

        traffic_add = ttk.LabelFrame(f, text="Трафик зони", padding=(14, 12))
        traffic_add.grid(row=r, column=0, columnspan=3, sticky="we", padx=2, pady=(6, 14))
        traffic_add.columnconfigure(0, weight=0)
        traffic_add.columnconfigure(1, weight=1)
        traffic_add.columnconfigure(2, weight=0)
        traffic_add.columnconfigure(3, weight=0)

        traffic_add.columnconfigure(0, weight=1)
        traffic_add.columnconfigure(1, weight=1)
        traffic_form = ttk.LabelFrame(traffic_add, text="Нова или редакция", padding=(12, 10))
        traffic_form.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=2)
        traffic_form.columnconfigure(1, weight=1)
        traffic_list_frame = ttk.LabelFrame(traffic_add, text="Създадени трафик зони", padding=(12, 10))
        traffic_list_frame.grid(row=0, column=1, columnspan=3, sticky="nsew", padx=(8, 0), pady=2)
        traffic_list_frame.columnconfigure(0, weight=1)

        ttk.Label(traffic_form, text="Име", style="Surface.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        traffic_name_var = tk.StringVar(value="")
        traffic_name_entry = ttk.Entry(traffic_form, textvariable=traffic_name_var, width=28)
        traffic_name_entry.grid(row=0, column=1, sticky="we", pady=5)
        self._bind_text_editing(traffic_name_entry)
        self.widgets["locations.new_traffic_zone_name"] = traffic_name_var

        ttk.Label(traffic_form, text="Център", style="Surface.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        coords_var = tk.StringVar(value="")
        coords_entry = ttk.Entry(traffic_form, textvariable=coords_var, width=28)
        coords_entry.grid(row=1, column=1, sticky="we", pady=5)
        self._bind_text_editing(coords_entry)
        self.widgets["locations.new_traffic_zone_coords"] = coords_var

        traffic_coord_buttons = ttk.Frame(traffic_form, style="Surface.TFrame")
        traffic_coord_buttons.grid(row=2, column=1, sticky="w", pady=(2, 8))
        ttk.Button(traffic_coord_buttons, text="Постави", command=lambda: self._paste_to_var(coords_var)).pack(side="left", padx=(0, 6))
        ttk.Button(traffic_coord_buttons, text="Избери кръг на карта", command=self._open_new_traffic_zone_editor).pack(side="left")

        ttk.Label(traffic_form, text="Радиус км", style="Surface.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=5)
        radius_var = tk.StringVar(value="3")
        radius_combo = ttk.Combobox(traffic_form, textvariable=radius_var, values=["1", "2", "3", "5", "8", "10"], width=10, state="normal")
        radius_combo.grid(row=3, column=1, sticky="w", pady=5)
        self._bind_text_editing(radius_combo)
        self.widgets["locations.new_traffic_zone_radius"] = radius_var

        ttk.Label(traffic_form, text="Забавяне %", style="Surface.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=5)
        delay_var = tk.StringVar(value="30")
        delay_combo = ttk.Combobox(traffic_form, textvariable=delay_var, values=["10", "20", "30", "40", "50"], width=10, state="normal")
        delay_combo.grid(row=4, column=1, sticky="w", pady=5)
        self._bind_text_editing(delay_combo)
        self.widgets["locations.new_traffic_zone_delay"] = delay_var

        show_on_map_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            traffic_form,
            text="Показвай зоната на картата",
            variable=show_on_map_var,
        ).grid(row=5, column=1, sticky="w", pady=5)
        self.widgets["locations.new_traffic_zone_show_on_map"] = show_on_map_var

        traffic_buttons = ttk.Frame(traffic_form, style="Surface.TFrame")
        traffic_buttons.grid(row=6, column=1, sticky="w", pady=(10, 0))
        ttk.Button(traffic_buttons, text="Добави / обнови", command=self._append_traffic_zone_from_fields).pack(side="left", padx=(0, 6))
        ttk.Button(traffic_buttons, text="Нова празна форма", command=self._clear_traffic_zone_form).pack(side="left")

        ttk.Label(
            traffic_list_frame,
            text="Избери зона, за да я заредиш във формата. Забавянето се записва като множител на времето.",
            style="Hint.TLabel",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 8))

        self.traffic_zone_tree = ttk.Treeview(
            traffic_list_frame,
            columns=("name", "gps", "radius", "delay", "status", "map_visibility"),
            show="headings",
            height=8,
            selectmode="browse",
        )
        for column, title, width in (
            ("name", "Име", 150),
            ("gps", "GPS", 190),
            ("radius", "Радиус км", 80),
            ("delay", "Забавяне", 80),
            ("status", "Статус", 85),
            ("map_visibility", "На карта", 85),
        ):
            self.traffic_zone_tree.heading(column, text=title)
            self.traffic_zone_tree.column(column, width=width, minwidth=60, stretch=True)
        self.traffic_zone_tree.grid(row=1, column=0, sticky="nsew")
        traffic_scroll = ttk.Scrollbar(traffic_list_frame, orient="vertical", command=self.traffic_zone_tree.yview)
        traffic_scroll.grid(row=1, column=1, sticky="ns")
        self.traffic_zone_tree.configure(yscrollcommand=traffic_scroll.set)
        self.traffic_zone_tree.bind("<<TreeviewSelect>>", self._load_selected_traffic_zone)
        traffic_table_actions = ttk.Frame(traffic_list_frame, style="Surface.TFrame")
        traffic_table_actions.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Button(traffic_table_actions, text="Покажи на карта", command=self._show_selected_traffic_zone_on_map).pack(side="left", padx=(0, 6))
        ttk.Button(traffic_table_actions, text="Покажи / скрий в картите", command=self._toggle_selected_traffic_zone_visibility).pack(side="left", padx=(0, 6))
        ttk.Button(traffic_table_actions, text="Премахни избраната", command=self._remove_selected_traffic_zone).pack(side="left")

        hidden_zones = tk.Text(traffic_add, width=1, height=1)
        self.widgets["locations.traffic_zones"] = hidden_zones
        self._sync_traffic_zone_widgets(getattr(loc, "traffic_zones", []) or [])
        r += 1

    # ── Tab: Изходни данни ───────────────────────────────────

    def _add_output_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 📄 Изходни данни ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        out = self.cfg.output
        r = 0

        maps, gr = self._add_grid_group(
            f,
            r,
            "Карти",
            "Интерактивната карта и отделните HTML маршрути за проверка на решението.",
        ); r += 1
        self._add_field(maps, gr, "output.enable_interactive_map", "Генерирай карта:", out.enable_interactive_map, "bool"); gr += 1
        self._add_field(maps, gr, "output.map_output_file", "Файл карта:", out.map_output_file); gr += 1
        self._add_field(maps, gr, "output.routes_output_dir", "HTML маршрути:", out.routes_output_dir,
                         tooltip="Отделни HTML файлове за всеки маршрут"); gr += 1
        self._add_field(
            maps,
            gr,
            "output.route_maps_upload_mode",
            "Качване маршрути:",
            getattr(out, "route_maps_upload_mode", "disabled"),
            "combo",
            ["disabled", "legacy", "effect_upload"],
            tooltip="disabled = не качва. legacy = старото поведение. effect_upload = качва индивидуалните HTML карти към upload endpoint.",
        ); gr += 1
        self._add_field(maps, gr, "output.route_maps_upload_url", "Upload URL:", getattr(out, "route_maps_upload_url", ""),
                         tooltip="Endpoint за индивидуалните route HTML файлове."); gr += 1
        self._add_field(
            maps,
            gr,
            "output.route_maps_upload_token",
            "Upload token:",
            getattr(out, "route_maps_upload_token", ""),
            "secret",
            tooltip="Стойност за pData. Използвай token-а за конкретния upload endpoint и не го публикувай.",
        ); gr += 1
        self._add_field(maps, gr, "output.route_maps_upload_token_field", "Token поле:", getattr(out, "route_maps_upload_token_field", "pData"),
                         tooltip="Име на POST полето за token-а."); gr += 1
        self._add_field(maps, gr, "output.route_maps_upload_file_field", "File поле:", getattr(out, "route_maps_upload_file_field", "files[]"),
                         tooltip="За PHP $_FILES['files'] с много файлове използвай files[]."); gr += 1
        self._add_field(maps, gr, "output.route_maps_upload_bus_id_field", "Bus ID поле:", getattr(out, "route_maps_upload_bus_id_field", "pData2[]"),
                         tooltip="За PHP масив с ID-та на бусовете използвай pData2[]. Редът съвпада с files[]."); gr += 1
        self._add_field(maps, gr, "output.route_maps_upload_timeout_seconds", "Upload timeout:", getattr(out, "route_maps_upload_timeout_seconds", 60), "int"); gr += 1
        self._add_field(maps, gr, "output.map_provider", "Map provider:", out.map_provider,
                         tooltip='google за Google Maps визуализация или osm за Folium/OpenStreetMap.'); gr += 1
        self._add_field(maps, gr, "output.folium_tiles", "Folium tiles:", out.folium_tiles,
                         tooltip='Например "Esri.WorldStreetMap", "Esri.WorldTopoMap", "CartoDB Voyager"'); gr += 1
        self._add_field(maps, gr, "output.google_maps_api_key", "Google Maps key:", out.google_maps_api_key, "secret",
                         tooltip="Може и чрез GOOGLE_MAPS_API_KEY env variable."); gr += 1

        excel, gr = self._add_grid_group(
            f,
            r,
            "Excel отчети",
            "Основните файлове за работа и проверка след run.",
        ); r += 1
        self._add_field(excel, gr, "output.enable_excel_output", "Генерирай Excel:", getattr(out, "enable_excel_output", True), "bool"); gr += 1
        self._add_field(excel, gr, "output.excel_output_dir", "Директория:", out.excel_output_dir); gr += 1
        self._add_field(excel, gr, "output.routes_excel_file", "Маршрути:", out.routes_excel_file); gr += 1
        self._add_field(excel, gr, "output.warehouse_excel_file", "Склад:", out.warehouse_excel_file); gr += 1
        self._add_field(excel, gr, "output.efficiency_excel_file", "Ефективност:", out.efficiency_excel_file); gr += 1
        self._add_field(excel, gr, "output.excel_bus_number_prefix", "Префикс ID бус:", getattr(out, "excel_bus_number_prefix", "10045010")); gr += 1
        self._add_field(excel, gr, "output.excel_bus_number_digits", "Цифри:", getattr(out, "excel_bus_number_digits", 2), "int"); gr += 1
        self._add_field(excel, gr, "output.saturday_excel_bus_number_prefix", "Префикс за събота:", getattr(out, "saturday_excel_bus_number_prefix", "100450121"),
                        tooltip="Използва се автоматично за normal номерирането при HTTP /run_saturday. Активното CENTER_BUS ID правило има предимство."); gr += 1
        self._add_field(excel, gr, "output.saturday_excel_bus_number_digits", "Цифри за събота:", getattr(out, "saturday_excel_bus_number_digits", 1), "int",
                        tooltip="Брой цифри след съботния префикс, например 1 дава ...1, ...2, ...3."); gr += 1
        self._add_field(excel, gr, "output.center_bus_numbering_enabled", "Център ID правило:", getattr(out, "center_bus_numbering_enabled", True), "bool",
                         tooltip="Включено: CENTER_BUS получава зададения ID и следващите център бусове вървят нагоре; другите бусове си тръгват от 1004501001 нагоре."); gr += 1
        self._add_field(excel, gr, "output.center_bus_numbering_start_id", "ID център бус:", getattr(out, "center_bus_numbering_start_id", "1004501015"),
                         tooltip="Първи ID за CENTER_BUS. Ако има повече център бусове: този ID, после +1, +2. Другите бусове остават по стандартната последователност от 1004501001."); gr += 1

        csv_group, gr = self._add_grid_group(
            f,
            r,
            "CSV",
            "Лек машинно-четим export за външни системи.",
        ); r += 1
        self._add_field(csv_group, gr, "output.enable_csv_output", "Генерирай CSV:", getattr(out, "enable_csv_output", True), "bool"); gr += 1
        self._add_field(csv_group, gr, "output.csv_output_file", "Файл CSV:", out.csv_output_file); gr += 1

        charts, gr = self._add_grid_group(
            f,
            r,
            "Графики",
            "Допълнителни визуални проверки за маршрутите.",
        ); r += 1
        self._add_field(charts, gr, "output.enable_charts", "Генерирай графики:", out.enable_charts, "bool"); gr += 1
        self._add_field(charts, gr, "output.charts_output_dir", "Директория:", out.charts_output_dir); gr += 1

    # ── Tab: Планировчик ────────────────────────────────────

    # API server settings

    def _add_api_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" API сървър ")
        f = self._make_scrollable_frame(tab)
        api = getattr(self.cfg, "api", None)
        base_url = self._api_base_url_preview(api)
        solve_path = self._api_path_preview(api, "api_endpoint", "/solve")
        trigger_path = self._api_path_preview(api, "trigger_endpoint", "/run")
        saturday_trigger_path = self._api_path_preview(api, "saturday_trigger_endpoint", "/run_saturday")
        tsp_path = self._api_path_preview(api, "tsp_endpoint", "/tsp")
        health_path = self._api_path_preview(api, "health_endpoint", "/health")
        shutdown_path = self._api_path_preview(api, "shutdown_endpoint", "/shutdown")
        api_key = str(getattr(api, "api_key", "") or "").strip()
        auth_header = f' -H "X-CVRP-API-Key: {api_key}"' if api_key else ""

        quick, r = self._add_group(
            f,
            "Бърз старт",
            "Това са командите, които най-често ще се копират от външна програма или от terminal.",
        )
        r = self._add_copyable_command(
            quick,
            r,
            "Старт без вход:",
            f'curl{auth_header} "{base_url}{trigger_path}"',
            "Стартира оптимизацията с текущите настройки и входния източник от config.py. Връща веднага 202 started.",
        )
        r = self._add_copyable_command(
            quick,
            r,
            "Съботен старт:",
            f'curl{auth_header} "{base_url}{saturday_trigger_path}"',
            "Автоматично използва съботата и отделния normal префикс; CENTER_BUS серията има предимство, ако е включена.",
        )
        r = self._add_copyable_command(
            quick,
            r,
            "Провери статус:",
            f'curl "{base_url}{health_path}"',
            "Показва дали API-то е живо, URL-ите и текущия/последния run статус.",
        )
        r = self._add_copyable_command(
            quick,
            r,
            "JSON решение:",
            f'curl -X POST "{base_url}{solve_path}" -H "Content-Type: application/json"{auth_header} -d "{{\\"customers\\":[...]}}"',
            "Използва се когато външната система подава клиентите директно в заявката.",
        )
        r = self._add_copyable_command(
            quick,
            r,
            "Текущ TSP:",
            f'curl -X POST "{base_url}{tsp_path}" -H "Content-Type: application/json"{auth_header} -d "{{\\"driver_id\\":\\"1004501001\\",\\"driver_location\\":\\"42.6977,23.3219\\",\\"end_location\\":\\"42.7000,23.4000\\",\\"service_time_minutes\\":8,\\"customers\\":[{{\\"id\\":\\"1\\",\\"document\\":\\"0004384359\\",\\"gps\\":\\"42.6629,23.37682\\",\\"quantity\\":5}}]}}"',
            "Подрежда текущ маршрут за един шофьор; връща JSON или директно HTML карта според настройката TSP HTML и upload -> Отговор от /tsp.",
        )
        r = self._add_copyable_command(
            quick,
            r,
            "Run + настройки:",
            f'curl -X POST "{base_url}{trigger_path}" -H "Content-Type: application/json"{auth_header} -d "{{\\"settings\\":{{\\"solver_type\\":\\"pyvrp\\",\\"objective_metric\\":\\"time\\",\\"time_limit_seconds\\":180,\\"osrm_base_url\\":\\"http://localhost:5000\\",\\"vehicles\\":[{{\\"vehicle_type\\":\\"internal_bus\\",\\"count\\":7,\\"capacity\\":385}}],\\"output\\":{{\\"excel_output_dir\\":\\"C:\\\\\\\\CVRP\\\\\\\\output\\",\\"routes_output_dir\\":\\"C:\\\\\\\\CVRP\\\\\\\\output\\\\\\\\Routes\\"}},\\"set_data\\":{{\\"enable_set_data_upload\\":false}}}}}}"',
            "Стартира /run, но само за тази заявка сменя solver, OSRM, бусове, изходни пътища и setData настройки.",
        )
        r = self._add_copyable_command(
            quick,
            r,
            "Run + JSON резултат:",
            f'curl -X POST "{base_url}{trigger_path}" -H "Content-Type: application/json"{auth_header} -d "{{\\"return_result\\":true,\\"settings\\":{{\\"solver_type\\":\\"pyvrp\\",\\"objective_metric\\":\\"time\\",\\"time_limit_seconds\\":180,\\"set_data\\":{{\\"enable_set_data_upload\\":false}}}}}}"',
            "Заявката чака програмата да завърши и връща директно JSON резултата от решението.",
        )
        r = self._add_copyable_command(
            quick,
            r,
            "Run + известие:",
            f'curl -X POST "{base_url}{trigger_path}" -H "Content-Type: application/json"{auth_header} -d "{{\\"callback_url\\":\\"https://example.com/cvrp-finished\\",\\"settings\\":{{\\"set_data\\":{{\\"enable_set_data_upload\\":false}}}}}}"',
            "Стартира във фон и след края праща POST към callback_url със статус completed/failed.",
        )
        self._add_copyable_command(
            quick,
            r,
            "Trigger през /solve:",
            f'curl -X POST "{base_url}{solve_path}?cmd=run"{auth_header}',
            "Съвместим вариант, ако външната система може да вика само solve endpoint-а.",
        )

        address, r = self._add_group(
            f,
            "Адрес",
            "Тези настройки определят къде слуша API сървърът и какъв URL да показва в примерите.",
        )
        self._add_field(
            address,
            r,
            "api.api_host",
            "Слушай на:",
            getattr(api, "api_host", "0.0.0.0"),
            tooltip="0.0.0.0 = приема заявки от мрежата. 127.0.0.1 = само от този компютър.",
        ); r += 1
        self._add_field(address, r, "api.api_port", "Порт:", getattr(api, "api_port", 8088)); r += 1
        self._add_field(
            address,
            r,
            "api.api_public_url",
            "URL за външни системи:",
            getattr(api, "api_public_url", ""),
            tooltip="Празно = автоматично. Попълни го, ако външната система трябва да вижда друг адрес.",
        ); r += 1
        self._add_field(
            address,
            r,
            "api.api_key",
            "API ключ:",
            getattr(api, "api_key", ""),
            "secret",
            tooltip="По желание. Ако е попълнен, /run и /solve искат header X-CVRP-API-Key.",
        )

        web_gui, r = self._add_group(
            f,
            "Уеб управление от други компютри",
            "Това отваря удобен browser екран за старт, прогрес, логове, бусове и основни настройки. Ползва същия порт като API сървъра.",
        )
        self._add_note_row(
            web_gui,
            r,
            "Най-често попълваш само: 1) път за отваряне, например /hell; "
            "2) име в мрежата, например bizant; 3) потребители за вход. "
            f"Портът идва от полето API порт по-горе. Пример: http://bizant:{getattr(api, 'api_port', 8088)}/hell",
        ); r += 1
        self._add_field(web_gui, r, "api.web_gui_enabled", "Пусни уеб управлението:", getattr(api, "web_gui_enabled", True), "bool",
                        tooltip="Когато е включено, можеш да отвориш управлението от browser на друг компютър."); r += 1
        self._add_field(web_gui, r, "api.web_gui_endpoint", "Път за отваряне:", getattr(api, "web_gui_endpoint", "/ui"),
                        tooltip="Краткото име след порта. Примери: /ui, /hell, /cvrp. Не е целият адрес."); r += 1
        self._add_field(web_gui, r, "api.web_gui_public_host", "Име в мрежата:", getattr(api, "web_gui_public_host", ""),
                        tooltip="Напиши само името или IP-то, например bizant или 10.10.10.155. Празно = програмата сама показва IP-то на машината."); r += 1
        self._add_field(web_gui, r, "api.web_gui_public_url", "Готов външен адрес:", getattr(api, "web_gui_public_url", ""),
                        tooltip="Обикновено остави празно. Попълва се само при Cloudflare/reverse proxy, например https://firma.example.com/hell."); r += 1
        self._add_field(web_gui, r, "api.web_gui_title", "Име на този сървър:", getattr(api, "web_gui_title", "CVRP Optimizer"),
                        tooltip="Това име се вижда най-отгоре в уеб екрана, например София, Враца или Тестова машина."); r += 1
        self._add_field(
            web_gui,
            r,
            "api.web_gui_trusted_proxy_ips",
            "Доверени reverse proxy IP/CIDR:",
            getattr(api, "web_gui_trusted_proxy_ips", "127.0.0.1,::1"),
            tooltip="Само тези директни адреси могат да задават Forwarded/X-Forwarded-For. Пример: 127.0.0.1,::1 или 10.0.0.5/32.",
        ); r += 1
        self._add_web_gui_user_controls(web_gui, r); r += 1
        self._add_note_row(
            web_gui,
            r,
            "Как да го четеш: ако API портът е 8087, пътят е /hell и името в мрежата е bizant, "
            "адресът за отваряне ще бъде http://bizant:8087/hell. "
            "Полето 'Готов външен адрес' прескача тази логика и се използва директно.",
        ); r += 1

        endpoints, r = self._add_group(
            f,
            "Endpoint-и",
            "Тези пътища могат да се сменят, ако друга система очаква конкретни имена.",
        )
        self._add_field(endpoints, r, "api.api_endpoint", "JSON solve:", getattr(api, "api_endpoint", "/solve"),
                        tooltip="POST с JSON клиенти. Сървърът чака решението и връща пълен резултат."); r += 1
        self._add_field(endpoints, r, "api.trigger_endpoint", "Старт без вход:", getattr(api, "trigger_endpoint", "/run"),
                        tooltip="GET/POST без body. Стартира програмата във фонова нишка."); r += 1
        self._add_field(endpoints, r, "api.saturday_trigger_endpoint", "Съботен старт:", getattr(api, "saturday_trigger_endpoint", "/run_saturday"),
                        tooltip="GET/POST. Задава съботната дата и normal префикс; специалното CENTER_BUS номериране има предимство."); r += 1
        self._add_field(endpoints, r, "api.tsp_endpoint", "Текущ TSP:", getattr(api, "tsp_endpoint", "/tsp"),
                        tooltip="POST. Подрежда текущ маршрут за един шофьор с текуща GPS позиция и optional крайна точка."); r += 1
        self._add_field(endpoints, r, "api.tsp_report_endpoint", "TSP отчет:", getattr(api, "tsp_report_endpoint", "/tsp-report"),
                        tooltip="GET/POST. Генерира Excel отчет за TSP маршрутите за деня."); r += 1
        self._add_field(endpoints, r, "api.shutdown_endpoint", "Спиране:", getattr(api, "shutdown_endpoint", "/shutdown"),
                        tooltip="GET/POST. Спира API сървъра/програмата. Ползвай API key при отдалечен достъп."); r += 1
        self._add_field(endpoints, r, "api.health_endpoint", "Статус:", getattr(api, "health_endpoint", "/health"),
                        tooltip="GET. Проверка дали API-то е живо и дали има активен run."); r += 1

        docs, r = self._add_group(
            f,
            "API документация",
            "Избери endpoint и виж какво прави, как се вика, какъв JSON приема и какъв отговор връща.",
        )
        self._add_api_docs_notebook(
            docs,
            r,
            self._api_endpoint_docs(base_url, solve_path, trigger_path, saturday_trigger_path, tsp_path, health_path, auth_header, shutdown_path),
        )

        tsp_optimization, r = self._add_group(
            f,
            "TSP оптимизация",
            "Тези стойности управляват как /tsp подрежда текущ маршрут, когато POST заявката не ги подаде.",
        )
        self._add_field(tsp_optimization, r, "api.tsp_objective_metric", "Цел:", getattr(api, "tsp_objective_metric", "time"),
                        tooltip="time = най-кратко време, distance = най-къси километри. POST metric/objective има приоритет."); r += 1
        self._add_field(tsp_optimization, r, "api.tsp_default_service_time_minutes", "Обслужване (мин):", getattr(api, "tsp_default_service_time_minutes", 8), "int",
                        tooltip="Default service_time_minutes за TSP. Ако POST подаде service_time_minutes, POST стойността има приоритет."); r += 1
        self._add_field(tsp_optimization, r, "api.tsp_use_time_windows", "Отчитай работно време:", getattr(api, "tsp_use_time_windows", True), "bool",
                        tooltip="Ако е включено, TSP предпочита ред, който намалява чакането и закъсненията спрямо работното време."); r += 1
        self._add_field(tsp_optimization, r, "api.tsp_time_window_wait_weight", "Тежест чакане:", getattr(api, "tsp_time_window_wait_weight", 1.0), "float",
                        tooltip="Колко силно чакането влияе на TSP подреждането. 0 = почти не го интересува чакането."); r += 1
        self._add_field(tsp_optimization, r, "api.tsp_time_window_late_weight", "Тежест закъснение:", getattr(api, "tsp_time_window_late_weight", 20.0), "float",
                        tooltip="Колко силно закъснението след работно време влияе на TSP подреждането. По-високо = по-строго."); r += 1
        self._add_field(tsp_optimization, r, "api.tsp_enable_two_opt", "2-opt подобрение:", getattr(api, "tsp_enable_two_opt", True), "bool",
                        tooltip="Прави допълнително локално подобрение след първоначалното greedy подреждане."); r += 1
        self._add_field(tsp_optimization, r, "api.tsp_two_opt_max_passes", "2-opt обходи:", getattr(api, "tsp_two_opt_max_passes", 30), "int",
                        tooltip="Повече обходи могат леко да подобрят реда, но забавят TSP при много клиенти."); r += 1
        self._add_field(tsp_optimization, r, "api.tsp_worker_timeout_seconds", "Worker timeout (сек):", getattr(api, "tsp_worker_timeout_seconds", 30), "int",
                        tooltip="Максимално време за отделния TSP процес, когато основният CVRP solver вече работи."); r += 1

        r = self._add_tsp_truck_profiles_editor(f, r, api)

        tsp_html, r = self._add_group(
            f,
            "TSP HTML и upload",
            "Тези настройки важат само за /tsp картите и не променят нормалните CVRP route карти.",
        )
        self._add_field(tsp_html, r, "api.tsp_response_format", "Отговор от /tsp:", getattr(api, "tsp_response_format", "json"), "combo",
                        options=["json", "html"],
                        tooltip="json = връща реда и ETA като JSON. html = връща самия HTML на картата като response body. Това не управлява локалното записване на файл."); r += 1
        self._add_field(tsp_html, r, "api.tsp_generate_html_map", "Локална HTML карта:", getattr(api, "tsp_generate_html_map", True), "bool",
                        tooltip="Само за /tsp. Управлява дали да се записва HTML файл на диска. Не променя дали HTTP отговорът е JSON или HTML."); r += 1
        self._add_field(tsp_html, r, "api.tsp_upload_html_map", "Качвай HTML карта:", getattr(api, "tsp_upload_html_map", True), "bool",
                        tooltip="Само за /tsp. Качването използва Output -> Route maps upload URL/token. Работи когато локалната TSP HTML карта е включена."); r += 1

        tsp_report, r = self._add_group(
            f,
            "TSP дневен Excel отчет",
            "API сървърът записва всяка успешна /tsp заявка и може автоматично да генерира дневен Excel отчет в зададен час.",
        )
        self._add_field(tsp_report, r, "api.tsp_daily_report_enabled", "Автоматичен отчет:", getattr(api, "tsp_daily_report_enabled", False), "bool",
                        tooltip="Ако е включено, API сървърът генерира TSP Excel отчет веднъж дневно в зададения час."); r += 1
        self._add_field(tsp_report, r, "api.tsp_daily_report_time", "Час:", getattr(api, "tsp_daily_report_time", "18:00"),
                        tooltip="Формат HH:MM, например 18:00."); r += 1
        self._add_field(tsp_report, r, "api.tsp_daily_report_output_dir", "Папка за отчети:", getattr(api, "tsp_daily_report_output_dir", ""),
                        tooltip="Ако е празно, използва Output -> Excel директория."); r += 1
        self._add_field(tsp_report, r, "api.tsp_daily_report_history_file", "TSP дневник:", getattr(api, "tsp_daily_report_history_file", ""),
                        tooltip="JSONL файл с история на /tsp маршрутите. Ако е празно, използва logs/tsp_routes_history.jsonl."); r += 1
        self._add_field(tsp_report, r, "api.tsp_daily_report_include_details", "Лист с клиенти:", getattr(api, "tsp_daily_report_include_details", True), "bool",
                        tooltip="Добавя подробен лист с всички клиенти/стопове към Excel отчета."); r += 1

        tsp_fields, r = self._add_group(
            f,
            "TSP полета във входната заявка",
            "Тук настройваш как се казват полетата, които външната система изпраща към /tsp. Може да зададеш повече от едно име, разделени със запетая; първото намерено поле се използва.",
        )
        self._add_field(tsp_fields, r, "api.tsp_driver_id_field", "ID шофьор/бус:", getattr(api, "tsp_driver_id_field", "driver_id"),
                        tooltip="Пример: driver_id или DriverID."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_driver_name_field", "Име шофьор/бус:", getattr(api, "tsp_driver_name_field", "driver_name"),
                        tooltip="Optional. Ако липсва, картата използва ID-то."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_driver_location_field", "Стартова GPS точка:", getattr(api, "tsp_driver_location_field", "driver_location"),
                        tooltip="Пример: driver_location,current_location,gps."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_end_location_field", "Крайна GPS точка:", getattr(api, "tsp_end_location_field", "end_location"),
                        tooltip="Optional. Ако липсва, TSP маршрутът е отворен."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_customers_field", "Списък клиенти:", getattr(api, "tsp_customers_field", "customers"),
                        tooltip="Пример: customers,clients,orders,data."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_customer_id_field", "Клиент ID:", getattr(api, "tsp_customer_id_field", "id"),
                        tooltip="Пример: id,IdCust,customer_id."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_customer_name_field", "Име клиент:", getattr(api, "tsp_customer_name_field", "name"),
                        tooltip="Пример: name,CustName,client_name."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_customer_order_field", "Номер документ:", getattr(api, "tsp_customer_order_field", "document"),
                        tooltip="Пример: document,order,DocNo."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_customer_gps_field", "GPS координати:", getattr(api, "tsp_customer_gps_field", "gps"),
                        tooltip="Пример: gps,GPS,coordinates,location."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_customer_quantity_field", "Количество/стекове:", getattr(api, "tsp_customer_quantity_field", "quantity"),
                        tooltip="Пример: quantity,Volume,stacks."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_customer_turnover_field", "Оборот:", getattr(api, "tsp_customer_turnover_field", "turnover"),
                        tooltip="Пример: turnover,amount,oborot."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_customer_work_time_field", "Работно време:", getattr(api, "tsp_customer_work_time_field", "work_time"),
                        tooltip="Пример: work_time,WorkTime,TimeWindow."); r += 1
        self._add_field(tsp_fields, r, "api.tsp_customer_comment_field", "Коментар доставка:", getattr(api, "tsp_customer_comment_field", "comment"),
                        tooltip="Пример: comment,note,remark,DeliveryComment."); r += 1

        commands, r = self._add_group(
            f,
            "Експертен API справочник",
            "Подробен списък с aliases и settings полета за напреднали. За ежедневна работа използвай табовата API документация по-горе.",
        )
        r = self._add_readonly_text(
            commands,
            r,
            "Команди:",
            f"""
GET {health_path}
  За какво е:
    Проверка дали API сървърът работи и какви URL-и са активни.
  Body:
    Няма.
  Връща:
    status, listen_url, public_url, solve_url, trigger_url, run_status,
    commands и settings_schema.
  Пример:
    curl "{base_url}{health_path}"

GET {trigger_path}
  За какво е:
    Стартира програмата във фон, без JSON body. Данните се зареждат според input_source.
  Body:
    Няма.
  Query настройки:
    Може да подадеш кратки настройки в URL-а. Те важат само за този run.
  Връща:
    202 started, run_id и текущ run статус. Ако вече върви run: 409 already_running.
  Примери:
    curl "{base_url}{trigger_path}"
    curl "{base_url}{trigger_path}?solver=pyvrp&time_limit=180"
    curl "{base_url}{trigger_path}?osrm_url=http://localhost:5000&sklad=106,128"
    curl "{base_url}{trigger_path}?output.enable_excel_output=true&set_data.enable_set_data_upload=false"
    curl "{base_url}{trigger_path}?callback_url=https://example.com/cvrp-finished"

POST {trigger_path}
  За какво е:
    Стартира програмата с JSON body. Това е най-гъвкавият начин за run от външна система.
  Body:
    {{
      "settings": {{ ... }},
      "return_result": false,
      "callback_url": "https://..."
    }}
  Режим 1 - background:
    Ако няма return_result=true, API-то връща веднага 202 started.
  Режим 2 - директен JSON резултат:
    Ако има return_result=true, return_json=true, wait=true или sync=true,
    заявката чака оптимизацията и връща пълния JSON резултат.
  Режим 3 - известие след края:
    Ако има callback_url/notify_url/webhook_url, background run праща POST към този URL.
  Връща:
    202 started за background, 200 с JSON решение за return_result, 409 ако вече има активен run.
  Примери:
    curl -X POST "{base_url}{trigger_path}" -H "Content-Type: application/json" -d "{{\"settings\":{{\"solver_type\":\"pyvrp\",\"objective_metric\":\"time\"}}}}"
    curl -X POST "{base_url}{trigger_path}" -H "Content-Type: application/json" -d "{{\"return_result\":true,\"settings\":{{\"objective_metric\":\"time\",\"time_limit_seconds\":180}}}}"
    curl -X POST "{base_url}{trigger_path}" -H "Content-Type: application/json" -d "{{\"callback_url\":\"https://example.com/cvrp-finished\"}}"

GET/POST {saturday_trigger_path}
  За какво е:
    Стартира същия CVRP workflow за съботата от текущата седмица.
  Поведение:
    Използва request-local съботна дата, output.saturday_excel_bus_number_prefix
    и output.saturday_excel_bus_number_digits. Картите и общият Excel получават
    suffix _събота; normal prefix-ът в config.py не се променя.
    Ако center_bus_numbering_enabled=true, CENTER_BUS запазва серията от
    center_bus_numbering_start_id и тя има предимство пред съботния prefix.
  Връща:
    202 started за background run, 200 при синхронен POST, 409 при активен основен run.
  Пример:
    curl -X POST "{base_url}{saturday_trigger_path}"

POST {solve_path}
  За какво е:
    Външната система подава клиентите директно в заявката и получава JSON резултат.
  Body варианти:
    1. Директен JSON array: [{{...}}, {{...}}]
    2. Wrapper: {{"customers":[...], "settings":{{...}}}}
  Познати имена за списъка:
    customers, clients, orders, data, items, records.
  Връща:
    200 с пълния JSON резултат от решението.
  Пример:
    curl -X POST "{base_url}{solve_path}" -H "Content-Type: application/json" -d "{{\"customers\":[{{\"IdCust\":\"1\",\"GPS\":\"42.6977,23.3219\",\"Volume\":10}}]}}"

POST {base_url}{tsp_path}
  За какво е:
    Подрежда текущ маршрут за един шофьор, без да пуска CVRP solver.
    Стартира от текущата GPS позиция на шофьора и ако има end_location,
    оптимизира реда така, че маршрутът да завърши в тази крайна точка.
  Body:
    {{
      "driver_id": "1004501001",
      "driver_location": "42.6977,23.3219",
      "end_location": "42.7000,23.4000",
      "service_time_minutes": 8,
      "customers": [{{"id":"1","name":"Клиент","document":"0004384359","gps":"42.6629,23.37682","work_time":"08:00-13:00","turnover":120.5,"quantity":5,"comment":"Обади се 10 мин преди доставка"}}]
    }}
  Field names:
    driver_id, driver_location, document, gps, work_time, quantity, turnover and comment
    are configurable in the "TSP полета във входната заявка" section.
  Defaults:
    Ако service_time_minutes липсва, /tsp използва "TSP оптимизация -> Обслужване (мин)".
    Стойността е обща за всички stops; клиентско ServiceTimeMinutes не се прилага в /tsp.
    Работните прозорци са soft score/ETA: късен stop може да остане в резултата.
    Форматът на HTTP отговора се управлява от "TSP HTML и upload -> Отговор от /tsp".
    Локалното записване и качването на TSP HTML карта са отделни настройки.
  Връща:
    При json: driver_id, ред на доставка, ETA, общи км/минути, map_file и upload статус.
    При html: Content-Type text/html и самият HTML на картата като body, без задължително да се записва локален файл.
    Ако route_maps_upload_mode=effect_upload, картата се качва с pData2[]=driver_id.
  Пример:
    curl -X POST "{base_url}{tsp_path}" -H "Content-Type: application/json" -d "{{\"driver_id\":\"1004501001\",\"driver_location\":\"42.6977,23.3219\",\"end_location\":\"42.7000,23.4000\",\"service_time_minutes\":8,\"customers\":[{{\"id\":\"1\",\"document\":\"0004384359\",\"gps\":\"42.6629,23.37682\",\"quantity\":5}}]}}"

POST {solve_path}?cmd=run
  За какво е:
    Compatibility режим, ако външната система може да вика само solve endpoint-а.
  Body:
    Няма нужда от клиенти. Може да има settings/callback_url/return_result.
  Приети trigger стойности:
    cmd=run, cmd=start, cmd=trigger, cmd=solve_config, cmd=start_program
    Същото работи и с query имена command=... или action=...
  Примери:
    curl -X POST "{base_url}{solve_path}?cmd=run"
    curl -X POST "{base_url}{solve_path}" -H "Content-Type: application/json" -d "{{\"cmd\":\"run\",\"settings\":{{\"solver_type\":\"or_tools\",\"objective_metric\":\"distance\"}}}}"

Временни settings:
  Могат да се подават като вложени секции:
    {{"settings": {{"output": {{"excel_output_dir": "H:\\\\..."}}}}}}
  Или с точкова нотация:
    {{"settings": {{"output.excel_output_dir": "H:\\\\..."}}}}
  В query GET формат:
    {trigger_path}?output.excel_output_dir=H%3A%5COut&set_data.enable_set_data_upload=false
  Всички временни настройки важат само за конкретната заявка.

Auth, ако API ключът е попълнен:
  X-CVRP-API-Key: <ключ>
  или Authorization: Bearer <ключ>
            """,
            height=46,
        )
        r = self._add_readonly_text(
            commands,
            r,
            "Body примери:",
            f"""
POST {trigger_path} - run с временни настройки, без да подаваме клиенти:
{{
  "settings": {{
    "solver_type": "pyvrp",
    "objective_metric": "time",
    "time_limit_seconds": 180,
    "routing": {{"engine": "osrm"}},
    "osrm": {{"base_url": "http://localhost:5000", "chunk_size": 80, "timeout_seconds": 45}},
    "vehicle_counts": {{"internal_bus": 7, "vratza_bus": 3}},
    "output": {{
      "enable_excel_output": true,
      "excel_output_dir": "C:\\\\CVRP\\\\output",
      "routes_output_dir": "C:\\\\CVRP\\\\output\\\\Routes",
      "enable_csv_output": true,
      "csv_output_file": "C:\\\\CVRP\\\\output\\\\routes.csv",
      "route_maps_upload_mode": "effect_upload",
      "route_maps_upload_url": "https://example.com/upload-files.php",
      "route_maps_upload_token": "<UPLOAD_TOKEN>",
      "route_maps_upload_file_field": "files[]",
      "route_maps_upload_bus_id_field": "pData2[]"
    }},
    "set_data": {{
      "enable_set_data_upload": false,
      "set_data_url": "https://example.com/setData",
      "set_data_http_method": "GET",
      "set_data_done_flag": "1973",
      "set_data_id_skld": "106",
      "set_data_vratza_id_skld": "128",
      "set_data_timeout_seconds": 30
    }}
  }}
}}

POST {trigger_path} - run, който връща директно JSON резултата:
{{
  "return_result": true,
  "settings": {{
    "solver_type": "pyvrp",
    "objective_metric": "time",
    "time_limit_seconds": 180,
    "set_data": {{"enable_set_data_upload": false}}
  }}
}}

POST {trigger_path} - background run с известие след края:
{{
  "callback_url": "https://example.com/cvrp-finished",
  "settings": {{
    "set_data": {{"enable_set_data_upload": false}}
  }}
}}

Callback payload при успех:
{{"status":"completed","success":true,"run_id":"...","finished_at":"...","result":{{...}}}}

Callback payload при грешка:
{{"status":"failed","success":false,"run_id":"...","finished_at":"...","error":"..."}}

POST {solve_path} - клиенти + настройки в една заявка:
{{
  "settings": {{
    "solver_type": "or_tools",
    "objective_metric": "time",
    "output.map_provider": "google",
    "set_data.enable_set_data_upload": false
  }},
  "customers": [
    {{"IdCust": "1", "CustName": "Клиент", "GPS": "42.6977,23.3219", "Volume": 10, "WorkTime": "08:00-13:00\\n16:00-18:00", "ServiceTimeMinutes": 6, "Mandatory": true}}
  ]
}}

POST {base_url}{tsp_path} - текущ TSP маршрут за един шофьор:
{{
  "driver_id": "1004501001",
  "driver_location": "42.6977,23.3219",
  "end_location": "42.7000,23.4000",
  "service_time_minutes": 8,
  "customers": [
    {{"id": "1", "name": "Клиент 1", "document": "0004384359", "gps": "42.6629,23.37682", "work_time": "08:00-13:00", "turnover": 120.50, "quantity": 5, "comment": "Обади се 10 мин преди доставка"}},
    {{"id": "2", "name": "Клиент 2", "document": "0004385134", "gps": "42.66119,23.39272", "work_time": "16:00-18:00", "turnover": 80.00, "quantity": 3, "comment": ""}}
  ]
}}

TSP имената на полетата се настройват от GUI:
  API сървър -> TSP полета във входната заявка.
  Ако външната система праща GPS като Coord или коментар като Note,
  попълни съответното име там, без да променяш кода.

Формат за vehicles patch:
{{
  "settings": {{
    "depots": {{
      "main": [42.6957, 23.2316],
      "vratza": [43.2210, 23.5344],
      "north": [42.8000, 23.4000]
    }},
    "vehicles": [
      {{"config_id": "internal-main", "vehicle_type": "internal_bus", "count": 7, "capacity": 385, "name": "HELL", "max_time_hours": 8, "max_distance_km": 250, "max_customers_per_day": 45, "start_depot_name": "main", "reload_depot_name": "main", "reload_time_minutes": 30, "end_location": [42.7000, 23.4000]}},
      {{"vehicle_type": "vratza_bus", "count": 3, "start_depot_name": "vratza", "end_depot_name": "vratza"}}
    ],
    "center_zone": {{
      "mode": "circle",
      "center": [42.6977, 23.3219],
      "radius_km": 2.0,
      "internal_bus_penalty": 40000,
      "external_bus_penalty": 40000,
      "vratza_bus_penalty": 40000,
      "center_bus_outside_penalty": 0
    }},
    "center_zones": [
      {{
        "name": "Център 2",
        "mode": "circle",
        "center": [42.7093, 23.3137],
        "radius_km": 1.2,
        "priority_vehicle_types": ["center_bus"],
        "restricted_vehicle_types": ["internal_bus", "external_bus", "vratza_bus"],
        "discount_priority_vehicle": 0.9,
        "priority_vehicle_outside_penalty": 0,
        "vehicle_penalties": {{"internal_bus": 40000, "external_bus": 40000, "vratza_bus": 40000}},
        "enabled": true,
        "show_on_map": true
      }}
    ],
    "traffic_zones": [
      {{"name": "Center traffic", "center": [42.6977, 23.3219], "radius_km": 3.0, "multiplier": 1.3, "enabled": true, "show_on_map": false}}
    ]
  }}
}}
            """,
            height=34,
        )
        for title, body, height in self._api_settings_reference_blocks():
            r = self._add_readonly_text(commands, r, title, body, height=height)

    def _add_set_data_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" setData ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        set_data = getattr(self.cfg, "set_data", None)
        r = 0

        connection, gr = self._add_grid_group(
            f,
            r,
            "Връщане към Bizant",
            "Когато е включено, след успешно решение програмата изпраща маршрутите обратно през setData.",
        ); r += 1
        self._add_field(connection, gr, "set_data.enable_set_data_upload", "Изпращай setData:", getattr(set_data, "enable_set_data_upload", False), "bool"); gr += 1
        self._add_field(connection, gr, "set_data.set_data_url", "URL:", getattr(set_data, "set_data_url", "")); gr += 1
        self._add_field(connection, gr, "set_data.set_data_http_method", "Метод:", getattr(set_data, "set_data_http_method", "GET"),
                         "combo", ["GET", "POST"]); gr += 1
        self._add_field(connection, gr, "set_data.set_data_command", "cmd:", getattr(set_data, "set_data_command", "setData")); gr += 1
        self._add_field(connection, gr, "set_data.set_data_done_flag", "DoneFlag:", getattr(set_data, "set_data_done_flag", "1973")); gr += 1
        self._add_field(connection, gr, "set_data.set_data_timeout_seconds", "Таймаут (сек):", getattr(set_data, "set_data_timeout_seconds", 30)); gr += 1

        warehouses, gr = self._add_grid_group(
            f,
            r,
            "Складове",
            "IdSkld стойностите, които се пращат към Bizant според депото на маршрута.",
        ); r += 1
        self._add_field(warehouses, gr, "set_data.set_data_id_skld", "Основно депо:", getattr(set_data, "set_data_id_skld", "128")); gr += 1
        self._add_field(warehouses, gr, "set_data.set_data_vratza_id_skld", "Враца:", getattr(set_data, "set_data_vratza_id_skld", "106")); gr += 1
        self._add_field(
            warehouses,
            gr,
            "set_data.set_data_depot_id_skld_map",
            "Корекции по депо:",
            getattr(set_data, "set_data_depot_id_skld_map", "Главно депо=128;Враца=106"),
            tooltip="Формат: Главно депо=128;Враца=106. Имената са същите като депата в таб Локации.",
        ); gr += 1

        templates, gr = self._add_grid_group(
            f,
            r,
            "Шаблони за обслужени клиенти",
            "Тук се описва какво да се праща като IdGrafik и Bukva за всеки обслужен клиент.",
        ); r += 1
        self._add_field(templates, gr, "set_data.set_data_id_grafik", "IdGrafik:", getattr(set_data, "set_data_id_grafik", "")); gr += 1
        self._add_field(
            templates,
            gr,
            "set_data.set_data_id_grafik_template",
            "IdGrafik шаблон:",
            getattr(set_data, "set_data_id_grafik_template", "{bus_number}"),
            tooltip="Default {bus_number} = номерът на буса от Excel, напр. 1004501001.",
        ); gr += 1
        self._add_field(
            templates,
            gr,
            "set_data.set_data_bukva_template",
            "Bukva шаблон:",
            getattr(set_data, "set_data_bukva_template", "БХ{route_number}-{stop_number}"),
            tooltip="Позволени: {bus_number}, {route_number}, {stop_number}, {vehicle_type}, {vehicle_name}, {customer_id}, {customer_document}, {id_skld}, {id_grafik}, {done_flag}.",
        ); gr += 1

        unserved, gr = self._add_grid_group(
            f,
            r,
            "Необслужени клиенти",
            "Отделни правила за клиенти, които са оставени за склад или не са обслужени от solver-а.",
        ); r += 1
        self._add_field(unserved, gr, "set_data.enable_unserved_set_data_upload", "Изпращай необслужени:", getattr(set_data, "enable_unserved_set_data_upload", True), "bool",
                        tooltip="IdSkld се взима от входното поле IdSkld на клиента."); gr += 1
        self._add_field(
            unserved,
            gr,
            "set_data.set_data_unserved_done_flag",
            "DoneFlag:",
            getattr(set_data, "set_data_unserved_done_flag", ""),
            tooltip="Само за необслужени клиенти. Празно поле = използва общия DoneFlag.",
        ); gr += 1
        self._add_field(unserved, gr, "set_data.set_data_unserved_id_grafik", "IdGrafik:", getattr(set_data, "set_data_unserved_id_grafik", "")); gr += 1
        self._add_field(
            unserved,
            gr,
            "set_data.set_data_unserved_id_grafik_template",
            "IdGrafik шаблон:",
            getattr(set_data, "set_data_unserved_id_grafik_template", "{id_grafik}"),
            tooltip="Позволени: {id_grafik}, {customer_id}, {customer_document}, {id_plas_doc}, {id_skld}, {stop_number}.",
        ); gr += 1
        self._add_field(
            unserved,
            gr,
            "set_data.set_data_unserved_bukva_template",
            "Bukva шаблон:",
            getattr(set_data, "set_data_unserved_bukva_template", "HOF-{id_plas_doc}"),
            tooltip="Позволени: {id_grafik}, {customer_id}, {customer_document}, {id_plas_doc}, {id_skld}, {stop_number}.",
        ); gr += 1

        grouping, gr = self._add_grid_group(
            f,
            r,
            "Групиране",
            "По желание след успешни setData заявки се праща makeGroup по склад.",
        ); r += 1
        self._add_field(grouping, gr, "set_data.enable_make_group", "Изпращай makeGroup:", getattr(set_data, "enable_make_group", True), "bool"); gr += 1
        self._add_field(grouping, gr, "set_data.set_data_make_group_command", "makeGroup cmd:", getattr(set_data, "set_data_make_group_command", "makeGroup")); gr += 1

    TASK_PREFIX = "CVRP_Optimizer_Auto"

    def _add_scheduler_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 🕒 Автоматично стартиране ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        r = 0

        schedule, gr = self._add_grid_group(
            f,
            r,
            "График",
            "Създава Windows Task Scheduler задача, която стартира програмата в избраните дни.",
        ); r += 1

        ttk.Label(schedule, text="Име на задача:", style="Surface.TLabel", anchor="w", width=28).grid(row=gr, column=0, sticky="w", padx=(8, 12), pady=6)
        self._sched_task_name = tk.StringVar(value=f"{self.TASK_PREFIX}_1")
        task_entry = ttk.Entry(schedule, textvariable=self._sched_task_name, width=32)
        task_entry.grid(row=gr, column=1, sticky="w", padx=6, pady=6)
        self._bind_text_editing(task_entry)
        gr += 1

        ttk.Label(schedule, text="Час:", style="Surface.TLabel", anchor="w", width=28).grid(row=gr, column=0, sticky="w", padx=(8, 12), pady=6)
        self._sched_time = tk.StringVar(value="17:01")
        time_entry = ttk.Entry(schedule, textvariable=self._sched_time, width=8)
        time_entry.grid(row=gr, column=1, sticky="w", padx=6, pady=6)
        self._bind_text_editing(time_entry)
        ttk.Label(schedule, text="Формат ЧЧ:ММ, например 17:01", style="Hint.TLabel").grid(row=gr, column=2, sticky="w", padx=(10, 4), pady=6)
        gr += 1

        ttk.Label(schedule, text="Дни:", style="Surface.TLabel", anchor="nw", width=28).grid(row=gr, column=0, sticky="nw", padx=(8, 12), pady=6)
        days_frame = ttk.Frame(schedule, style="Surface.TFrame")
        days_frame.grid(row=gr, column=1, sticky="w", padx=6, pady=6)
        self._sched_days = {}
        day_names = [("ПН", "MON"), ("ВТ", "TUE"), ("СР", "WED"),
                     ("ЧТ", "THU"), ("ПТ", "FRI"), ("СБ", "SAT"), ("НД", "SUN")]
        for i, (bg, en) in enumerate(day_names):
            var = tk.BooleanVar(value=(i < 5))  # Mon-Fri by default
            cb = ttk.Checkbutton(days_frame, text=bg, variable=var)
            cb.pack(side="left", padx=3)
            self._sched_days[en] = var
        gr += 1

        actions, gr = self._add_grid_group(
            f,
            r,
            "Действия",
            "Създай, премахни или провери автоматичните задачи.",
        ); r += 1
        btn_frame = ttk.Frame(actions, style="Surface.TFrame")
        btn_frame.grid(row=gr, column=0, columnspan=3, sticky="w", padx=8, pady=6)
        ttk.Button(btn_frame, text="✅ Създай задача", command=self._create_scheduled_task).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="🗑️ Премахни задача", command=self._remove_scheduled_task).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Провери статус", command=self._check_task_status_async).pack(side="left", padx=4)

        status_box, gr = self._add_grid_group(
            f,
            r,
            "Статус",
            "Тук се показва какво има в Windows Task Scheduler.",
        ); r += 1
        self._sched_status = tk.Text(status_box, width=70, height=8, wrap="word", state="disabled",
                                      background="#f5f5f5")
        self._sched_status.grid(row=gr, column=0, columnspan=3, sticky="we", padx=8, pady=6)

        # Auto-check status on load
        self.root.after(300, self._check_task_status_async)

    def _get_program_command(self):
        """Връща команда за стартиране, подходяща за schtasks и локално изпълнение"""
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            batch_path = os.path.join(exe_dir, "start_cvrp.bat")
            if os.path.isfile(batch_path):
                return f'cmd /c ""{batch_path}""', exe_dir
            return f'"{sys.executable}"', exe_dir

        dist_dir = os.path.abspath(os.path.join(_base_dir, "..", "dist"))
        for executable_name in ("Bizant.exe", "CVRP_Optimizer.exe"):
            exe = os.path.join(dist_dir, executable_name)
            if os.path.isfile(exe):
                batch_path = os.path.join(os.path.dirname(exe), "start_cvrp.bat")
                if os.path.isfile(batch_path):
                    return f'cmd /c ""{batch_path}""', os.path.dirname(exe)
                return f'"{exe}"', os.path.dirname(exe)

        main_py = os.path.join(_base_dir, "main.py")
        return f'"{sys.executable}" "{main_py}"', _base_dir

    def _set_scheduler_status(self, text):
        self._sched_status.configure(state="normal")
        self._sched_status.delete("1.0", "end")
        self._sched_status.insert("1.0", text)
        self._sched_status.configure(state="disabled")

    def _get_scheduler_task_name(self):
        task_name = self._sched_task_name.get().strip()
        return task_name or f"{self.TASK_PREFIX}_1"

    def _list_scheduler_tasks(self):
        import csv
        import io
        import subprocess

        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "CSV"],
            capture_output=True, text=True, creationflags=0x08000000
        )
        if result.returncode != 0:
            return []

        tasks = []
        for row in csv.DictReader(io.StringIO(result.stdout)):
            task_name = row.get("TaskName", "")
            if self.TASK_PREFIX.lower() in task_name.lower():
                next_run = row.get("Next Run Time", "")
                status = row.get("Status", "")
                tasks.append((task_name, next_run, status))
        return tasks

    def _create_scheduled_task(self):
        import subprocess
        task_name = self._get_scheduler_task_name()
        time_str = self._sched_time.get().strip()
        if not time_str or ":" not in time_str:
            messagebox.showwarning("Грешка", "Въведете час във формат ЧЧ:ММ")
            return

        selected = [en for en, var in self._sched_days.items() if var.get()]
        if not selected:
            messagebox.showwarning("Грешка", "Изберете поне един ден.")
            return

        days_str = ",".join(selected)
        prog, workdir = self._get_program_command()

        cmd = [
            "schtasks", "/Create",
            "/TN", task_name,
            "/TR", prog,
            "/SC", "WEEKLY",
            "/D", days_str,
            "/ST", time_str,
            "/F",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000)
        if result.returncode == 0:
            self._set_scheduler_status(f"Задачата е създадена/обновена.\n"
                             f"Име: {task_name}\n"
                             f"Час: {time_str}\n"
                             f"Дни: {days_str}\n"
                             f"Команда: {prog}\n\n"
                             f"Важно: друга задача се презаписва само ако използва същото име.")
            messagebox.showinfo("Готово", f"Задачата '{task_name}' е създадена/обновена.")
        else:
            err = result.stderr.strip() or result.stdout.strip()
            self._set_scheduler_status(f"Грешка при създаване:\n{err}")
            messagebox.showerror("Грешка", f"Не може да се създаде задачата:\n{err}")

    def _remove_scheduled_task(self):
        import subprocess
        task_name = self._get_scheduler_task_name()
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True, text=True, creationflags=0x08000000
        )
        if result.returncode == 0:
            self._set_scheduler_status(f"Задачата '{task_name}' е премахната.")
            messagebox.showinfo("Готово", f"Задачата '{task_name}' е премахната.")
        else:
            err = result.stderr.strip() or result.stdout.strip()
            self._set_scheduler_status(f"Грешка при премахване:\n{err}")

    def _check_task_status_async(self):
        self._set_scheduler_status("Проверявам Windows задачите...")
        task_name = self._get_scheduler_task_name()

        def worker():
            import subprocess

            result = subprocess.run(
                ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST", "/V"],
                capture_output=True,
                text=True,
                creationflags=0x08000000,
            )
            task_lines = [
                f"- {name} | Следващо: {next_run} | Статус: {status}"
                for name, next_run, status in self._list_scheduler_tasks()
            ]
            all_tasks_text = "\n\nВсички CVRP задачи:\n" + (
                "\n".join(task_lines) if task_lines else "Няма намерени CVRP задачи."
            )
            if result.returncode == 0:
                message = f"Избраната задача съществува:\n{result.stdout.strip()}{all_tasks_text}"
            else:
                message = f"Избраната задача '{task_name}' не съществува.{all_tasks_text}"
            self.root.after(0, lambda: self._set_scheduler_status(message))

        threading.Thread(target=worker, name="CVRPSchedulerStatus", daemon=True).start()

    def _check_task_status(self):
        """Backward-compatible entry point used by older callers."""
        self._check_task_status_async()

    def _check_task_status_sync_legacy(self):
        import subprocess
        task_name = self._get_scheduler_task_name()
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST", "/V"],
            capture_output=True, text=True, creationflags=0x08000000
        )
        task_lines = [f"- {name} | Следващо: {next_run} | Статус: {status}"
                      for name, next_run, status in self._list_scheduler_tasks()]
        all_tasks_text = "\n\nВсички CVRP задачи:\n" + ("\n".join(task_lines) if task_lines else "Няма намерени CVRP задачи.")
        if result.returncode == 0:
            self._set_scheduler_status(f"Избраната задача съществува:\n{result.stdout.strip()}{all_tasks_text}")
        else:
            self._set_scheduler_status(f"Избраната задача '{task_name}' не съществува.{all_tasks_text}")

    # ── Save logic ───────────────────────────────────────────

    def _collect_values(self) -> dict:
        """Събира стойности от всички уиджети"""
        values = {}
        for key, widget in self.widgets.items():
            if isinstance(widget, tk.Text):
                values[key] = widget.get("1.0", "end-1c")
            else:
                values[key] = widget.get()
        solver_display = values.get("cvrp.solver_type")
        if solver_display in self.SOLVER_VALUES_BY_LABEL:
            values["cvrp.solver_type"] = self.SOLVER_VALUES_BY_LABEL[solver_display]
        return values

    def _apply_to_config_file(self, values: dict):
        """Записва промените директно в config.py чрез текстова замяна"""
        config_path = os.path.join(_base_dir, "config.py")
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        original = content

        # ─── Input fields ───
        field_map = {
            "input.input_source": ("input_source", "str"),
            "input.excel_file_path": ("excel_file_path", "path"),
            "input.json_url": ("json_url", "str"),
            "input.json_http_method": ("json_http_method", "str"),
            "input.json_command": ("json_command", "str"),
            "input.json_date_field": ("json_date_field", "str"),
            "input.json_sklad": ("json_sklad", "str"),
            "input.json_done_flag": ("json_done_flag", "str"),
            "input.json_extra_query": ("json_extra_query", "str"),
            "input.json_override_date": ("json_override_date", "str"),
            "input.json_timeout_seconds": ("json_timeout_seconds", "int"),
            "input.json_gps_field": ("json_gps_field", "str"),
            "input.json_client_id_field": ("json_client_id_field", "str"),
            "input.json_client_name_field": ("json_client_name_field", "str"),
            "input.json_volume_field": ("json_volume_field", "str"),
            "input.json_document_field": ("json_document_field", "str"),
            "input.json_plas_doc_field": ("json_plas_doc_field", "str"),
            "input.json_id_skld_field": ("json_id_skld_field", "str"),
            "input.json_time_window_field": ("json_time_window_field", "str"),
            "input.json_delivery_comment_field": ("json_delivery_comment_field", "str"),
            "input.json_service_time_field": ("json_service_time_field", "str"),
            "input.json_mandatory_field": ("json_mandatory_field", "str"),
            "input.gps_column": ("gps_column", "str"),
            "input.client_id_column": ("client_id_column", "str"),
            "input.client_name_column": ("client_name_column", "str"),
            "input.volume_column": ("volume_column", "str"),
            "input.document_column": ("document_column", "str"),
            "input.time_window_column": ("time_window_column", "str"),
            "input.delivery_comment_column": ("delivery_comment_column", "str"),
            "input.mandatory_column": ("mandatory_column", "str"),
            "input.enable_customer_document_grouping": ("enable_customer_document_grouping", "bool"),
            # Routing
            "routing.engine": ("engine", "routing_engine"),
            "routing.enable_time_dependent": ("enable_time_dependent", "bool"),
            "routing.departure_time": ("departure_time", "str"),
            "routing.enable_curbside_approach": ("enable_curbside_approach", "bool"),
            "routing.valhalla_preferred_side": ("valhalla_preferred_side", "str"),
            # Warehouse
            "warehouse.enable_warehouse": ("enable_warehouse", "bool"),
            "warehouse.sort_by_volume": ("sort_by_volume", "bool"),
            "warehouse.sort_by_distance": ("sort_by_distance", "bool"),
            "warehouse.check_max_bus_capacity": ("check_max_bus_capacity", "bool"),
            "warehouse.max_bus_customer_volume": ("max_bus_customer_volume", "float"),
            "warehouse.capacity_toleranse": ("capacity_toleranse", "float"),
            # CVRP
            "cvrp.solver_type": ("solver_type", "str"),
            "cvrp.objective_metric": ("objective_metric", "str"),
            "cvrp.time_objective_include_waiting": ("time_objective_include_waiting", "bool"),
            "cvrp.enable_multiple_trips": ("enable_multiple_trips", "bool"),
            "cvrp.time_limit_seconds": ("time_limit_seconds", "int"),
            "cvrp.allow_customer_skipping": ("allow_customer_skipping", "bool"),
            "cvrp.distance_penalty_disjunction": ("distance_penalty_disjunction", "int"),
            "cvrp.enable_priority_dropping": ("enable_priority_dropping", "bool"),
            "cvrp.drop_volume_weight": ("drop_volume_weight", "float"),
            "cvrp.drop_closeness_weight": ("drop_closeness_weight", "float"),
            "cvrp.min_customer_drop_penalty": ("min_customer_drop_penalty", "int"),
            "cvrp.max_customer_drop_penalty": ("max_customer_drop_penalty", "int"),
            "cvrp.first_solution_strategy": ("first_solution_strategy", "str"),
            "cvrp.local_search_metaheuristic": ("local_search_metaheuristic", "str"),
            "cvrp.lns_time_limit_seconds": ("lns_time_limit_seconds", "float"),
            "cvrp.lns_num_nodes": ("lns_num_nodes", "int"),
            "cvrp.lns_num_arcs": ("lns_num_arcs", "int"),
            "cvrp.use_full_propagation": ("use_full_propagation", "bool"),
            "cvrp.search_lambda_coefficient": ("search_lambda_coefficient", "float"),
            "cvrp.log_search": ("log_search", "bool"),
            "cvrp.enable_start_time_tracking": ("enable_start_time_tracking", "bool"),
            "cvrp.global_start_time_minutes": ("global_start_time_minutes", "int"),
            "cvrp.enable_customer_time_windows": ("enable_customer_time_windows", "bool"),
            "cvrp.customer_time_window_default_start_minutes": ("customer_time_window_default_start_minutes", "int"),
            "cvrp.customer_time_window_default_end_minutes": ("customer_time_window_default_end_minutes", "int"),
            "cvrp.enable_parallel_solving": ("enable_parallel_solving", "bool"),
            "cvrp.num_workers": ("num_workers", "int"),
            "cvrp.pyvrp_seed_base": ("pyvrp_seed_base", "int"),
            "cvrp.pyvrp_seed": ("pyvrp_seed", "optional_int"),
            "cvrp.pyvrp_num_neighbours": ("pyvrp_num_neighbours", "int"),
            "cvrp.pyvrp_weight_wait_time": ("pyvrp_weight_wait_time", "float"),
            "cvrp.pyvrp_symmetric_proximity": ("pyvrp_symmetric_proximity", "bool"),
            "cvrp.pyvrp_ils_no_improvement": ("pyvrp_ils_no_improvement", "int"),
            "cvrp.pyvrp_ils_history_length": ("pyvrp_ils_history_length", "int"),
            "cvrp.pyvrp_exhaustive_on_best": ("pyvrp_exhaustive_on_best", "bool"),
            "cvrp.pyvrp_use_extended_operators": ("pyvrp_use_extended_operators", "bool"),
            "cvrp.pyvrp_min_perturbations": ("pyvrp_min_perturbations", "int"),
            "cvrp.pyvrp_max_perturbations": ("pyvrp_max_perturbations", "int"),
            "cvrp.pyvrp_display_progress": ("pyvrp_display_progress", "bool"),
            "cvrp.pyvrp_display_interval_seconds": ("pyvrp_display_interval_seconds", "float"),
            "cvrp.pyvrp_use_library_penalty_defaults": ("pyvrp_use_library_penalty_defaults", "bool"),
            "cvrp.pyvrp_penalty_solutions_between_updates": ("pyvrp_penalty_solutions_between_updates", "int"),
            "cvrp.pyvrp_penalty_increase": ("pyvrp_penalty_increase", "float"),
            "cvrp.pyvrp_penalty_decrease": ("pyvrp_penalty_decrease", "float"),
            "cvrp.pyvrp_penalty_target_feasible": ("pyvrp_penalty_target_feasible", "float"),
            "cvrp.pyvrp_penalty_feas_tolerance": ("pyvrp_penalty_feas_tolerance", "float"),
            "cvrp.pyvrp_penalty_min": ("pyvrp_penalty_min", "float"),
            "cvrp.pyvrp_penalty_max": ("pyvrp_penalty_max", "float"),
            "cvrp.pyvrp_next_worker_path": ("pyvrp_next_worker_path", "str"),
            "cvrp.pyvrp_next_worker_timeout_seconds": ("pyvrp_next_worker_timeout_seconds", "int"),
            "cvrp.pyvrp_next_fallback_to_stable": ("pyvrp_next_fallback_to_stable", "bool"),
            "cvrp.vroom_worker_path": ("vroom_worker_path", "str"),
            "cvrp.vroom_worker_timeout_seconds": ("vroom_worker_timeout_seconds", "int"),
            "cvrp.vroom_threads": ("vroom_threads", "int"),
            "cvrp.vroom_exploration_level": ("vroom_exploration_level", "int"),
            "cvrp.vrp_worker_path": ("vrp_worker_path", "str"),
            "cvrp.vrp_worker_timeout_seconds": ("vrp_worker_timeout_seconds", "int"),
            "cvrp.vrp_threads": ("vrp_threads", "int"),
            "cvrp.vrp_max_generations": ("vrp_max_generations", "int"),
            "cvrp.vrp_log_progress": ("vrp_log_progress", "bool"),
            # Locations
            "locations.center_zone_mode": ("center_zone_mode", "str"),
            "locations.center_zone_radius_km": ("center_zone_radius_km", "float"),
            "locations.show_center_zone_on_map": ("show_center_zone_on_map", "bool"),
            "locations.enable_center_zone_priority": ("enable_center_zone_priority", "bool"),
            "locations.enable_center_zone_restrictions": ("enable_center_zone_restrictions", "bool"),
            "locations.discount_center_bus": ("discount_center_bus", "float"),
            "locations.center_bus_outside_center_penalty": ("center_bus_outside_center_penalty", "float"),
            "locations.internal_bus_center_penalty": ("internal_bus_center_penalty", "float"),
            "locations.external_bus_center_penalty": ("external_bus_center_penalty", "float"),
            "locations.special_bus_center_penalty": ("special_bus_center_penalty", "float"),
            "locations.vratza_bus_center_penalty": ("vratza_bus_center_penalty", "float"),
            "locations.enable_city_traffic_adjustment": ("enable_city_traffic_adjustment", "bool"),
            "locations.show_city_traffic_zone_on_map": ("show_city_traffic_zone_on_map", "bool"),
            "locations.city_traffic_radius_km": ("city_traffic_radius_km", "float"),
            "locations.city_traffic_duration_multiplier": ("city_traffic_duration_multiplier", "float"),
            # Output
            "output.enable_interactive_map": ("enable_interactive_map", "bool"),
            "output.map_output_file": ("map_output_file", "path"),
            "output.routes_output_dir": ("routes_output_dir", "path"),
            "output.route_maps_upload_mode": ("route_maps_upload_mode", "str"),
            "output.route_maps_upload_url": ("route_maps_upload_url", "str"),
            "output.route_maps_upload_token_field": ("route_maps_upload_token_field", "str"),
            "output.route_maps_upload_token": ("route_maps_upload_token", "str"),
            "output.route_maps_upload_file_field": ("route_maps_upload_file_field", "str"),
            "output.route_maps_upload_bus_id_field": ("route_maps_upload_bus_id_field", "str"),
            "output.route_maps_upload_timeout_seconds": ("route_maps_upload_timeout_seconds", "int"),
            "output.map_provider": ("map_provider", "str"),
            "output.folium_tiles": ("folium_tiles", "str"),
            "output.google_maps_api_key": ("google_maps_api_key", "str"),
            "output.enable_excel_output": ("enable_excel_output", "bool"),
            "output.excel_output_dir": ("excel_output_dir", "path"),
            "output.routes_excel_file": ("routes_excel_file", "str"),
            "output.warehouse_excel_file": ("warehouse_excel_file", "str"),
            "output.efficiency_excel_file": ("efficiency_excel_file", "str"),
            "output.excel_bus_number_prefix": ("excel_bus_number_prefix", "str"),
            "output.excel_bus_number_digits": ("excel_bus_number_digits", "int"),
            "output.saturday_excel_bus_number_prefix": ("saturday_excel_bus_number_prefix", "str"),
            "output.saturday_excel_bus_number_digits": ("saturday_excel_bus_number_digits", "int"),
            "output.center_bus_numbering_enabled": ("center_bus_numbering_enabled", "bool"),
            "output.center_bus_numbering_start_id": ("center_bus_numbering_start_id", "str"),
            "output.enable_csv_output": ("enable_csv_output", "bool"),
            "output.csv_output_file": ("csv_output_file", "path"),
            "output.enable_charts": ("enable_charts", "bool"),
            "output.charts_output_dir": ("charts_output_dir", "path"),
            # API server
            "api.api_host": ("api_host", "str"),
            "api.api_port": ("api_port", "int"),
            "api.api_public_url": ("api_public_url", "str"),
            "api.api_key": ("api_key", "str"),
            "api.api_endpoint": ("api_endpoint", "str"),
            "api.trigger_endpoint": ("trigger_endpoint", "str"),
            "api.saturday_trigger_endpoint": ("saturday_trigger_endpoint", "str"),
            "api.tsp_endpoint": ("tsp_endpoint", "str"),
            "api.tsp_report_endpoint": ("tsp_report_endpoint", "str"),
            "api.shutdown_endpoint": ("shutdown_endpoint", "str"),
            "api.health_endpoint": ("health_endpoint", "str"),
            "api.web_gui_enabled": ("web_gui_enabled", "bool"),
            "api.web_gui_endpoint": ("web_gui_endpoint", "str"),
            "api.web_gui_title": ("web_gui_title", "str"),
            "api.web_gui_public_host": ("web_gui_public_host", "str"),
            "api.web_gui_public_url": ("web_gui_public_url", "str"),
            "api.web_gui_trusted_proxy_ips": ("web_gui_trusted_proxy_ips", "str"),
            "api.tsp_default_service_time_minutes": ("tsp_default_service_time_minutes", "int"),
            "api.tsp_objective_metric": ("tsp_objective_metric", "str"),
            "api.tsp_use_time_windows": ("tsp_use_time_windows", "bool"),
            "api.tsp_time_window_wait_weight": ("tsp_time_window_wait_weight", "float"),
            "api.tsp_time_window_late_weight": ("tsp_time_window_late_weight", "float"),
            "api.tsp_enable_two_opt": ("tsp_enable_two_opt", "bool"),
            "api.tsp_two_opt_max_passes": ("tsp_two_opt_max_passes", "int"),
            "api.tsp_response_format": ("tsp_response_format", "str"),
            "api.tsp_generate_html_map": ("tsp_generate_html_map", "bool"),
            "api.tsp_upload_html_map": ("tsp_upload_html_map", "bool"),
            "api.tsp_worker_timeout_seconds": ("tsp_worker_timeout_seconds", "int"),
            "api.tsp_valhalla_truck_profiles": ("tsp_valhalla_truck_profiles", "str"),
            "api.tsp_valhalla_truck_driver_ids": ("tsp_valhalla_truck_driver_ids", "str"),
            "api.tsp_valhalla_truck_height": ("tsp_valhalla_truck_height", "float"),
            "api.tsp_valhalla_truck_width": ("tsp_valhalla_truck_width", "float"),
            "api.tsp_valhalla_truck_length": ("tsp_valhalla_truck_length", "float"),
            "api.tsp_valhalla_truck_weight": ("tsp_valhalla_truck_weight", "float"),
            "api.tsp_valhalla_truck_axle_load": ("tsp_valhalla_truck_axle_load", "float"),
            "api.tsp_valhalla_truck_axle_count": ("tsp_valhalla_truck_axle_count", "int"),
            "api.tsp_valhalla_truck_hazmat": ("tsp_valhalla_truck_hazmat", "bool"),
            "api.tsp_valhalla_truck_hgv_no_access_penalty": ("tsp_valhalla_truck_hgv_no_access_penalty", "int"),
            "api.tsp_daily_report_enabled": ("tsp_daily_report_enabled", "bool"),
            "api.tsp_daily_report_time": ("tsp_daily_report_time", "str"),
            "api.tsp_daily_report_output_dir": ("tsp_daily_report_output_dir", "str"),
            "api.tsp_daily_report_history_file": ("tsp_daily_report_history_file", "str"),
            "api.tsp_daily_report_include_details": ("tsp_daily_report_include_details", "bool"),
            "api.tsp_driver_id_field": ("tsp_driver_id_field", "str"),
            "api.tsp_driver_name_field": ("tsp_driver_name_field", "str"),
            "api.tsp_driver_location_field": ("tsp_driver_location_field", "str"),
            "api.tsp_end_location_field": ("tsp_end_location_field", "str"),
            "api.tsp_customers_field": ("tsp_customers_field", "str"),
            "api.tsp_customer_id_field": ("tsp_customer_id_field", "str"),
            "api.tsp_customer_name_field": ("tsp_customer_name_field", "str"),
            "api.tsp_customer_order_field": ("tsp_customer_order_field", "str"),
            "api.tsp_customer_gps_field": ("tsp_customer_gps_field", "str"),
            "api.tsp_customer_quantity_field": ("tsp_customer_quantity_field", "str"),
            "api.tsp_customer_turnover_field": ("tsp_customer_turnover_field", "str"),
            "api.tsp_customer_work_time_field": ("tsp_customer_work_time_field", "str"),
            "api.tsp_customer_comment_field": ("tsp_customer_comment_field", "str"),
            # Bizant setData
            "set_data.enable_set_data_upload": ("enable_set_data_upload", "bool"),
            "set_data.set_data_url": ("set_data_url", "str"),
            "set_data.set_data_http_method": ("set_data_http_method", "str"),
            "set_data.set_data_command": ("set_data_command", "str"),
            "set_data.set_data_done_flag": ("set_data_done_flag", "str"),
            "set_data.set_data_id_skld": ("set_data_id_skld", "str"),
            "set_data.set_data_vratza_id_skld": ("set_data_vratza_id_skld", "str"),
            "set_data.set_data_depot_id_skld_map": ("set_data_depot_id_skld_map", "str"),
            "set_data.set_data_id_grafik": ("set_data_id_grafik", "str"),
            "set_data.set_data_id_grafik_template": ("set_data_id_grafik_template", "str"),
            "set_data.set_data_bukva_template": ("set_data_bukva_template", "str"),
            "set_data.enable_unserved_set_data_upload": ("enable_unserved_set_data_upload", "bool"),
            "set_data.set_data_unserved_done_flag": ("set_data_unserved_done_flag", "str"),
            "set_data.set_data_unserved_id_grafik": ("set_data_unserved_id_grafik", "str"),
            "set_data.set_data_unserved_id_grafik_template": ("set_data_unserved_id_grafik_template", "str"),
            "set_data.set_data_unserved_bukva_template": ("set_data_unserved_bukva_template", "str"),
            "set_data.enable_make_group": ("enable_make_group", "bool"),
            "set_data.set_data_make_group_command": ("set_data_make_group_command", "str"),
            "set_data.set_data_timeout_seconds": ("set_data_timeout_seconds", "int"),
        }

        import re

        for gui_key, (field_name, ftype) in field_map.items():
            if gui_key not in values:
                continue
            raw_val = values[gui_key]
            content = self._replace_field_value(content, field_name, raw_val, ftype)

        scoped_field_map = {
            "osrm.base_url": ("OSRMConfig", "base_url", "str"),
            "osrm.profile": ("OSRMConfig", "profile", "str"),
            "osrm.chunk_size": ("OSRMConfig", "chunk_size", "int"),
            "osrm.timeout_seconds": ("OSRMConfig", "timeout_seconds", "int"),
            "osrm.retry_attempts": ("OSRMConfig", "retry_attempts", "int"),
            "osrm.average_speed_kmh": ("OSRMConfig", "average_speed_kmh", "float"),
            "osrm.fallback_to_public": ("OSRMConfig", "fallback_to_public", "bool"),
            "osrm.max_locations_for_osrm": ("OSRMConfig", "max_locations_for_osrm", "int"),
            "osrm.enable_smart_chunking": ("OSRMConfig", "enable_smart_chunking", "bool"),
            "valhalla.base_url": ("ValhallaConfig", "base_url", "str"),
            "valhalla.costing": ("ValhallaConfig", "costing", "str"),
            "valhalla.timeout_seconds": ("ValhallaConfig", "timeout_seconds", "int"),
        }
        for gui_key, (class_name, field_name, ftype) in scoped_field_map.items():
            if gui_key not in values:
                continue
            content = self._replace_scoped_field_value(content, class_name, field_name, values[gui_key], ftype)

        # ─── List fields (parallel strategies) ───
        list_fields = {
            "cvrp.parallel_first_solution_strategies": "parallel_first_solution_strategies",
            "cvrp.parallel_local_search_metaheuristics": "parallel_local_search_metaheuristics",
        }
        for gui_key, field_name in list_fields.items():
            if gui_key in values:
                raw = values[gui_key]
                content = self._replace_list_field_value(content, field_name, raw)

        # ─── Location tuples ───
        for loc_key in ("depot_location", "center_location", "vratza_depot_location", "city_center_coords"):
            gui_key = f"locations.{loc_key}"
            if gui_key in values:
                raw = values[gui_key]
                content = self._replace_tuple_value(content, loc_key, raw)

        if "locations.center_zone_polygon" in values:
            content = self._replace_polygon_value(
                content,
                "center_zone_polygon",
                values["locations.center_zone_polygon"],
            )

        if "locations.depot_locations" in values:
            content = self._replace_depot_locations_value(
                content,
                values["locations.depot_locations"],
            )

        if "locations.traffic_zones" in values:
            content = self._replace_traffic_zones_value(
                content,
                values["locations.traffic_zones"],
            )

        if "locations.center_zones" in values:
            content = self._replace_center_zones_value(
                content,
                values["locations.center_zones"],
            )

        # ─── Vehicle fields ───
        if self._should_rewrite_vehicle_list(values):
            content = self._replace_vehicles_block(content, values)
        elif self.cfg.vehicles:
            for i, v in enumerate(self.cfg.vehicles):
                prefix = f"vehicle.{i}"
                vtype = v.vehicle_type.value
                content = self._replace_vehicle_field(content, vtype, i, values, prefix)

        if content != original:
            compile(content, config_path, "exec")
            backup_dir = os.path.join(os.path.dirname(config_path), "config_backups")
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup_path = os.path.join(backup_dir, f"config_desktop_gui_{timestamp}.py")
            shutil.copy2(config_path, backup_path)

            temp_handle, temp_path = tempfile.mkstemp(
                prefix="config_gui_",
                suffix=".tmp",
                dir=os.path.dirname(config_path),
                text=True,
            )
            try:
                with os.fdopen(temp_handle, "w", encoding="utf-8", newline="") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temp_path, config_path)
            except Exception:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise
            return True
        return False

    def _replace_field_value(self, content, field_name, raw_val, ftype):
        """Замества стойността на поле в config.py"""
        import re

        if ftype == "bool":
            new_val = "True" if raw_val else "False"
            pattern = rf'({field_name}\s*(?::\s*bool\s*)?=\s*)(?:True|False)'
            content = re.sub(pattern, rf'\g<1>{new_val}', content)
        elif ftype == "int":
            try:
                val = int(raw_val)
                # Match the complete Python integer literal, including digit
                # separators.  Matching only up to the first underscore left
                # suffixes such as ``_000_000`` behind and multiplied values
                # on every GUI save.
                pattern = rf'({field_name}\s*(?::\s*int\s*)?=\s*)-?\d[\d_]*'
                content = re.sub(pattern, rf'\g<1>{val}', content)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Полето {field_name} трябва да бъде цяло число.") from exc
        elif ftype == "optional_int":
            raw_text = str(raw_val).strip()
            pattern = rf'({field_name}\s*(?::\s*Optional\[int\]\s*)?=\s*)(?:None|-?\d[\d_]*)'
            if raw_text == "" or raw_text.lower() == "none":
                content = re.sub(pattern, rf'\g<1>None', content)
            else:
                try:
                    val = int(raw_text)
                    content = re.sub(pattern, rf'\g<1>{val}', content)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"Полето {field_name} трябва да бъде цяло число или празно.") from exc
        elif ftype == "float":
            try:
                val = float(raw_val)
                pattern = rf'({field_name}\s*(?::\s*float\s*)?=\s*)-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
                content = re.sub(pattern, rf'\g<1>{val}', content)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Полето {field_name} трябва да бъде число.") from exc
        elif ftype in ("str", "path"):
            escaped = (
                str(raw_val)
                .replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\r\n", "\n")
                .replace("\r", "\n")
                .replace("\n", "\\n")
            )
            pattern = rf'({field_name}\s*(?::\s*str\s*)?=\s*(?:_abs_path\()?")[^"]*(")'
            content, replacements = re.subn(
                pattern,
                lambda match: f'{match.group(1)}{escaped}{match.group(2)}',
                content,
            )
            if replacements == 0 and field_name == "google_maps_api_key":
                env_pattern = r'(google_maps_api_key\s*:\s*str\s*=\s*os\.environ\.get\("GOOGLE_MAPS_API_KEY",\s*")[^"]*("\))'
                content = re.sub(
                    env_pattern,
                    lambda match: f'{match.group(1)}{escaped}{match.group(2)}',
                    content,
                    count=1,
                )
        elif ftype == "routing_engine":
            engine = str(raw_val).strip().upper()
            if engine in ("OSRM", "VALHALLA"):
                pattern = rf'({field_name}\s*:\s*RoutingEngine\s*=\s*)RoutingEngine\.[A-Z_]+'
                content = re.sub(pattern, rf'\g<1>RoutingEngine.{engine}', content)
            else:
                raise ValueError("Routing engine трябва да бъде OSRM или VALHALLA.")
        return content

    def _replace_scoped_field_value(self, content, class_name, field_name, raw_val, ftype):
        """Замества поле само в конкретен dataclass, за да не бърка еднакви имена като base_url."""
        import re

        class_pattern = rf'(class\s+{re.escape(class_name)}\s*:\s*.*?)(?=\n@dataclass|\nclass\s+|\Z)'

        def replace_in_class(match):
            class_block = match.group(1)
            return self._replace_field_value(class_block, field_name, raw_val, ftype)

        return re.sub(class_pattern, replace_in_class, content, count=1, flags=re.S)

    def _replace_tuple_value(self, content, field_name, raw_val):
        """Замества tuple стойност (lat, lon) в config.py"""
        import re
        try:
            parts = [float(x.strip()) for x in str(raw_val).split(",")]
        except ValueError as exc:
            raise ValueError(f"Полето {field_name} трябва да бъде във формат lat, lon.") from exc
        if len(parts) != 2 or not (-90 <= parts[0] <= 90 and -180 <= parts[1] <= 180):
            raise ValueError(f"Полето {field_name} съдържа невалидни GPS координати.")
        new_tuple = f"({parts[0]}, {parts[1]})"
        pattern = rf'({field_name}\s*(?::\s*Tuple\[float,\s*float\]\s*)?=\s*)\([^)]+\)'
        content = re.sub(pattern, rf'\g<1>{new_tuple}', content)
        return content

    def _replace_polygon_value(self, content, field_name, raw_val):
        import re

        points = self._parse_polygon_text(raw_val)
        if points:
            points_str = ",\n        ".join(f"({lat}, {lon})" for lat, lon in points)
            new_block = f"field(default_factory=lambda: [\n        {points_str}\n    ])"
        else:
            new_block = "field(default_factory=lambda: [])"

        pattern = (
            rf'({field_name}\s*:\s*List\[Tuple\[float,\s*float\]\]\s*=\s*)'
            rf'field\(default_factory=lambda:\s*\[.*?\]\)'
        )
        return re.sub(pattern, rf'\g<1>{new_block}', content, flags=re.S)

    def _replace_depot_locations_value(self, content, raw_val):
        import re

        depots = self._parse_depots_text(raw_val)
        if depots:
            items = ",\n        ".join(
                f'"{name}": ({coords[0]}, {coords[1]})'
                for name, coords in depots.items()
            )
            new_block = f"field(default_factory=lambda: {{\n        {items}\n    }})"
        else:
            new_block = "field(default_factory=lambda: {})"

        pattern = (
            r'(depot_locations\s*:\s*Dict\[str,\s*Tuple\[float,\s*float\]\]\s*=\s*)'
            r'field\(default_factory=lambda:\s*\{.*?\}\)'
        )
        return re.sub(pattern, rf'\g<1>{new_block}', content, flags=re.S)

    def _replace_traffic_zones_value(self, content, raw_val):
        import re

        zones = self._parse_traffic_zones_text(raw_val)
        if zones:
            items = ",\n        ".join(
                "\n        ".join([
                    "TrafficZoneConfig(",
                    f'    name="{zone.name.replace(chr(34), chr(92) + chr(34))}",',
                    f"    center_coords=({float(zone.center_coords[0])}, {float(zone.center_coords[1])}),",
                    f"    radius_km={float(zone.radius_km)},",
                    f"    duration_multiplier={float(zone.duration_multiplier)},",
                    f"    enabled={'True' if zone.enabled else 'False'},",
                    f"    show_on_map={'True' if getattr(zone, 'show_on_map', False) else 'False'},",
                    ")",
                ])
                for zone in zones
            )
            new_block = f"field(default_factory=lambda: [\n        {items}\n    ])"
        else:
            new_block = "field(default_factory=lambda: [])"

        pattern = (
            r'(traffic_zones\s*:\s*List\[TrafficZoneConfig\]\s*=\s*)'
            r'field\(default_factory=lambda:\s*\[.*?\]\)'
        )
        return re.sub(pattern, rf'\g<1>{new_block}', content, flags=re.S)

    def _replace_center_zones_value(self, content, raw_val):
        import re

        zones = self._parse_center_zones_text(raw_val, strict=True)
        if zones:
            zone_blocks = []
            for zone in zones:
                polygon = getattr(zone, "polygon", []) or []
                polygon_block = "[" + ", ".join(f"({float(lat)}, {float(lon)})" for lat, lon in polygon) + "]"
                center = getattr(zone, "center_coords", None)
                center_block = f"({float(center[0])}, {float(center[1])})" if center else "None"
                priority = "[" + ", ".join(
                    self._string_literal(item) for item in (zone.priority_vehicle_types or [])
                ) + "]"
                restricted = "[" + ", ".join(
                    self._string_literal(item) for item in (zone.restricted_vehicle_types or [])
                ) + "]"
                penalties = "{" + ", ".join(
                    f'{self._string_literal(bus)}: {float(value)}'
                    for bus, value in (zone.vehicle_penalties or {}).items()
                ) + "}"
                zone_blocks.append(
                    "\n        ".join([
                        "CenterZoneConfig(",
                        f"    name={self._string_literal(zone.name)},",
                        f"    mode={self._string_literal(zone.mode)},",
                        f"    center_coords={center_block},",
                        f"    radius_km={float(zone.radius_km)},",
                        f"    polygon={polygon_block},",
                        f"    enabled={'True' if zone.enabled else 'False'},",
                        f"    show_on_map={'True' if getattr(zone, 'show_on_map', True) else 'False'},",
                        f"    enable_priority={'True' if zone.enable_priority else 'False'},",
                        f"    enable_restrictions={'True' if zone.enable_restrictions else 'False'},",
                        f"    priority_vehicle_types={priority},",
                        f"    restricted_vehicle_types={restricted},",
                        f"    discount_priority_vehicle={float(zone.discount_priority_vehicle)},",
                        f"    priority_vehicle_outside_penalty={float(zone.priority_vehicle_outside_penalty)},",
                        f"    vehicle_penalties={penalties},",
                        ")",
                    ])
                )
            joined_blocks = ",\n        ".join(zone_blocks)
            new_block = f"field(default_factory=lambda: [\n        {joined_blocks}\n    ])"
        else:
            new_block = "field(default_factory=lambda: [])"

        pattern = (
            r'(?ms)^(    center_zones\s*:\s*List\[CenterZoneConfig\]\s*=\s*)'
            r'field\(default_factory=lambda:\s*(?:\[\]|\[\n.*?^    \])\)'
            r'(?:[^\n]*)?'
        )
        content, count = re.subn(pattern, rf'\g<1>{new_block}', content)
        if count:
            return content

        return content

    def _replace_list_field_value(self, content, field_name, raw_val):
        """Замества List[str] поле с field(default_factory=lambda: [...]) в config.py"""
        import re
        items = [line.strip() for line in raw_val.strip().splitlines() if line.strip()]
        if not items:
            return content
        items_str = ",\n        ".join(f'"{item}"' for item in items)
        new_block = f"field(default_factory=lambda: [\n        {items_str}\n    ])"
        pattern = rf'({field_name}\s*:\s*List\[str\]\s*=\s*)field\(default_factory=lambda:\s*\[.*?\]\)'
        content = re.sub(pattern, rf'\g<1>{new_block}', content, flags=re.DOTALL)
        return content

    def _format_optional_tuple_value(self, raw_val):
        raw_str = str(raw_val).strip()
        if raw_str == "" or raw_str.lower() == "none":
            return "None"

        try:
            parts = [float(part.strip()) for part in raw_str.split(",")]
            if len(parts) == 2:
                return f"({parts[0]}, {parts[1]})"
        except ValueError:
            return None
        return None

    def _depot_lookup_for_values(self, values):
        depots = dict(self._named_depots())
        extra_raw = values.get("locations.depot_locations", "")
        depots.update(self._parse_depots_text(extra_raw))
        return depots

    def _format_depot_choice_value(self, depot_name, values):
        name = str(depot_name).strip()
        if not name:
            name = "Главно депо"
        depots = self._depot_lookup_for_values(values)
        coords = depots.get(name)
        if not coords:
            return None
        return f"({float(coords[0])}, {float(coords[1])})"

    def _parse_int_value(self, raw_val, default=0):
        try:
            return int(float(str(raw_val).strip()))
        except (TypeError, ValueError):
            return default

    def _parse_optional_int_value(self, raw_val):
        raw_text = str(raw_val).strip()
        if raw_text == "" or raw_text.lower() == "none":
            return None
        try:
            return int(float(raw_text))
        except (TypeError, ValueError):
            return None

    def _parse_bool_value(self, raw_val):
        if isinstance(raw_val, bool):
            return raw_val
        return str(raw_val).strip().lower() in ("1", "true", "yes", "y", "on")

    def _depot_coords_from_choice(self, depot_name, values):
        name = str(depot_name).strip() or "Главно депо"
        depots = self._depot_lookup_for_values(values)
        coords = depots.get(name)
        if not coords:
            return self.cfg.locations.depot_location
        return (float(coords[0]), float(coords[1]))

    def _vehicle_config_from_values(self, values, prefix):
        vehicle_type_value = str(values.get(f"{prefix}.vehicle_type", "internal_bus")).strip()
        try:
            vehicle_type = config.VehicleType(vehicle_type_value)
        except ValueError:
            vehicle_type = config.VehicleType.INTERNAL_BUS

        start_location = self._depot_coords_from_choice(values.get(f"{prefix}.start_depot_name", ""), values)
        end_location = self._parse_coords_text(values.get(f"{prefix}.end_location", ""))
        reload_location = self._depot_coords_from_choice(
            values.get(f"{prefix}.reload_depot_name", ""),
            values,
        )
        return config.VehicleConfig(
            vehicle_type=vehicle_type,
            capacity=self._parse_int_value(values.get(f"{prefix}.capacity", 320), 320),
            count=max(0, self._parse_int_value(values.get(f"{prefix}.count", 1), 1)),
            name=str(values.get(f"{prefix}.name", "") or "").strip(),
            config_id=str(values.get(f"{prefix}.config_id", "") or "").strip(),
            fixed_cost=self._parse_int_value(values.get(f"{prefix}.fixed_cost", 0), 0),
            max_distance_km=self._parse_optional_int_value(values.get(f"{prefix}.max_distance_km", "")),
            max_time_hours=self._parse_int_value(values.get(f"{prefix}.max_time_hours", 8), 8),
            service_time_minutes=self._parse_int_value(values.get(f"{prefix}.service_time_minutes", 8), 8),
            enabled=self._parse_bool_value(values.get(f"{prefix}.enabled", True)),
            max_customers_per_route=None,
            max_customers_per_day=self._parse_optional_int_value(values.get(f"{prefix}.max_customers_per_day", "")),
            start_location=start_location,
            end_location=end_location,
            reload_location=reload_location,
            reload_time_minutes=max(0, self._parse_int_value(values.get(f"{prefix}.reload_time_minutes", 30), 30)),
            start_time_minutes=self._parse_int_value(values.get(f"{prefix}.start_time_minutes", 480), 480),
            tsp_depot_location=start_location,
        )

    def _collect_vehicle_configs_from_values(self, values):
        import re

        indices = sorted({
            int(match.group(1))
            for key in values
            for match in [re.match(r"vehicle\.(\d+)\.vehicle_type$", key)]
            if match
        })
        vehicles = []
        for index in indices:
            prefix = f"vehicle.{index}"
            if self._parse_bool_value(values.get(f"{prefix}.remove", False)):
                continue
            vehicle = self._vehicle_config_from_values(values, prefix)
            if vehicle.count <= 0:
                continue
            vehicles.append(vehicle)
        return vehicles

    def _should_rewrite_vehicle_list(self, values):
        import re

        indices = sorted({
            int(match.group(1))
            for key in values
            for match in [re.match(r"vehicle\.(\d+)\.vehicle_type$", key)]
            if match
        })
        if any(self._parse_bool_value(values.get(f"vehicle.{index}.remove", False)) for index in indices):
            return True
        if len(indices) != len(self.cfg.vehicles or []):
            return True

        vehicles = self._collect_vehicle_configs_from_values(values)
        if len(vehicles) != len(self.cfg.vehicles or []):
            return True

        seen_types = set()
        for vehicle in vehicles:
            vehicle_type_value = vehicle.vehicle_type.value
            if vehicle_type_value in seen_types:
                return True
            seen_types.add(vehicle_type_value)
        return False

    def _tuple_literal(self, coords):
        if not coords:
            return "None"
        return f"({float(coords[0])}, {float(coords[1])})"

    def _optional_int_literal(self, value):
        return "None" if value is None else str(int(value))

    def _string_literal(self, value):
        return json.dumps(str(value or ""), ensure_ascii=False)

    def _vehicle_config_literal(self, vehicle):
        return "\n".join([
            "            VehicleConfig(",
            f"                vehicle_type=VehicleType.{vehicle.vehicle_type.name},",
            f"                capacity={int(vehicle.capacity)},",
            f"                count={int(vehicle.count)},",
            f"                name={self._string_literal(getattr(vehicle, 'name', ''))},",
            f"                config_id={self._string_literal(getattr(vehicle, 'config_id', ''))},",
            f"                fixed_cost={int(getattr(vehicle, 'fixed_cost', 0) or 0)},",
            f"                max_distance_km={self._optional_int_literal(vehicle.max_distance_km)},",
            f"                max_time_hours={int(vehicle.max_time_hours)},",
            f"                service_time_minutes={int(vehicle.service_time_minutes)},",
            f"                enabled={'True' if vehicle.enabled else 'False'},",
            f"                max_customers_per_route={self._optional_int_literal(vehicle.max_customers_per_route)},",
            f"                max_customers_per_day={self._optional_int_literal(getattr(vehicle, 'max_customers_per_day', None))},",
            f"                start_location={self._tuple_literal(vehicle.start_location)},",
            f"                end_location={self._tuple_literal(getattr(vehicle, 'end_location', None))},",
            f"                reload_location={self._tuple_literal(getattr(vehicle, 'reload_location', None) or vehicle.start_location)},",
            f"                reload_time_minutes={int(getattr(vehicle, 'reload_time_minutes', 30) or 0)},",
            f"                start_time_minutes={int(vehicle.start_time_minutes)},",
            f"                tsp_depot_location={self._tuple_literal(vehicle.tsp_depot_location or vehicle.start_location)}",
            "            ),",
        ])

    def _replace_vehicles_block(self, content, values):
        import re

        vehicles = self._collect_vehicle_configs_from_values(values)
        if not vehicles:
            return content

        vehicles_block = "\n".join(self._vehicle_config_literal(vehicle) for vehicle in vehicles)
        pattern = r'(def _create_default_vehicles\(self\)\s*->\s*List\[VehicleConfig\]:.*?return\s*)\[.*?\n        \]'
        replacement = lambda match: f"{match.group(1)}[\n{vehicles_block}\n        ]"
        return re.sub(pattern, replacement, content, count=1, flags=re.S)

    def _replace_vehicle_tuple_assignment(self, block, field, new_val):
        import re

        pattern = rf'({field}\s*=\s*)(?:None|\([^)]+\)|depot_[a-zA-Z_]+)'
        if re.search(pattern, block):
            return re.sub(pattern, rf'\g<1>{new_val}', block)

        insert_pattern = r'(\n\s*start_time_minutes\s*=)'
        if field in ("start_location", "end_location", "reload_location", "tsp_depot_location"):
            return re.sub(insert_pattern, f"\n                {field}={new_val},\\1", block, count=1)

        insert_pattern = r'(\n\s*\)\s*,?)'
        return re.sub(insert_pattern, f"\n                {field}={new_val},\\1", block, count=1)

    def _replace_vehicle_field(self, content, vtype, idx, values, prefix):
        """Замества полета на превозно средство в config.py"""
        import re

        # Намираме блока на VehicleConfig за този тип
        vehicle_fields = {
            "name": "str",
            "config_id": "str",
            "enabled": "bool",
            "count": "int",
            "fixed_cost": "int",
            "capacity": "int",
            "max_distance_km": "optional_int",
            "max_time_hours": "int",
            "service_time_minutes": "int",
            "max_customers_per_route": "optional_int",
            "max_customers_per_day": "optional_int",
            "start_depot_name": "depot_choice",
            "reload_depot_name": "reload_depot_choice",
            "end_location": "optional_tuple",
            "reload_time_minutes": "int",
            "start_time_minutes": "int",
        }

        # Намираме позицията на VehicleType.VTYPE в текста
        type_upper = vtype.upper()
        # Търсим VehicleConfig блока за този тип
        pattern = rf'VehicleConfig\(\s*\n\s*vehicle_type\s*=\s*VehicleType\.{type_upper}.*?(?=VehicleConfig\(|\]\s*\n|$)'
        match = re.search(pattern, content, re.DOTALL)
        if not match:
            return content

        block_start = match.start()
        block_end = match.end()
        block = content[block_start:block_end]

        for field, ftype in vehicle_fields.items():
            gui_key = f"{prefix}.{field}"
            if gui_key not in values:
                continue
            raw = values[gui_key]

            if ftype == "bool":
                new_val = "True" if raw else "False"
                block = re.sub(rf'({field}\s*=\s*)(?:True|False)', rf'\g<1>{new_val}', block)
            elif ftype == "str":
                new_val = self._string_literal(str(raw).strip())
                pattern = rf'({field}\s*=\s*)(?:"[^"]*"|\'[^\']*\')'
                if re.search(pattern, block):
                    block = re.sub(pattern, rf'\g<1>{new_val}', block, count=1)
                else:
                    block = re.sub(
                        r'(\n\s*count\s*=\s*[^,\n]+,)',
                        rf'\1\n                {field}={new_val},',
                        block,
                        count=1,
                    )
            elif ftype == "int":
                try:
                    val = int(raw)
                    block = re.sub(rf'({field}\s*=\s*)-?\d[\d_]*', rf'\g<1>{val}', block)
                except (ValueError, TypeError):
                    pass
            elif ftype == "optional_int":
                raw_str = str(raw).strip()
                if raw_str == "" or raw_str.lower() == "none":
                    block = re.sub(rf'({field}\s*=\s*)(?:None|-?\d[\d_]*)', rf'\g<1>None', block)
                else:
                    try:
                        val = int(raw_str)
                        block = re.sub(rf'({field}\s*=\s*)(?:None|-?\d[\d_]*)', rf'\g<1>{val}', block)
                    except ValueError:
                        pass
            elif ftype == "optional_tuple":
                new_val = self._format_optional_tuple_value(raw)
                if new_val is None:
                    continue
                block = self._replace_vehicle_tuple_assignment(block, field, new_val)
                if field == "start_location":
                    block = self._replace_vehicle_tuple_assignment(block, "tsp_depot_location", new_val)
            elif ftype == "depot_choice":
                new_val = self._format_depot_choice_value(raw, values)
                if new_val is None:
                    continue
                block = self._replace_vehicle_tuple_assignment(block, "start_location", new_val)
                block = self._replace_vehicle_tuple_assignment(block, "tsp_depot_location", new_val)
            elif ftype == "reload_depot_choice":
                new_val = self._format_depot_choice_value(raw, values)
                if new_val is None:
                    continue
                block = self._replace_vehicle_tuple_assignment(block, "reload_location", new_val)

        content = content[:block_start] + block + content[block_end:]
        return content

    # ── Actions ──────────────────────────────────────────────

    def _save_config(self, show_success=True):
        try:
            self.status_var.set("Записвам настройките...")
            self.root.update_idletasks()
            values = self._collect_values()
            errors, warnings = self._validate_values(values)
            if errors:
                self.validation_var.set(f"{len(errors)} грешки")
                self.status_var.set("Записът е спрян: има невалидни настройки")
                self._focus_validation_error(errors[0][0])
                details = "\n".join(f"• {message}" for _key, message in errors[:12])
                if len(errors) > 12:
                    details += f"\n• … и още {len(errors) - 12}"
                messagebox.showerror("Настройките не са записани", details)
                return False

            changed = self._apply_to_config_file(values)
            if changed:
                self.status_var.set("Настройките са записани безопасно в config.py")
            else:
                if values != self._saved_snapshot:
                    raise RuntimeError(
                        "Има променени полета, но не беше намерено съответстващо място в config.py. "
                        "Файлът не е променен."
                    )
                self.status_var.set("Няма нови промени за запис")

            self._saved_snapshot = copy.deepcopy(values)
            self.validation_var.set(
                f"Запазено с {len(warnings)} предупреждения" if warnings else "Проверено и запазено"
            )
            self._set_dirty(False)
            if show_success and warnings:
                messagebox.showwarning(
                    "Запазено с предупреждения",
                    "Настройките са записани.\n\n" + "\n".join(f"• {item}" for item in warnings),
                )
            return True
        except Exception as e:
            self.status_var.set("Грешка при запис")
            messagebox.showerror("Грешка", f"Грешка при запис: {e}")
            return False

    def _save(self):
        return self._save_config(show_success=True)

    def _save_and_close(self):
        if self._save_config(show_success=False):
            self.root.destroy()

    def _save_and_run(self):
        self.status_var.set("Проверявам и записвам преди стартиране...")
        if not self._save_config(show_success=False):
            return
        
        # Стартираме програмата ПРЕДИ да затворим GUI
        import subprocess
        try:
            if getattr(sys, 'frozen', False):
                exe_path = sys.executable
                exe_dir = os.path.dirname(exe_path)
                env = os.environ.copy()
                env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                env.pop("_MEIPASS2", None)
                subprocess.Popen(
                    [exe_path],
                    cwd=exe_dir,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
            else:
                main_py = os.path.join(_base_dir, "main.py")
                subprocess.Popen(
                    [sys.executable, main_py],
                    cwd=_base_dir,
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
        except Exception as e:
            self.status_var.set("Грешка при стартиране")
            messagebox.showerror("Грешка", f"Не мога да стартирам програмата: {e}")
            return
        
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = ConfigGUI()
    app.run()


if __name__ == "__main__":
    main()
