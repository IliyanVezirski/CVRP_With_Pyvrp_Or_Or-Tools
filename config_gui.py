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
import json
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Добавяме текущата директория в path
if getattr(sys, 'frozen', False):
    _base_dir = os.path.dirname(sys.executable)
else:
    _base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _base_dir)
import config


class ConfigGUI:
    """Графичен интерфейс за config.py"""

    # Български етикети за секциите и полетата
    SECTION_LABELS = {
        "input": "📥 Входни данни",
        "vehicles": "🚐 Превозни средства",
        "warehouse": "🏭 Предварителна оптимизация",
        "cvrp": "⚙️ Солвър (CVRP)",
        "locations": "📍 Локации",
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CVRP Настройки")
        self.root.geometry("1180x820")
        self.root.minsize(1000, 700)
        self.root.resizable(True, True)
        self._configure_style()

        # Зареждаме текущата конфигурация
        importlib.reload(config)
        self.cfg = config.get_config()
        self.widgets = {}  # field_key → widget
        self.status_var = tk.StringVar(value="Готово")
        self.vehicle_container = None
        self.vehicle_next_index = 0
        self.depot_choice_widgets = []
        self.depot_listbox = None
        self.traffic_zone_listbox = None
        self.center_zone_listbox = None
        self.center_zone_priority_vars = {}
        self.center_zone_restricted_vars = {}

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────

    def _configure_style(self):
        self.root.configure(bg="#eef2f6")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.root.option_add("*Font", ("Segoe UI", 9))
        style.configure("TFrame", background="#eef2f6")
        style.configure("Toolbar.TFrame", background="#ffffff")
        style.configure("Surface.TFrame", background="#ffffff")
        style.configure("TLabel", background="#eef2f6", foreground="#1f2933")
        style.configure("Surface.TLabel", background="#ffffff", foreground="#1f2933")
        style.configure("Hint.TLabel", background="#ffffff", foreground="#667085", font=("Segoe UI", 8))
        style.configure("Status.TLabel", background="#ffffff", foreground="#475467", font=("Segoe UI", 8))
        style.configure("AppTitle.TLabel", background="#ffffff", foreground="#111827", font=("Segoe UI", 15, "bold"))
        style.configure("AppSubtitle.TLabel", background="#ffffff", foreground="#667085", font=("Segoe UI", 8))
        style.configure("TLabelframe", background="#ffffff", bordercolor="#d0d5dd", relief="solid")
        style.configure("TLabelframe.Label", background="#ffffff", foreground="#111827", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(10, 6))
        style.configure("Primary.TButton", padding=(12, 7), foreground="#ffffff", background="#2563eb")
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])
        style.configure("TEntry", padding=(6, 4))
        style.configure("TCombobox", padding=(6, 4))
        style.configure("TNotebook", background="#eef2f6", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#111827")])

    def _build_ui(self):
        # Toolbar
        toolbar = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(14, 10))
        toolbar.pack(fill="x")

        title_box = ttk.Frame(toolbar, style="Toolbar.TFrame")
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="CVRP настройки", style="AppTitle.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text="Редакция на основните параметри за входни данни, бусове, решители и output.",
            style="AppSubtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        actions = ttk.Frame(toolbar, style="Toolbar.TFrame")
        actions.pack(side="right")
        ttk.Button(actions, text="Запази", command=self._save).pack(side="left", padx=3)
        ttk.Button(actions, text="Запази и затвори", command=self._save_and_close, style="Primary.TButton").pack(side="left", padx=3)
        ttk.Button(actions, text="Запази и стартирай", command=self._save_and_run).pack(side="left", padx=3)
        ttk.Button(actions, text="Отказ", command=self.root.destroy).pack(side="left", padx=(10, 0))

        # Notebook (tabs)
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=12, pady=(12, 8))

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

        status_bar = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(14, 7))
        status_bar.pack(fill="x", side="bottom")
        ttk.Label(status_bar, textvariable=self.status_var, style="Status.TLabel").pack(side="left")

    # ── Helpers ──────────────────────────────────────────────

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

        def _on_mousewheel(event):
            if canvas._scroll_needed:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_wheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_wheel(event):
            canvas.unbind_all("<MouseWheel>")

        def _resize_inner(event):
            canvas.itemconfigure(window_id, width=event.width)

        canvas.bind("<Configure>", _resize_inner, add="+")
        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        frame.bind("<Enter>", _bind_wheel)
        frame.bind("<Leave>", _unbind_wheel)
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
        elif field_type == "combo" and options:
            var = tk.StringVar(value=display_value)
            combo = ttk.Combobox(parent, textvariable=var, values=options, state="readonly", width=34)
            combo.grid(row=row, column=1, sticky="w", padx=6, pady=6)
            self._bind_text_editing(combo)
            self._bind_combobox_scrolling(combo)
            self.widgets[key] = var
        else:
            var = tk.StringVar(value=display_value)
            entry = ttk.Entry(parent, textvariable=var, width=40)
            entry.grid(row=row, column=1, sticky="we", padx=6, pady=6)
            self._bind_text_editing(entry)
            self.widgets[key] = var

        if tooltip:
            ttk.Label(parent, text=tooltip, style="Hint.TLabel", wraplength=330).grid(
                row=row, column=2, sticky="nw", padx=(10, 4), pady=6
            )

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
     "output": { "excel_output_dir": "H:\\Out" },
     "set_data": { "enable_set_data_upload": false }
   }

2. Точкова нотация:
   "settings": {
     "output.excel_output_dir": "H:\\Out",
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
tsp_generate_map                      -> api.tsp_generate_html_map
tsp_generate_local_html               -> api.tsp_generate_html_map
tsp_local_html                        -> api.tsp_generate_html_map
tsp_upload_map                        -> api.tsp_upload_html_map
tsp_worker_timeout                    -> api.tsp_worker_timeout_seconds
tsp_daily_report                      -> api.tsp_daily_report_enabled
tsp_daily_report_time                 -> api.tsp_daily_report_time
                """,
                31,
            ),
            (
                "Solver настройки:",
                """
cvrp.solver_type                       pyvrp или or_tools
cvrp.objective_metric                  distance = най-къси км, time = най-кратко време
cvrp.time_limit_seconds                време за решаване
cvrp.enable_parallel_solving           паралелно решаване
cvrp.num_workers                       брой процеси (-1 = автоматично)
cvrp.allow_customer_skipping           позволява пропускане на клиенти
cvrp.distance_penalty_disjunction      глоба за пропуснат клиент
cvrp.enable_customer_time_windows      спазва работното време на клиентите
cvrp.first_solution_strategy           OR-Tools начална стратегия
cvrp.local_search_metaheuristic        OR-Tools локално търсене
cvrp.lns_time_limit_seconds            OR-Tools LNS лимит
cvrp.pyvrp_seed                        точен PyVRP seed
cvrp.pyvrp_seed_base                   база за seed-ове при паралелно PyVRP
cvrp.pyvrp_num_neighbours              PyVRP neighbourhood размер
cvrp.pyvrp_ils_no_improvement          PyVRP търпимост без подобрение
cvrp.pyvrp_ils_history_length          PyVRP ILS история
cvrp.pyvrp_use_extended_operators      разширени PyVRP оператори
cvrp.pyvrp_min_perturbations           минимална perturbation сила
cvrp.pyvrp_max_perturbations           максимална perturbation сила
cvrp.pyvrp_display_progress            PyVRP progress log
                """,
                22,
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
input.json_delivery_comment_field
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
output.enable_excel_output              генерира Excel отчети
output.excel_output_dir                 папка за Excel
output.routes_excel_file                файл с маршрути
output.warehouse_excel_file             файл за склад/необслужени
output.efficiency_excel_file            файл ефективност
output.excel_bus_number_prefix          префикс ID бус
output.excel_bus_number_digits          брой цифри

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
  Patch по vehicle_type. Пази останалите бусове непроменени.
  start_depot_name може да сочи име от depots.
  end_location е optional GPS крайна точка. Ако липсва, маршрутът завършва в стартовото депо.
  end_depot_name може да сочи име от depots.
  "vehicles": [
    { "vehicle_type": "internal_bus", "count": 7, "capacity": 385, "name": "HELL", "start_depot_name": "main", "end_location": [42.7000, 23.4000] },
    { "vehicle_type": "vratza_bus", "count": 3, "start_depot_name": "vratza", "end_depot_name": "vratza" }
  ]

replace_vehicles:
  Пълна подмяна на всички бусове. Използвай внимателно.

Полета за един бус:
  vehicle_type, capacity, count, name, fixed_cost, max_distance_km,
  max_time_hours, service_time_minutes, enabled, start_location, end_location,
  max_customers_per_route, start_time_minutes, tsp_depot_location

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
      "enabled": true
    }
  ]

traffic_zones:
  Допълнителни зони с multiplier за време.
  "traffic_zones": [
    { "name": "Center traffic", "center": [42.6977, 23.3219], "radius_km": 3.0, "multiplier": 1.3, "enabled": true }
  ]

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
        hint = tooltip or "По един елемент на ред"
        ttk.Label(parent, text=hint, style="Hint.TLabel", wraplength=330).grid(
            row=row, column=2, sticky="nw", padx=(10, 4), pady=6
        )

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
            lines.append(f"{name}: {float(center[0])}, {float(center[1])}, {radius}, {multiplier}, {enabled}")
        return "\n".join(lines)

    def _format_center_zones_text(self, zones):
        lines = []
        for zone in zones or []:
            name = getattr(zone, "name", "Център зона")
            mode = str(getattr(zone, "mode", "circle") or "circle").lower()
            if mode == "polygon":
                points = getattr(zone, "polygon", []) or []
                geometry = "; ".join(f"{float(lat)}, {float(lon)}" for lat, lon in points)
                radius = ""
            else:
                center = getattr(zone, "center_coords", None)
                if not center:
                    continue
                geometry = f"{float(center[0])}, {float(center[1])}"
                radius = f"{float(getattr(zone, 'radius_km', 0) or 0):g}"
            priority = ",".join(getattr(zone, "priority_vehicle_types", []) or [])
            restricted = ",".join(getattr(zone, "restricted_vehicle_types", []) or [])
            enabled = "true" if getattr(zone, "enabled", True) else "false"
            discount = f"{float(getattr(zone, 'discount_priority_vehicle', 1.0) or 1.0):g}"
            outside = f"{float(getattr(zone, 'priority_vehicle_outside_penalty', 0.0) or 0.0):g}"
            penalties = ",".join(
                f"{bus}={float(value):g}"
                for bus, value in (getattr(zone, "vehicle_penalties", {}) or {}).items()
            )
            lines.append(
                f"{name}: {mode} | {geometry} | {radius} | {priority} | {restricted} | "
                f"{enabled} | {discount} | {outside} | {penalties}"
            )
        return "\n".join(lines)

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
            parts = [part.strip() for part in zone_raw.split(",")]
            if len(parts) < 4:
                continue
            try:
                enabled = True
                if len(parts) >= 5:
                    enabled = parts[4].lower() not in ("0", "false", "no", "off", "не")
                zones.append(
                    config.TrafficZoneConfig(
                        name=name.strip(),
                        center_coords=(float(parts[0]), float(parts[1])),
                        radius_km=float(parts[2]),
                        duration_multiplier=float(parts[3]),
                        enabled=enabled,
                    )
                )
            except ValueError:
                continue
        return zones

    def _parse_center_zones_text(self, raw):
        zones = []
        for line in (raw or "").splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            name, zone_raw = line.split(":", 1)
            parts = [part.strip() for part in zone_raw.split("|")]
            if len(parts) < 5:
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
                    polygon = []
                    for point_raw in geometry.split(";"):
                        point_parts = [float(part.strip()) for part in point_raw.split(",") if part.strip()]
                        if len(point_parts) == 2:
                            polygon.append((point_parts[0], point_parts[1]))
                    if len(polygon) < 3:
                        continue
                    center_coords = polygon[0]
                else:
                    coord_parts = [float(part.strip()) for part in geometry.split(",") if part.strip()]
                    if len(coord_parts) != 2:
                        continue
                    center_coords = (coord_parts[0], coord_parts[1])
                    polygon = []

                zones.append(
                    config.CenterZoneConfig(
                        name=name.strip(),
                        mode=mode,
                        center_coords=center_coords,
                        radius_km=radius,
                        polygon=polygon,
                        enabled=enabled,
                        priority_vehicle_types=priority_types,
                        restricted_vehicle_types=restricted_types,
                        discount_priority_vehicle=discount,
                        priority_vehicle_outside_penalty=outside_penalty,
                        vehicle_penalties=penalties,
                    )
                )
            except ValueError:
                continue
        return zones

    def _traffic_zone_display_label(self, zone):
        center = getattr(zone, "center_coords", (0, 0))
        radius = float(getattr(zone, "radius_km", 0) or 0)
        multiplier = float(getattr(zone, "duration_multiplier", 1.0) or 1.0)
        delay_percent = max(0, round((multiplier - 1.0) * 100))
        status = "" if getattr(zone, "enabled", True) else " (изключена)"
        return (
            f"{getattr(zone, 'name', 'Трафик зона')}: "
            f"{float(center[0]):.5f}, {float(center[1]):.5f} | "
            f"{radius:g} км | +{delay_percent}%{status}"
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
        return (
            f"{getattr(zone, 'name', 'Център зона')}: {geometry} | "
            f"приоритет: {priority} | глоба: {restricted}{status}"
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
        if not isinstance(depot_widget, tk.Text) or self.depot_listbox is None:
            return
        selection = self.depot_listbox.curselection()
        if not selection:
            messagebox.showinfo("Няма избрано депо", "Избери депо от списъка.")
            return

        depots = self._parse_depots_text(depot_widget.get("1.0", "end-1c"))
        names = list(depots.keys())
        index = selection[0]
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
        if not isinstance(depot_widget, tk.Text) or self.depot_listbox is None:
            return
        if not name_widget or not coords_widget:
            return
        selection = self.depot_listbox.curselection()
        if not selection:
            return

        depots = self._parse_depots_text(depot_widget.get("1.0", "end-1c"))
        items = list(depots.items())
        index = selection[0]
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
        self.status_var.set("Формата за депо е изчистена.")

    def _append_traffic_zone_from_fields(self):
        name_widget = self.widgets.get("locations.new_traffic_zone_name")
        coords_widget = self.widgets.get("locations.new_traffic_zone_coords")
        radius_widget = self.widgets.get("locations.new_traffic_zone_radius")
        delay_widget = self.widgets.get("locations.new_traffic_zone_delay")
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
        if not isinstance(zones_widget, tk.Text) or self.traffic_zone_listbox is None:
            return
        selection = self.traffic_zone_listbox.curselection()
        if not selection:
            return
        zones = self._parse_traffic_zones_text(zones_widget.get("1.0", "end-1c"))
        index = selection[0]
        if index >= len(zones):
            return
        zone = zones[index]
        name_widget = self.widgets.get("locations.new_traffic_zone_name")
        coords_widget = self.widgets.get("locations.new_traffic_zone_coords")
        radius_widget = self.widgets.get("locations.new_traffic_zone_radius")
        delay_widget = self.widgets.get("locations.new_traffic_zone_delay")
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
        if self.traffic_zone_listbox is not None:
            self.traffic_zone_listbox.selection_clear(0, "end")
        self.status_var.set("Формата за трафик зона е изчистена.")

    def _append_center_zone_from_fields(self):
        name_widget = self.widgets.get("locations.new_center_zone_name")
        mode_widget = self.widgets.get("locations.new_center_zone_mode")
        coords_widget = self.widgets.get("locations.new_center_zone_coords")
        radius_widget = self.widgets.get("locations.new_center_zone_radius")
        discount_widget = self.widgets.get("locations.new_center_zone_discount")
        outside_widget = self.widgets.get("locations.new_center_zone_outside_penalty")
        penalty_widget = self.widgets.get("locations.new_center_zone_penalty")
        zones_widget = self.widgets.get("locations.center_zones")
        if not all((name_widget, mode_widget, coords_widget, radius_widget, discount_widget, outside_widget, penalty_widget)):
            return
        if not isinstance(zones_widget, tk.Text):
            return

        zones = self._parse_center_zones_text(zones_widget.get("1.0", "end-1c"))
        name = name_widget.get().strip() or self._next_center_zone_name(zones)
        mode = str(mode_widget.get() or "circle").strip().lower()
        if mode not in ("circle", "polygon"):
            mode = "circle"

        try:
            radius = float(radius_widget.get() or 0)
            discount = float(discount_widget.get() or 0.9)
            outside_penalty = float(outside_widget.get() or 0)
            penalty = float(penalty_widget.get() or 0)
        except ValueError:
            messagebox.showwarning("Грешна център зона", "Радиусът, отстъпката и глобите трябва да са числа.")
            return

        raw_coords = (
            coords_widget.get("1.0", "end-1c")
            if isinstance(coords_widget, tk.Text)
            else coords_widget.get()
        )
        polygon = []
        if mode == "polygon":
            polygon = self._parse_polygon_points_text(raw_coords)
            if len(polygon) < 3:
                messagebox.showwarning("Грешен полигон", "За polygon въведи поне 3 точки. Може всяка точка на нов ред.")
                return
            center_coords = polygon[0]
            radius = 0.0
        else:
            center_coords = self._parse_coords_text(raw_coords)
            if center_coords is None:
                messagebox.showwarning("Грешни координати", "Въведи координати във формат lat, lon.")
                return
            if radius <= 0:
                messagebox.showwarning("Грешен радиус", "Радиусът трябва да е по-голям от 0.")
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
        if not priority_types and not restricted_types:
            messagebox.showwarning("Няма правила", "Избери поне един тип бус за приоритет или глоба.")
            return

        zone = config.CenterZoneConfig(
            name=name,
            mode=mode,
            center_coords=center_coords,
            radius_km=radius,
            polygon=polygon,
            enabled=True,
            priority_vehicle_types=priority_types,
            restricted_vehicle_types=restricted_types,
            discount_priority_vehicle=discount,
            priority_vehicle_outside_penalty=outside_penalty,
            vehicle_penalties={vehicle_type: penalty for vehicle_type in restricted_types},
        )
        existing_index = next((idx for idx, item in enumerate(zones) if item.name == name), None)
        if existing_index is None:
            zones.append(zone)
            action = "добавена"
        else:
            zones[existing_index] = zone
            action = "обновена"
        self._sync_center_zone_widgets(zones)
        name_widget.set("")
        if isinstance(coords_widget, tk.Text):
            coords_widget.delete("1.0", "end")
        else:
            coords_widget.set("")
        self.status_var.set(f"Център зона '{name}' е {action}. Натисни Запази, за да влезе в config.py.")

    def _load_selected_center_zone(self, event=None):
        zones_widget = self.widgets.get("locations.center_zones")
        if not isinstance(zones_widget, tk.Text) or self.center_zone_listbox is None:
            return
        selection = self.center_zone_listbox.curselection()
        if not selection:
            return
        zones = self._parse_center_zones_text(zones_widget.get("1.0", "end-1c"))
        index = selection[0]
        if index >= len(zones):
            return
        zone = zones[index]

        name_widget = self.widgets.get("locations.new_center_zone_name")
        mode_widget = self.widgets.get("locations.new_center_zone_mode")
        coords_widget = self.widgets.get("locations.new_center_zone_coords")
        radius_widget = self.widgets.get("locations.new_center_zone_radius")
        discount_widget = self.widgets.get("locations.new_center_zone_discount")
        outside_widget = self.widgets.get("locations.new_center_zone_outside_penalty")
        penalty_widget = self.widgets.get("locations.new_center_zone_penalty")
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

        penalties = getattr(zone, "vehicle_penalties", {}) or {}
        if penalty_widget:
            penalty = next(iter(penalties.values()), 0.0)
            penalty_widget.set(f"{float(penalty):g}")

        priority_types = set(getattr(zone, "priority_vehicle_types", []) or [])
        restricted_types = set(getattr(zone, "restricted_vehicle_types", []) or [])
        for vehicle_type, var in self.center_zone_priority_vars.items():
            var.set(vehicle_type in priority_types)
        for vehicle_type, var in self.center_zone_restricted_vars.items():
            var.set(vehicle_type in restricted_types)

        self.status_var.set(f"Заредена е център зона '{getattr(zone, 'name', '')}' за редакция.")

    def _clear_center_zone_form(self):
        name_widget = self.widgets.get("locations.new_center_zone_name")
        mode_widget = self.widgets.get("locations.new_center_zone_mode")
        coords_widget = self.widgets.get("locations.new_center_zone_coords")
        radius_widget = self.widgets.get("locations.new_center_zone_radius")
        discount_widget = self.widgets.get("locations.new_center_zone_discount")
        outside_widget = self.widgets.get("locations.new_center_zone_outside_penalty")
        penalty_widget = self.widgets.get("locations.new_center_zone_penalty")
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
        if penalty_widget:
            penalty_widget.set("40000")
        for vehicle_type, var in self.center_zone_priority_vars.items():
            var.set(vehicle_type == "center_bus")
        for vehicle_type, var in self.center_zone_restricted_vars.items():
            var.set(vehicle_type != "center_bus")
        if self.center_zone_listbox is not None:
            self.center_zone_listbox.selection_clear(0, "end")
        self.status_var.set("Формата за център зона е изчистена.")

    def _remove_selected_traffic_zone(self):
        zones_widget = self.widgets.get("locations.traffic_zones")
        if not isinstance(zones_widget, tk.Text) or self.traffic_zone_listbox is None:
            return
        selection = self.traffic_zone_listbox.curselection()
        if not selection:
            messagebox.showinfo("Няма избрана зона", "Избери зона от списъка.")
            return

        zones = self._parse_traffic_zones_text(zones_widget.get("1.0", "end-1c"))
        index = selection[0]
        if index >= len(zones):
            return
        removed = zones.pop(index)
        self._sync_traffic_zone_widgets(zones)
        self.status_var.set(f"Премахната е {removed.name}. Натисни Запази, за да се махне от config.py.")

    def _remove_selected_center_zone(self):
        zones_widget = self.widgets.get("locations.center_zones")
        if not isinstance(zones_widget, tk.Text) or self.center_zone_listbox is None:
            return
        selection = self.center_zone_listbox.curselection()
        if not selection:
            messagebox.showinfo("Няма избрана зона", "Избери център зона от списъка.")
            return

        zones = self._parse_center_zones_text(zones_widget.get("1.0", "end-1c"))
        index = selection[0]
        if index >= len(zones):
            return
        removed = zones.pop(index)
        self._sync_center_zone_widgets(zones)
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
  Избор PyVRP/OR-Tools, време, пропускане, работно време, OSRM/Valhalla и фини настройки.

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
    "excel_output_dir": "H:\\Run1",
    "routes_output_dir": "H:\\Run1\\Routes",
    "csv_output_file": "H:\\Run1\\routes.csv"
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
  5. Решава задачата с PyVRP или OR-Tools.
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
  програмата използва първия прозорец, защото solver-ите са настроени за един прозорец на клиент.
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
    Максимално разстояние за маршрут. Празно = без лимит.
  max_time_hours:
    Максимално работно време.
  service_time_minutes:
    Време за обслужване на клиент.
  enabled:
    Дали типът е активен.
  start_location:
    Координати на стартово депо.
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
  max_customers_per_route:
    Максимален брой клиенти в един маршрут.

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
                """,
                38,
            ),
            (
                "7. Solver настройки",
                """
solver_type:
  pyvrp:
    Основен качествен solver.
    Добър при повече време и различни seed-ове.
  or_tools:
    Стабилен solver за сравнение и контрол.

objective_metric:
  distance:
    Solver-ът търси най-къси километри.
  time:
    Solver-ът търси най-кратко време по OSRM/Valhalla duration матрицата.

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
                32,
            ),
            (
                "8. PyVRP и OR-Tools фини настройки",
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
  pyvrp_use_extended_operators:
    По-тежки move operators.
  pyvrp_min_perturbations / pyvrp_max_perturbations:
    Сила на разбъркване при restart.
  pyvrp_display_progress:
    Показва прогрес лог.

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
                36,
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
    Генерира обща карта.
  map_output_file:
    Файл за общата карта.
  routes_output_dir:
    Папка за отделни route HTML файлове.
  route_maps_upload_mode:
    disabled = не качва, legacy = старото поведение, effect_upload = качва HTML маршрутите към upload endpoint.
  route_maps_upload_url:
    URL за качване, напр. https://effect.bg/dragon/hellbizante/upload-files.php
  route_maps_upload_token:
    Token за pData. За Effect endpoint-а е Effect-Bizante-Token.
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
    Генерира Excel отчети.
  excel_output_dir:
    Папка за Excel.
  routes_excel_file:
    Маршрути.
  warehouse_excel_file:
    Необслужени/склад.
  efficiency_excel_file:
    Ефективност.

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
  Output пътищата трябва да съществуват.
  Програмата трябва да има права за писане.
  Ако подадеш H:\\..., този drive/share трябва да е достъпен от компютъра, който стартира програмата.
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
  - PyVRP и OR-Tools трябва да се сравняват върху едни и същи клиенти.
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
  API пази само един активен run.
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
    "solver_type": "pyvrp",
    "time_limit_seconds": 180,
    "depots": {
      "main": [42.6957, 23.2316],
      "vratza": [43.2210, 23.5344]
    },
    "vehicles": [
      { "vehicle_type": "internal_bus", "count": 7, "capacity": 385, "start_depot_name": "main", "end_location": [42.7000, 23.4000] },
      { "vehicle_type": "vratza_bus", "count": 3, "capacity": 385, "start_depot_name": "vratza", "end_depot_name": "vratza" }
    ],
    "center_zone": {
      "mode": "circle",
      "center": [42.6977, 23.3219],
      "radius_km": 2.0,
      "internal_bus_penalty": 40000,
      "external_bus_penalty": 40000,
      "vratza_bus_penalty": 40000
    },
    "traffic_zones": [
      { "name": "Center traffic", "center": [42.6977, 23.3219], "radius_km": 3.0, "multiplier": 1.3, "enabled": true }
    ],
    "osrm": {
      "base_url": "http://localhost:5000",
      "chunk_size": 80,
      "timeout_seconds": 45
    },
    "output": {
      "enable_excel_output": true,
      "excel_output_dir": "H:\\Out",
      "enable_csv_output": true,
      "csv_output_file": "H:\\Out\\routes.csv"
    },
    "set_data": {
      "enable_set_data_upload": false
    }
  },
  "customers": [
    { "IdCust": "1", "CustName": "Клиент", "GPS": "42.6977,23.3219", "Volume": 10, "WorkTime": "08:00-13:00", "DeliveryComment": "Обади се 10 мин преди доставка" }
  ]
}
                """,
                45,
            ),
            (
                "16. Диагностика",
                """
API не отговаря:
  - Провери дали сървърът е стартиран.
  - Провери /health.
  - Провери host 0.0.0.0 и port 8088.
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
                            tooltip="Празно = без лимит."); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.max_time_hours", "Макс. време (ч.):", v.max_time_hours); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.service_time_minutes", "Обслужване (мин):", v.service_time_minutes); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.max_customers_per_route", "Макс. клиенти:",
                             v.max_customers_per_route if v.max_customers_per_route else "",
                             tooltip="Празно = без ограничение"); gr += 1
            self._add_depot_choice_field(vehicle_box, gr, f"{prefix}.start_depot_name", "Депо тръгване:",
                                         self._depot_name_for_coords(v.start_location),
                                         depot_options,
                                         tooltip="Избери депо по име. Ако добавиш ново депо, можеш да напишеш името му тук след запис/презареждане."); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.end_location", "Крайна точка GPS:",
                            self._format_optional_coords(getattr(v, "end_location", None)),
                            tooltip="По желание във формат lat, lon. Празно = маршрутът завършва в депото на тръгване."); gr += 1
            self._add_field(vehicle_box, gr, f"{prefix}.start_time_minutes", "Старт (мин от 00:00):", v.start_time_minutes,
                             tooltip="480 = 08:00"); gr += 1

        self.vehicle_next_index = len(self.cfg.vehicles)
        self.vehicle_next_row = r

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

    def _new_vehicle_template(self, vehicle_type_value):
        for vehicle in reversed(self.cfg.vehicles or []):
            if vehicle.vehicle_type.value == vehicle_type_value:
                clone = copy.deepcopy(vehicle)
                clone.count = 1
                clone.enabled = True
                clone.name = ""
                clone.end_location = None
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
            tooltip="Празно = без лимит.",
        ); gr += 1
        self._add_field(vehicle_box, gr, f"{prefix}.max_time_hours", "Макс. време (ч.):", vehicle.max_time_hours); gr += 1
        self._add_field(vehicle_box, gr, f"{prefix}.service_time_minutes", "Обслужване (мин):", vehicle.service_time_minutes); gr += 1
        self._add_field(
            vehicle_box,
            gr,
            f"{prefix}.max_customers_per_route",
            "Макс. клиенти:",
            vehicle.max_customers_per_route if vehicle.max_customers_per_route else "",
            tooltip="Празно = без ограничение.",
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
            "Избор на решител и общ лимит за търсене. PyVRP е основният режим, OR-Tools е полезен за сравнение.",
        )
        self._add_field(basic, r, "cvrp.solver_type", "Тип солвър:", c.solver_type,
                         "combo", ["pyvrp", "or_tools"]); r += 1
        self._add_field(basic, r, "cvrp.objective_metric", "Цел на оптимизацията:", getattr(c, "objective_metric", "distance"),
                         "combo", ["distance", "time"],
                         tooltip="distance = най-къси километри. time = най-кратко време по OSRM/Valhalla duration матрицата."); r += 1
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
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_ils_no_improvement", "ILS без подобрение:", getattr(c, "pyvrp_ils_no_improvement", 300000)); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_ils_history_length", "ILS история:", getattr(c, "pyvrp_ils_history_length", 500)); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_use_extended_operators", "Разширени оператори:", getattr(c, "pyvrp_use_extended_operators", True), "bool"); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_min_perturbations", "Мин. perturbations:", getattr(c, "pyvrp_min_perturbations", 1)); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_max_perturbations", "Макс. perturbations:", getattr(c, "pyvrp_max_perturbations", 40)); r += 1
        self._add_field(pyvrp_quality, r, "cvrp.pyvrp_display_progress", "PyVRP progress лог:", getattr(c, "pyvrp_display_progress", False), "bool"); r += 1

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
            "При OR-Tools пуска различни стратегии. При PyVRP пуска различни seed-ове и избира най-добрия резултат.",
        )
        self._add_field(parallel, r, "cvrp.enable_parallel_solving", "Включено:", c.enable_parallel_solving, "bool"); r += 1
        self._add_field(parallel, r, "cvrp.num_workers", "Брой процеси:", c.num_workers,
                         tooltip="-1 = всички ядра без едно"); r += 1
        self._add_field(parallel, r, "cvrp.pyvrp_seed_base", "PyVRP seed база:", getattr(c, "pyvrp_seed_base", 42),
                         tooltip="При PyVRP worker-ите използват seed база + номер на worker."); r += 1
        self._add_list_field(parallel, r, "cvrp.parallel_first_solution_strategies",
                             "First solution стратегии:", c.parallel_first_solution_strategies); r += 1
        self._add_list_field(parallel, r, "cvrp.parallel_local_search_metaheuristics",
                             "Метаевристики:", c.parallel_local_search_metaheuristics); r += 1

    # ── Tab: Локации ─────────────────────────────────────────

    def _add_center_zones_editor(self, parent, row, loc):
        box = ttk.LabelFrame(parent, text="Допълнителни център зони", padding=(14, 12))
        box.grid(row=row, column=0, columnspan=3, sticky="we", padx=2, pady=(6, 14))
        box.columnconfigure(0, weight=1)
        box.columnconfigure(1, weight=1)

        form = ttk.LabelFrame(box, text="Нова или редакция", padding=(12, 10))
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=2)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(2, weight=0)

        list_frame = ttk.LabelFrame(box, text="Създадени зони", padding=(12, 10))
        list_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=2)
        list_frame.columnconfigure(0, weight=1)

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
        self.widgets["locations.new_center_zone_mode"] = mode_var
        ttk.Label(form, text="circle = радиус, polygon = начертана зона", style="Hint.TLabel").grid(
            row=1, column=2, sticky="w", padx=(0, 0), pady=5
        )

        ttk.Label(form, text="Координати", style="Surface.TLabel").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=5)
        coords_text = tk.Text(form, height=4, width=46, font=("Segoe UI", 9), wrap="none", relief="solid", borderwidth=1)
        coords_text.grid(row=2, column=1, columnspan=2, sticky="we", padx=(0, 0), pady=5)
        self._bind_text_editing(coords_text)
        self._bind_text_scrolling(coords_text)
        self.widgets["locations.new_center_zone_coords"] = coords_text
        coord_actions = ttk.Frame(form, style="Surface.TFrame")
        coord_actions.grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(coord_actions, text="Постави координати", command=lambda: self._paste_to_text_widget(coords_text)).pack(side="left", padx=(0, 6))
        ttk.Button(coord_actions, text="Избери/начертай на карта", command=self._open_new_center_zone_editor).pack(side="left")

        ttk.Label(form, text="Радиус км", style="Surface.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=5)
        radius_var = tk.StringVar(value="1.5")
        radius_combo = ttk.Combobox(form, textvariable=radius_var, values=["0.5", "1", "1.5", "2", "3", "5"], width=10, state="normal")
        radius_combo.grid(row=4, column=1, sticky="w", padx=(0, 8), pady=5)
        self._bind_text_editing(radius_combo)
        self.widgets["locations.new_center_zone_radius"] = radius_var
        ttk.Label(form, text="ползва се само при circle", style="Hint.TLabel").grid(row=4, column=2, sticky="w", pady=5)

        rules = ttk.Frame(form, style="Surface.TFrame")
        rules.grid(row=5, column=0, columnspan=3, sticky="we", pady=(8, 6))
        ttk.Label(rules, text="Тип бус", style="Surface.TLabel", width=18).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 4))
        ttk.Label(rules, text="Приоритет", style="Surface.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 22), pady=(0, 4))
        ttk.Label(rules, text="Глоба", style="Surface.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=(0, 4))
        vehicle_labels = [
            ("center_bus", "Център бус"),
            ("internal_bus", "Вътрешен бус"),
            ("external_bus", "Външен бус"),
            ("special_bus", "Специален бус"),
            ("vratza_bus", "Враца бус"),
        ]
        self.center_zone_priority_vars = {}
        self.center_zone_restricted_vars = {}
        for idx, (vehicle_type, label) in enumerate(vehicle_labels, start=1):
            ttk.Label(rules, text=label, style="Surface.TLabel").grid(row=idx, column=0, sticky="w", padx=(0, 12), pady=2)
            priority_var = tk.BooleanVar(value=(vehicle_type == "center_bus"))
            restricted_var = tk.BooleanVar(value=(vehicle_type != "center_bus"))
            ttk.Checkbutton(rules, variable=priority_var).grid(row=idx, column=1, sticky="w", padx=(0, 22), pady=2)
            ttk.Checkbutton(rules, variable=restricted_var).grid(row=idx, column=2, sticky="w", padx=(0, 8), pady=2)
            self.center_zone_priority_vars[vehicle_type] = priority_var
            self.center_zone_restricted_vars[vehicle_type] = restricted_var

        ttk.Label(form, text="Отстъпка", style="Surface.TLabel").grid(row=6, column=0, sticky="w", padx=(0, 8), pady=5)
        discount_var = tk.StringVar(value="0.9")
        discount_combo = ttk.Combobox(form, textvariable=discount_var, values=["0.7", "0.8", "0.9", "1.0"], width=10, state="normal")
        discount_combo.grid(row=6, column=1, sticky="w", padx=(0, 8), pady=5)
        self._bind_text_editing(discount_combo)
        self.widgets["locations.new_center_zone_discount"] = discount_var
        ttk.Label(form, text="0.9 = 10% по-ниска цена", style="Hint.TLabel").grid(row=6, column=2, sticky="w", pady=5)

        ttk.Label(form, text="Глоба навън", style="Surface.TLabel").grid(row=7, column=0, sticky="w", padx=(0, 8), pady=5)
        outside_var = tk.StringVar(value="0")
        outside_entry = ttk.Entry(form, textvariable=outside_var, width=12)
        outside_entry.grid(row=7, column=1, sticky="w", padx=(0, 8), pady=5)
        self._bind_text_editing(outside_entry)
        self.widgets["locations.new_center_zone_outside_penalty"] = outside_var

        ttk.Label(form, text="Глоба вътре", style="Surface.TLabel").grid(row=8, column=0, sticky="w", padx=(0, 8), pady=5)
        default_penalty = float(getattr(loc, "internal_bus_center_penalty", 40000.0) or 40000.0)
        penalty_var = tk.StringVar(value=f"{default_penalty:g}")
        penalty_entry = ttk.Entry(form, textvariable=penalty_var, width=12)
        penalty_entry.grid(row=8, column=1, sticky="w", padx=(0, 8), pady=5)
        self._bind_text_editing(penalty_entry)
        self.widgets["locations.new_center_zone_penalty"] = penalty_var

        buttons = ttk.Frame(form, style="Surface.TFrame")
        buttons.grid(row=9, column=1, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Добави / обнови", command=self._append_center_zone_from_fields).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Нова празна форма", command=self._clear_center_zone_form).pack(side="left")

        ttk.Label(
            list_frame,
            text="Избери зона, за да я заредиш във формата. После можеш да я обновиш или премахнеш.",
            style="Hint.TLabel",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 8))
        self.center_zone_listbox = tk.Listbox(list_frame, height=12, font=("Segoe UI", 9), exportselection=False)
        self.center_zone_listbox.grid(row=1, column=0, sticky="nsew")
        center_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.center_zone_listbox.yview)
        center_scroll.grid(row=1, column=1, sticky="ns")
        self.center_zone_listbox.configure(yscrollcommand=center_scroll.set)
        self.center_zone_listbox.bind("<<ListboxSelect>>", self._load_selected_center_zone)
        ttk.Button(list_frame, text="Премахни избраната", command=self._remove_selected_center_zone).grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )

        hidden_zones = tk.Text(box, width=1, height=1)
        self.widgets["locations.center_zones"] = hidden_zones
        self._sync_center_zone_widgets(getattr(loc, "center_zones", []) or [])

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
        self.depot_listbox = tk.Listbox(depot_list_frame, height=6, font=("Segoe UI", 9), exportselection=False)
        self.depot_listbox.grid(row=1, column=0, sticky="nsew")
        depot_scroll = ttk.Scrollbar(depot_list_frame, orient="vertical", command=self.depot_listbox.yview)
        depot_scroll.grid(row=1, column=1, sticky="ns")
        self.depot_listbox.configure(yscrollcommand=depot_scroll.set)
        self.depot_listbox.bind("<<ListboxSelect>>", self._load_selected_depot)
        ttk.Button(depot_list_frame, text="Премахни избраното", command=self._remove_selected_depot).grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )

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

        traffic_buttons = ttk.Frame(traffic_form, style="Surface.TFrame")
        traffic_buttons.grid(row=5, column=1, sticky="w", pady=(10, 0))
        ttk.Button(traffic_buttons, text="Добави / обнови", command=self._append_traffic_zone_from_fields).pack(side="left", padx=(0, 6))
        ttk.Button(traffic_buttons, text="Нова празна форма", command=self._clear_traffic_zone_form).pack(side="left")

        ttk.Label(
            traffic_list_frame,
            text="Избери зона, за да я заредиш във формата. Забавянето се записва като множител на времето.",
            style="Hint.TLabel",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 8))

        self.traffic_zone_listbox = tk.Listbox(traffic_list_frame, height=8, font=("Segoe UI", 9), exportselection=False)
        self.traffic_zone_listbox.grid(row=1, column=0, sticky="nsew")
        traffic_scroll = ttk.Scrollbar(traffic_list_frame, orient="vertical", command=self.traffic_zone_listbox.yview)
        traffic_scroll.grid(row=1, column=1, sticky="ns")
        self.traffic_zone_listbox.configure(yscrollcommand=traffic_scroll.set)
        self.traffic_zone_listbox.bind("<<ListboxSelect>>", self._load_selected_traffic_zone)
        ttk.Button(traffic_list_frame, text="Премахни избраната", command=self._remove_selected_traffic_zone).grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )

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
        self._add_field(maps, gr, "output.route_maps_upload_token", "Upload token:", getattr(out, "route_maps_upload_token", ""),
                         tooltip="Стойност за pData. За Effect endpoint-а е Effect-Bizante-Token."); gr += 1
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
        self._add_field(maps, gr, "output.google_maps_api_key", "Google Maps key:", out.google_maps_api_key,
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
        tsp_path = self._api_path_preview(api, "tsp_endpoint", "/tsp")
        health_path = self._api_path_preview(api, "health_endpoint", "/health")
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
            "Подрежда текущ маршрут за един шофьор от текущ GPS, optional крайна точка и списък клиенти; връща реда и генерира индивидуална карта.",
        )
        r = self._add_copyable_command(
            quick,
            r,
            "Run + настройки:",
            f'curl -X POST "{base_url}{trigger_path}" -H "Content-Type: application/json"{auth_header} -d "{{\\"settings\\":{{\\"solver_type\\":\\"pyvrp\\",\\"objective_metric\\":\\"time\\",\\"time_limit_seconds\\":180,\\"osrm_base_url\\":\\"http://localhost:5000\\",\\"vehicles\\":[{{\\"vehicle_type\\":\\"internal_bus\\",\\"count\\":7,\\"capacity\\":385}}],\\"output\\":{{\\"excel_output_dir\\":\\"H:\\\\\\\\Hell_Bizant_files\\\\\\\\Bizant_with_vratza\\",\\"routes_output_dir\\":\\"H:\\\\\\\\Hell_Bizant_files\\\\\\\\Bizant_with_vratza\\\\\\\\Routes\\"}},\\"set_data\\":{{\\"enable_set_data_upload\\":false}}}}}}"',
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
            tooltip="По желание. Ако е попълнен, /run и /solve искат header X-CVRP-API-Key.",
        )

        endpoints, r = self._add_group(
            f,
            "Endpoint-и",
            "Тези пътища могат да се сменят, ако друга система очаква конкретни имена.",
        )
        self._add_field(endpoints, r, "api.api_endpoint", "JSON solve:", getattr(api, "api_endpoint", "/solve"),
                        tooltip="POST с JSON клиенти. Сървърът чака решението и връща пълен резултат."); r += 1
        self._add_field(endpoints, r, "api.trigger_endpoint", "Старт без вход:", getattr(api, "trigger_endpoint", "/run"),
                        tooltip="GET/POST без body. Стартира програмата във фонова нишка."); r += 1
        self._add_field(endpoints, r, "api.tsp_endpoint", "Текущ TSP:", getattr(api, "tsp_endpoint", "/tsp"),
                        tooltip="POST. Подрежда текущ маршрут за един шофьор с текуща GPS позиция и optional крайна точка."); r += 1
        self._add_field(endpoints, r, "api.tsp_report_endpoint", "TSP отчет:", getattr(api, "tsp_report_endpoint", "/tsp-report"),
                        tooltip="GET/POST. Генерира Excel отчет за TSP маршрутите за деня."); r += 1
        self._add_field(endpoints, r, "api.shutdown_endpoint", "Спиране:", getattr(api, "shutdown_endpoint", "/shutdown"),
                        tooltip="GET/POST. Спира API сървъра/програмата. Ползвай API key при отдалечен достъп."); r += 1
        self._add_field(endpoints, r, "api.health_endpoint", "Статус:", getattr(api, "health_endpoint", "/health"),
                        tooltip="GET. Проверка дали API-то е живо и дали има активен run."); r += 1

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

        tsp_html, r = self._add_group(
            f,
            "TSP HTML и upload",
            "Тези настройки важат само за /tsp картите и не променят нормалните CVRP route карти.",
        )
        self._add_field(tsp_html, r, "api.tsp_generate_html_map", "Локална HTML карта:", getattr(api, "tsp_generate_html_map", True), "bool",
                        tooltip="Само за /tsp. Ако е изключено, TSP връща JSON реда, но не създава локален HTML файл и няма какво да качи."); r += 1
        self._add_field(tsp_html, r, "api.tsp_upload_html_map", "Качвай HTML карта:", getattr(api, "tsp_upload_html_map", True), "bool",
                        tooltip="Само за /tsp. Качването използва Output -> Route maps upload URL/token, но се включва/изключва отделно от нормалните route карти."); r += 1

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
            "API справочник",
            "Тук са отделени командите, JSON body примерите и всички settings полета, които могат да се подават временно.",
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
    Генерирането и качването на TSP HTML карта се управляват от отделните TSP checkbox-и в GUI.
  Връща:
    driver_id, ред на доставка, ETA, общи км/минути, HTML карта и upload статус, ако картите са включени.
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
      "excel_output_dir": "H:\\\\Hell_Bizant_files\\\\Bizant_with_vratza",
      "routes_output_dir": "H:\\\\Hell_Bizant_files\\\\Bizant_with_vratza\\\\Routes",
      "enable_csv_output": true,
      "csv_output_file": "H:\\\\Hell_Bizant_files\\\\Bizant_with_vratza\\\\routes.csv",
      "route_maps_upload_mode": "effect_upload",
      "route_maps_upload_url": "https://effect.bg/dragon/hellbizante/upload-files.php",
      "route_maps_upload_token": "Effect-Bizante-Token",
      "route_maps_upload_file_field": "files[]",
      "route_maps_upload_bus_id_field": "pData2[]"
    }},
    "set_data": {{
      "enable_set_data_upload": false,
      "set_data_url": "http://sio.effect.bg:7080/lubiv_Bizant",
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
    {{"IdCust": "1", "CustName": "Клиент", "GPS": "42.6977,23.3219", "Volume": 10, "WorkTime": "08:00 - 16:00"}}
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
      {{"vehicle_type": "internal_bus", "count": 7, "capacity": 385, "name": "HELL", "start_depot_name": "main", "end_location": [42.7000, 23.4000]}},
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
        "enabled": true
      }}
    ],
    "traffic_zones": [
      {{"name": "Center traffic", "center": [42.6977, 23.3219], "radius_km": 3.0, "multiplier": 1.3, "enabled": true}}
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
        self._add_field(connection, gr, "set_data.set_data_url", "URL:", getattr(set_data, "set_data_url", "http://sio.effect.bg:7080/lubiv_Bizant")); gr += 1
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
        ttk.Button(btn_frame, text="🔄 Провери статус", command=self._check_task_status).pack(side="left", padx=4)

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
        self.root.after(300, self._check_task_status)

    def _get_program_command(self):
        """Връща команда за стартиране, подходяща за schtasks и локално изпълнение"""
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            batch_path = os.path.join(exe_dir, "start_cvrp.bat")
            if os.path.isfile(batch_path):
                return f'cmd /c ""{batch_path}""', exe_dir
            return f'"{sys.executable}"', exe_dir

        exe = os.path.abspath(os.path.join(_base_dir, "..", "dist", "CVRP_Optimizer.exe"))
        if os.path.isfile(exe):
            batch_path = os.path.join(os.path.dirname(exe), "start_cvrp.bat")
            if os.path.isfile(batch_path):
                return f'cmd /c ""{batch_path}""', os.path.dirname(exe)
            return f'"{exe}"', os.path.dirname(exe)

        main_py = os.path.join(_base_dir, "main.py")
        return f'"{sys.executable}" "{main_py}"', _base_dir

    def _set_status(self, text):
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
            self._set_status(f"Задачата е създадена/обновена.\n"
                             f"Име: {task_name}\n"
                             f"Час: {time_str}\n"
                             f"Дни: {days_str}\n"
                             f"Команда: {prog}\n\n"
                             f"Важно: друга задача се презаписва само ако използва същото име.")
            messagebox.showinfo("Готово", f"Задачата '{task_name}' е създадена/обновена.")
        else:
            err = result.stderr.strip() or result.stdout.strip()
            self._set_status(f"Грешка при създаване:\n{err}")
            messagebox.showerror("Грешка", f"Не може да се създаде задачата:\n{err}")

    def _remove_scheduled_task(self):
        import subprocess
        task_name = self._get_scheduler_task_name()
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True, text=True, creationflags=0x08000000
        )
        if result.returncode == 0:
            self._set_status(f"Задачата '{task_name}' е премахната.")
            messagebox.showinfo("Готово", f"Задачата '{task_name}' е премахната.")
        else:
            err = result.stderr.strip() or result.stdout.strip()
            self._set_status(f"Грешка при премахване:\n{err}")

    def _check_task_status(self):
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
            self._set_status(f"Избраната задача съществува:\n{result.stdout.strip()}{all_tasks_text}")
        else:
            self._set_status(f"Избраната задача '{task_name}' не съществува.{all_tasks_text}")

    # ── Save logic ───────────────────────────────────────────

    def _collect_values(self) -> dict:
        """Събира стойности от всички уиджети"""
        values = {}
        for key, widget in self.widgets.items():
            if isinstance(widget, tk.Text):
                values[key] = widget.get("1.0", "end-1c")
            else:
                values[key] = widget.get()
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
            "input.gps_column": ("gps_column", "str"),
            "input.client_id_column": ("client_id_column", "str"),
            "input.client_name_column": ("client_name_column", "str"),
            "input.volume_column": ("volume_column", "str"),
            "input.document_column": ("document_column", "str"),
            "input.time_window_column": ("time_window_column", "str"),
            "input.delivery_comment_column": ("delivery_comment_column", "str"),
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
            "cvrp.pyvrp_ils_no_improvement": ("pyvrp_ils_no_improvement", "int"),
            "cvrp.pyvrp_ils_history_length": ("pyvrp_ils_history_length", "int"),
            "cvrp.pyvrp_use_extended_operators": ("pyvrp_use_extended_operators", "bool"),
            "cvrp.pyvrp_min_perturbations": ("pyvrp_min_perturbations", "int"),
            "cvrp.pyvrp_max_perturbations": ("pyvrp_max_perturbations", "int"),
            "cvrp.pyvrp_display_progress": ("pyvrp_display_progress", "bool"),
            # Locations
            "locations.center_zone_mode": ("center_zone_mode", "str"),
            "locations.center_zone_radius_km": ("center_zone_radius_km", "float"),
            "locations.enable_center_zone_priority": ("enable_center_zone_priority", "bool"),
            "locations.enable_center_zone_restrictions": ("enable_center_zone_restrictions", "bool"),
            "locations.discount_center_bus": ("discount_center_bus", "float"),
            "locations.center_bus_outside_center_penalty": ("center_bus_outside_center_penalty", "float"),
            "locations.internal_bus_center_penalty": ("internal_bus_center_penalty", "float"),
            "locations.external_bus_center_penalty": ("external_bus_center_penalty", "float"),
            "locations.special_bus_center_penalty": ("special_bus_center_penalty", "float"),
            "locations.vratza_bus_center_penalty": ("vratza_bus_center_penalty", "float"),
            "locations.enable_city_traffic_adjustment": ("enable_city_traffic_adjustment", "bool"),
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
            "api.tsp_endpoint": ("tsp_endpoint", "str"),
            "api.tsp_report_endpoint": ("tsp_report_endpoint", "str"),
            "api.shutdown_endpoint": ("shutdown_endpoint", "str"),
            "api.health_endpoint": ("health_endpoint", "str"),
            "api.tsp_default_service_time_minutes": ("tsp_default_service_time_minutes", "int"),
            "api.tsp_objective_metric": ("tsp_objective_metric", "str"),
            "api.tsp_use_time_windows": ("tsp_use_time_windows", "bool"),
            "api.tsp_time_window_wait_weight": ("tsp_time_window_wait_weight", "float"),
            "api.tsp_time_window_late_weight": ("tsp_time_window_late_weight", "float"),
            "api.tsp_enable_two_opt": ("tsp_enable_two_opt", "bool"),
            "api.tsp_two_opt_max_passes": ("tsp_two_opt_max_passes", "int"),
            "api.tsp_generate_html_map": ("tsp_generate_html_map", "bool"),
            "api.tsp_upload_html_map": ("tsp_upload_html_map", "bool"),
            "api.tsp_worker_timeout_seconds": ("tsp_worker_timeout_seconds", "int"),
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
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)
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
                pattern = rf'({field_name}\s*(?::\s*int\s*)?=\s*)-?\d+'
                content = re.sub(pattern, rf'\g<1>{val}', content)
            except (ValueError, TypeError):
                pass
        elif ftype == "optional_int":
            raw_text = str(raw_val).strip()
            pattern = rf'({field_name}\s*(?::\s*Optional\[int\]\s*)?=\s*)(?:None|-?\d+)'
            if raw_text == "" or raw_text.lower() == "none":
                content = re.sub(pattern, rf'\g<1>None', content)
            else:
                try:
                    val = int(raw_text)
                    content = re.sub(pattern, rf'\g<1>{val}', content)
                except (ValueError, TypeError):
                    pass
        elif ftype == "float":
            try:
                val = float(raw_val)
                pattern = rf'({field_name}\s*(?::\s*float\s*)?=\s*)[\d.]+'
                content = re.sub(pattern, rf'\g<1>{val}', content)
            except (ValueError, TypeError):
                pass
        elif ftype in ("str", "path"):
            escaped = str(raw_val).replace("\\", "\\\\").replace('"', '\\"')
            pattern = rf'({field_name}\s*(?::\s*str\s*)?=\s*(?:_abs_path\()?")[^"]*(")'
            content = re.sub(pattern, lambda match: f'{match.group(1)}{escaped}{match.group(2)}', content)
        elif ftype == "routing_engine":
            engine = str(raw_val).strip().upper()
            if engine in ("OSRM", "VALHALLA"):
                pattern = rf'({field_name}\s*:\s*RoutingEngine\s*=\s*)RoutingEngine\.[A-Z_]+'
                content = re.sub(pattern, rf'\g<1>RoutingEngine.{engine}', content)
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
            parts = [float(x.strip()) for x in raw_val.split(",")]
            if len(parts) == 2:
                new_tuple = f"({parts[0]}, {parts[1]})"
                pattern = rf'({field_name}\s*(?::\s*Tuple\[float,\s*float\]\s*)?=\s*)\([^)]+\)'
                content = re.sub(pattern, rf'\g<1>{new_tuple}', content)
        except ValueError:
            pass
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

        zones = self._parse_center_zones_text(raw_val)
        if zones:
            zone_blocks = []
            for zone in zones:
                polygon = getattr(zone, "polygon", []) or []
                polygon_block = "[" + ", ".join(f"({float(lat)}, {float(lon)})" for lat, lon in polygon) + "]"
                center = getattr(zone, "center_coords", None)
                center_block = f"({float(center[0])}, {float(center[1])})" if center else "None"
                priority = "[" + ", ".join(f'"{item}"' for item in (zone.priority_vehicle_types or [])) + "]"
                restricted = "[" + ", ".join(f'"{item}"' for item in (zone.restricted_vehicle_types or [])) + "]"
                penalties = "{" + ", ".join(
                    f'"{bus}": {float(value)}'
                    for bus, value in (zone.vehicle_penalties or {}).items()
                ) + "}"
                safe_name = zone.name.replace(chr(34), chr(92) + chr(34))
                zone_blocks.append(
                    "\n        ".join([
                        "CenterZoneConfig(",
                        f'    name="{safe_name}",',
                        f'    mode="{zone.mode}",',
                        f"    center_coords={center_block},",
                        f"    radius_km={float(zone.radius_km)},",
                        f"    polygon={polygon_block},",
                        f"    enabled={'True' if zone.enabled else 'False'},",
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
        return config.VehicleConfig(
            vehicle_type=vehicle_type,
            capacity=self._parse_int_value(values.get(f"{prefix}.capacity", 320), 320),
            count=max(0, self._parse_int_value(values.get(f"{prefix}.count", 1), 1)),
            name=str(values.get(f"{prefix}.name", "") or "").strip(),
            fixed_cost=self._parse_int_value(values.get(f"{prefix}.fixed_cost", 0), 0),
            max_distance_km=self._parse_optional_int_value(values.get(f"{prefix}.max_distance_km", "")),
            max_time_hours=self._parse_int_value(values.get(f"{prefix}.max_time_hours", 8), 8),
            service_time_minutes=self._parse_int_value(values.get(f"{prefix}.service_time_minutes", 8), 8),
            enabled=self._parse_bool_value(values.get(f"{prefix}.enabled", True)),
            max_customers_per_route=self._parse_optional_int_value(values.get(f"{prefix}.max_customers_per_route", "")),
            start_location=start_location,
            end_location=end_location,
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
            f"                fixed_cost={int(getattr(vehicle, 'fixed_cost', 0) or 0)},",
            f"                max_distance_km={self._optional_int_literal(vehicle.max_distance_km)},",
            f"                max_time_hours={int(vehicle.max_time_hours)},",
            f"                service_time_minutes={int(vehicle.service_time_minutes)},",
            f"                enabled={'True' if vehicle.enabled else 'False'},",
            f"                max_customers_per_route={self._optional_int_literal(vehicle.max_customers_per_route)},",
            f"                start_location={self._tuple_literal(vehicle.start_location)},",
            f"                end_location={self._tuple_literal(getattr(vehicle, 'end_location', None))},",
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
        if field in ("start_location", "end_location"):
            return re.sub(insert_pattern, f"\n                {field}={new_val},\\1", block, count=1)

        insert_pattern = r'(\n\s*\)\s*,?)'
        return re.sub(insert_pattern, f"\n                {field}={new_val},\\1", block, count=1)

    def _replace_vehicle_field(self, content, vtype, idx, values, prefix):
        """Замества полета на превозно средство в config.py"""
        import re

        # Намираме блока на VehicleConfig за този тип
        vehicle_fields = {
            "name": "str",
            "enabled": "bool",
            "count": "int",
            "fixed_cost": "int",
            "capacity": "int",
            "max_time_hours": "int",
            "service_time_minutes": "int",
            "max_customers_per_route": "optional_int",
            "start_depot_name": "depot_choice",
            "end_location": "optional_tuple",
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
                    block = re.sub(rf'({field}\s*=\s*)\d+', rf'\g<1>{val}', block)
                except (ValueError, TypeError):
                    pass
            elif ftype == "optional_int":
                raw_str = str(raw).strip()
                if raw_str == "" or raw_str.lower() == "none":
                    block = re.sub(rf'({field}\s*=\s*)(?:None|\d+)', rf'\g<1>None', block)
                else:
                    try:
                        val = int(raw_str)
                        block = re.sub(rf'({field}\s*=\s*)(?:None|\d+)', rf'\g<1>{val}', block)
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

        content = content[:block_start] + block + content[block_end:]
        return content

    # ── Actions ──────────────────────────────────────────────

    def _save(self):
        try:
            self.status_var.set("Записвам настройките...")
            self.root.update_idletasks()
            values = self._collect_values()
            changed = self._apply_to_config_file(values)
            if changed:
                self.status_var.set("Запазено в config.py")
                messagebox.showinfo("Запазено", "Настройките са записани в config.py")
            else:
                self.status_var.set("Няма промени за запис")
                messagebox.showinfo("Без промени", "Няма промени за записване.")
        except Exception as e:
            self.status_var.set("Грешка при запис")
            messagebox.showerror("Грешка", f"Грешка при запис: {e}")

    def _save_and_close(self):
        try:
            self.status_var.set("Записвам настройките...")
            self.root.update_idletasks()
            values = self._collect_values()
            self._apply_to_config_file(values)
            self.root.destroy()
        except Exception as e:
            self.status_var.set("Грешка при запис")
            messagebox.showerror("Грешка", f"Грешка при запис: {e}")

    def _save_and_run(self):
        try:
            self.status_var.set("Записвам и стартирам...")
            self.root.update_idletasks()
            values = self._collect_values()
            self._apply_to_config_file(values)
        except Exception as e:
            self.status_var.set("Грешка при запис")
            messagebox.showerror("Грешка", f"Грешка при запис: {e}")
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
