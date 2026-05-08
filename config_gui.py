"""
Опростен графичен интерфейс за редактиране на config.py
Позволява промяна на най-важните настройки без ръчна редакция на файла.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os
import importlib
import json
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
        self.root.geometry("1040x760")
        self.root.minsize(920, 640)
        self.root.resizable(True, True)
        self._configure_style()

        # Зареждаме текущата конфигурация
        importlib.reload(config)
        self.cfg = config.get_config()
        self.widgets = {}  # field_key → widget

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────

    def _configure_style(self):
        self.root.configure(bg="#f4f6f8")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.root.option_add("*Font", ("Segoe UI", 9))
        style.configure("TFrame", background="#f4f6f8")
        style.configure("Toolbar.TFrame", background="#ffffff")
        style.configure("Surface.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f4f6f8", foreground="#1f2933")
        style.configure("Surface.TLabel", background="#ffffff", foreground="#1f2933")
        style.configure("Hint.TLabel", background="#ffffff", foreground="#667085", font=("Segoe UI", 8))
        style.configure("AppTitle.TLabel", background="#ffffff", foreground="#111827", font=("Segoe UI", 13, "bold"))
        style.configure("AppSubtitle.TLabel", background="#ffffff", foreground="#667085", font=("Segoe UI", 8))
        style.configure("TLabelframe", background="#ffffff", bordercolor="#d0d5dd", relief="solid")
        style.configure("TLabelframe.Label", background="#ffffff", foreground="#111827", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(10, 5))
        style.configure("Primary.TButton", padding=(12, 6))
        style.configure("TNotebook", background="#f4f6f8", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 6))

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
        nb.pack(fill="both", expand=True, padx=10, pady=10)

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
        self._add_api_tab(nb)
        self._add_set_data_tab(nb)

        # ─── Tab 7: Планировчик ───
        self._add_scheduler_tab(nb)

    # ── Helpers ──────────────────────────────────────────────

    def _make_scrollable_frame(self, parent):
        shell = ttk.Frame(parent)
        shell.pack(fill="both", expand=True)

        canvas = tk.Canvas(shell, highlightthickness=0, background="#f4f6f8", borderwidth=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        frame = ttk.Frame(canvas, padding=(14, 12))
        window_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
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

    def _add_field(self, parent, row, key, label, value, field_type="str", options=None, tooltip=""):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, anchor="w", style="Surface.TLabel").grid(
            row=row, column=0, sticky="w", padx=(8, 10), pady=5
        )

        if field_type == "bool":
            var = tk.BooleanVar(value=value)
            cb = ttk.Checkbutton(parent, variable=var)
            cb.grid(row=row, column=1, sticky="w", padx=6, pady=5)
            self.widgets[key] = var
        elif field_type == "combo" and options:
            var = tk.StringVar(value=str(value))
            combo = ttk.Combobox(parent, textvariable=var, values=options, state="readonly", width=32)
            combo.grid(row=row, column=1, sticky="w", padx=6, pady=5)
            self.widgets[key] = var
        else:
            var = tk.StringVar(value=str(value))
            entry = ttk.Entry(parent, textvariable=var, width=44)
            entry.grid(row=row, column=1, sticky="we", padx=6, pady=5)
            self.widgets[key] = var

        if tooltip:
            ttk.Label(parent, text=tooltip, style="Hint.TLabel", wraplength=260).grid(
                row=row, column=2, sticky="w", padx=(8, 4), pady=5
            )

    def _add_group(self, parent, title, hint=""):
        group = ttk.LabelFrame(parent, text=title, padding=(12, 10))
        group.pack(fill="x", expand=True, padx=2, pady=(0, 12))
        group.columnconfigure(1, weight=1)
        row = 0
        if hint:
            ttk.Label(group, text=hint, style="Hint.TLabel", wraplength=760).grid(
                row=row, column=0, columnspan=3, sticky="we", padx=8, pady=(0, 8)
            )
            row += 1
        return group, row

    def _add_list_field(self, parent, row, key, label, values_list, options=None, tooltip=""):
        """Добавя поле за списък от стойности (по една на ред в Text widget)"""
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, anchor="w", style="Surface.TLabel").grid(
            row=row, column=0, sticky="nw", padx=(8, 10), pady=5
        )
        text_val = "\n".join(str(v) for v in values_list)
        text_w = tk.Text(parent, width=42, height=max(3, len(values_list)), wrap="none", relief="solid", borderwidth=1)
        text_w.insert("1.0", text_val)
        text_w.grid(row=row, column=1, sticky="we", padx=6, pady=5)
        self.widgets[key] = text_w
        hint = tooltip or "По един елемент на ред"
        ttk.Label(parent, text=hint, style="Hint.TLabel", wraplength=260).grid(
            row=row, column=2, sticky="nw", padx=(8, 4), pady=5
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

    def _add_depot_choice_field(self, parent, row, key, label, value, options, tooltip=""):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, anchor="w", style="Surface.TLabel").grid(
            row=row, column=0, sticky="w", padx=(8, 10), pady=5
        )
        var = tk.StringVar(value=value)
        combo = ttk.Combobox(parent, textvariable=var, values=options, state="normal", width=32)
        combo.grid(row=row, column=1, sticky="w", padx=6, pady=5)
        self.widgets[key] = var
        if tooltip:
            ttk.Label(parent, text=tooltip, style="Hint.TLabel", wraplength=260).grid(
                row=row, column=2, sticky="w", padx=(8, 4), pady=5
            )

    def _add_polygon_field(self, parent, row, key, label, polygon):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, anchor="w", style="Surface.TLabel").grid(
            row=row, column=0, sticky="nw", padx=(8, 10), pady=5
        )
        text_w = tk.Text(parent, width=42, height=6, wrap="none", relief="solid", borderwidth=1)
        text_w.insert("1.0", self._format_polygon_text(polygon or []))
        text_w.grid(row=row, column=1, sticky="we", padx=6, pady=5)
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

    def _open_center_zone_editor(self):
        center_raw = self.widgets.get("locations.center_location").get()
        try:
            center = [float(part.strip()) for part in center_raw.split(",")[:2]]
        except Exception:
            center = [42.69735652560932, 23.323809998750914]

        polygon_widget = self.widgets.get("locations.center_zone_polygon")
        polygon_raw = polygon_widget.get("1.0", "end-1c") if isinstance(polygon_widget, tk.Text) else ""
        polygon = self._parse_polygon_text(polygon_raw)

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
                    gui.root.after(0, lambda: gui._set_center_zone_polygon(saved_polygon))
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

    # ── Tab: Входни данни ────────────────────────────────────

    def _add_input_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 📥 Входни данни ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        inp = self.cfg.input
        r = 0
        self._add_field(f, r, "input.input_source", "Източник на данни:", inp.input_source,
                         "combo", ["excel", "http_json"]); r += 1
        self._add_field(f, r, "input.excel_file_path", "Excel файл:", inp.excel_file_path); r += 1
        self._add_field(f, r, "input.json_url", "JSON URL:", inp.json_url); r += 1
        self._add_field(f, r, "input.json_http_method", "JSON HTTP метод:", getattr(inp, "json_http_method", "GET"),
                         "combo", ["GET", "POST"]); r += 1
        self._add_field(f, r, "input.json_command", "cmd:", getattr(inp, "json_command", "getData")); r += 1
        self._add_field(f, r, "input.json_date_field", "JSON поле за дата:", getattr(inp, "json_date_field", "date")); r += 1
        self._add_field(f, r, "input.json_sklad", "Sklad:", getattr(inp, "json_sklad", "106")); r += 1
        self._add_field(f, r, "input.json_done_flag", "DoneFlag:", getattr(inp, "json_done_flag", "1973")); r += 1
        self._add_field(f, r, "input.json_extra_query", "Допълнителни GET параметри:", getattr(inp, "json_extra_query", ""),
                         tooltip="Формат: key=value&key2=value2. По желание."); r += 1
        self._add_field(f, r, "input.json_override_date", "Конкретна дата (DD/MM/YYYY):", inp.json_override_date,
                         tooltip="Празно = автоматично следващ работен ден"); r += 1
        self._add_field(f, r, "input.json_timeout_seconds", "HTTP таймаут (сек):", inp.json_timeout_seconds); r += 1

        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="we", pady=8); r += 1
        ttk.Label(f, text="Картографиране на JSON полета:", font=("", 9, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", padx=6); r += 1
        self._add_field(f, r, "input.json_gps_field", "GPS поле:", inp.json_gps_field); r += 1
        self._add_field(f, r, "input.json_client_id_field", "Клиентски номер:", inp.json_client_id_field); r += 1
        self._add_field(f, r, "input.json_client_name_field", "Име на клиент:", inp.json_client_name_field); r += 1
        self._add_field(f, r, "input.json_volume_field", "Обем (стекове):", inp.json_volume_field); r += 1
        self._add_field(f, r, "input.json_document_field", "Номер документ:", inp.json_document_field); r += 1
        self._add_field(f, r, "input.json_plas_doc_field", "IdPlasDoc поле:", getattr(inp, "json_plas_doc_field", "IdPlasDoc")); r += 1
        self._add_field(f, r, "input.json_id_skld_field", "IdSkld поле:", getattr(inp, "json_id_skld_field", "IdSkld")); r += 1

        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="we", pady=8); r += 1
        ttk.Label(f, text="Картографиране на Excel колони:", font=("", 9, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", padx=6); r += 1
        self._add_field(f, r, "input.gps_column", "GPS колона:", inp.gps_column); r += 1
        self._add_field(f, r, "input.client_id_column", "ID колона:", inp.client_id_column); r += 1
        self._add_field(f, r, "input.client_name_column", "Име колона:", inp.client_name_column); r += 1
        self._add_field(f, r, "input.volume_column", "Обем колона:", inp.volume_column); r += 1
        self._add_field(f, r, "input.document_column", "Документ колона:", inp.document_column); r += 1

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
            ttk.Label(f, text=f"── {header} ──", font=("", 10, "bold")).grid(
                row=r, column=0, columnspan=3, sticky="w", padx=6, pady=(10, 2)); r += 1

            prefix = f"vehicle.{i}"
            self._add_field(f, r, f"{prefix}.enabled", "Активен:", v.enabled, "bool"); r += 1
            self._add_field(f, r, f"{prefix}.count", "Брой:", v.count); r += 1
            self._add_field(f, r, f"{prefix}.fixed_cost", "Цена за използване:", getattr(v, "fixed_cost", 40000),
                            tooltip="Глоба за всеки използван бус. 40000 е приблизително като 40 км."); r += 1
            self._add_field(f, r, f"{prefix}.capacity", "Капацитет (ст.):", v.capacity); r += 1
            self._add_field(f, r, f"{prefix}.max_time_hours", "Макс. време (ч.):", v.max_time_hours); r += 1
            self._add_field(f, r, f"{prefix}.service_time_minutes", "Обслужване (мин):", v.service_time_minutes); r += 1
            self._add_field(f, r, f"{prefix}.max_customers_per_route", "Макс. клиенти:",
                             v.max_customers_per_route if v.max_customers_per_route else "",
                             tooltip="Празно = без ограничение"); r += 1
            self._add_depot_choice_field(f, r, f"{prefix}.start_depot_name", "Депо тръгване:",
                                         self._depot_name_for_coords(v.start_location),
                                         depot_options,
                                         tooltip="Избери депо по име. Ако добавиш ново депо, можеш да напишеш името му тук след запис/презареждане."); r += 1
            self._add_field(f, r, f"{prefix}.start_time_minutes", "Старт (мин от 00:00):", v.start_time_minutes,
                             tooltip="480 = 08:00"); r += 1

    # ── Tab: Склад ───────────────────────────────────────────

    def _add_warehouse_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 🏭 Предварителна оптимизация")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        wh = self.cfg.warehouse
        r = 0
        self._add_field(f, r, "warehouse.enable_warehouse", "Включен:", wh.enable_warehouse, "bool"); r += 1
        self._add_field(f, r, "warehouse.sort_by_volume", "Сортиране по обем:", wh.sort_by_volume, "bool"); r += 1
        self._add_field(f, r, "warehouse.sort_by_distance", "Сортиране по разстояние:", wh.sort_by_distance, "bool"); r += 1
        self._add_field(f, r, "warehouse.check_max_bus_capacity", "Проверка макс. капацитет:", wh.check_max_bus_capacity, "bool"); r += 1
        self._add_field(f, r, "warehouse.max_bus_customer_volume", "Макс. обем на клиент (ст.):", wh.max_bus_customer_volume); r += 1
        self._add_field(f, r, "warehouse.capacity_toleranse", "Толеранс капацитет:", wh.capacity_toleranse); r += 1

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
        self._add_field(basic, r, "cvrp.time_limit_seconds", "Време за решение (сек):", c.time_limit_seconds); r += 1

        routing, r = self._add_group(
            f,
            "Routing",
            "Настройки за пътната матрица, която се подава към solver-а.",
        )
        routing_cfg = self.cfg.routing
        self._add_field(routing, r, "routing.enable_curbside_approach", "Спазвай страна на улицата:", getattr(routing_cfg, "enable_curbside_approach", False), "bool",
                        tooltip="При OSRM добавя approaches=curb. При Valhalla добавя preferred_side към локациите."); r += 1
        self._add_field(routing, r, "routing.valhalla_preferred_side", "Valhalla preferred side:", getattr(routing_cfg, "valhalla_preferred_side", "same"),
                         "combo", ["same", "either", "opposite"],
                         tooltip="same = клиентът да е от страната на движение. За България това обикновено значи отдясно."); r += 1

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

        ortools, r = self._add_group(
            f,
            "OR-Tools настройки",
            "Тези параметри се използват при OR-Tools режима и при паралелни OR-Tools стратегии.",
        )
        self._add_field(ortools, r, "cvrp.first_solution_strategy", "First solution:", c.first_solution_strategy,
                         "combo", ["AUTOMATIC", "PATH_CHEAPEST_ARC", "SAVINGS", "SWEEP", "CHRISTOFIDES",
                                   "PARALLEL_CHEAPEST_INSERTION"]); r += 1
        self._add_field(ortools, r, "cvrp.local_search_metaheuristic", "Метаевристика:", c.local_search_metaheuristic,
                         "combo", ["AUTOMATIC", "GUIDED_LOCAL_SEARCH", "SIMULATED_ANNEALING", "TABU_SEARCH"]); r += 1
        self._add_field(ortools, r, "cvrp.enable_start_time_tracking", "Проследяване старт. време:", c.enable_start_time_tracking, "bool"); r += 1
        self._add_field(ortools, r, "cvrp.global_start_time_minutes", "Глобално старт. време (мин):", c.global_start_time_minutes,
                         tooltip="480 = 08:00"); r += 1

        parallel, r = self._add_group(
            f,
            "Паралелно търсене",
            "Пуска няколко OR-Tools стратегии и избира най-добрия резултат.",
        )
        self._add_field(parallel, r, "cvrp.enable_parallel_solving", "Включено:", c.enable_parallel_solving, "bool"); r += 1
        self._add_field(parallel, r, "cvrp.num_workers", "Брой процеси:", c.num_workers,
                         tooltip="-1 = всички ядра без едно"); r += 1
        self._add_list_field(parallel, r, "cvrp.parallel_first_solution_strategies",
                             "First solution стратегии:", c.parallel_first_solution_strategies); r += 1
        self._add_list_field(parallel, r, "cvrp.parallel_local_search_metaheuristics",
                             "Метаевристики:", c.parallel_local_search_metaheuristics); r += 1

    # ── Tab: Локации ─────────────────────────────────────────

    def _add_locations_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 📍 Локации ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        loc = self.cfg.locations
        r = 0
        self._add_field(f, r, "locations.depot_location", "Главно депо (lat, lon):",
                         f"{loc.depot_location[0]}, {loc.depot_location[1]}"); r += 1
        self._add_field(f, r, "locations.center_location", "Център (lat, lon):",
                         f"{loc.center_location[0]}, {loc.center_location[1]}"); r += 1
        self._add_field(f, r, "locations.vratza_depot_location", "Враца депо (lat, lon):",
                         f"{loc.vratza_depot_location[0]}, {loc.vratza_depot_location[1]}"); r += 1
        self._add_list_field(
            f,
            r,
            "locations.depot_locations",
            "Допълнителни депа:",
            self._format_depots_text(getattr(loc, "depot_locations", {}) or {}).splitlines(),
            tooltip="Формат: Име: lat, lon. След запис/презареждане депото ще е налично за избор при бусове.",
        ); r += 1
        self._add_field(f, r, "locations.center_zone_mode", "Тип център зона:", getattr(loc, "center_zone_mode", "circle"),
                         "combo", ["circle", "polygon"]); r += 1
        self._add_field(f, r, "locations.center_zone_radius_km", "Радиус център зона (км):", loc.center_zone_radius_km); r += 1
        self._add_polygon_field(f, r, "locations.center_zone_polygon", "Начертана център зона:", getattr(loc, "center_zone_polygon", [])); r += 1
        self._add_field(f, r, "locations.enable_center_zone_priority", "Приоритет център зона:", loc.enable_center_zone_priority, "bool"); r += 1
        self._add_field(f, r, "locations.enable_center_zone_restrictions", "Ограничения за център:", loc.enable_center_zone_restrictions, "bool"); r += 1
        self._add_field(f, r, "locations.discount_center_bus", "Отстъпка CENTER_BUS:", loc.discount_center_bus,
                         tooltip="0.5 = плаща 50% от разстоянието"); r += 1
        self._add_field(f, r, "locations.center_bus_outside_center_penalty", "Глоба CENTER_BUS извън център:", getattr(loc, "center_bus_outside_center_penalty", 50000.0),
                         tooltip="Добавя се към цената, когато CENTER_BUS обслужва клиент извън център зоната."); r += 1

        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="we", pady=8); r += 1
        ttk.Label(f, text="Глоби за влизане в център зоната:", font=("", 9, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", padx=6); r += 1
        self._add_field(f, r, "locations.internal_bus_center_penalty", "Вътрешен бус:", loc.internal_bus_center_penalty); r += 1
        self._add_field(f, r, "locations.external_bus_center_penalty", "Външен бус:", loc.external_bus_center_penalty); r += 1
        self._add_field(f, r, "locations.special_bus_center_penalty", "Специален бус:", loc.special_bus_center_penalty); r += 1
        self._add_field(f, r, "locations.vratza_bus_center_penalty", "Враца бус:", loc.vratza_bus_center_penalty); r += 1

        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="we", pady=8); r += 1
        ttk.Label(f, text="Градски трафик:", font=("", 9, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", padx=6); r += 1
        self._add_field(f, r, "locations.enable_city_traffic_adjustment", "Корекция за трафик:", loc.enable_city_traffic_adjustment, "bool"); r += 1
        self._add_field(f, r, "locations.city_center_coords", "Център на трафик зона (lat, lon):",
                         f"{loc.city_center_coords[0]}, {loc.city_center_coords[1]}"); r += 1
        self._add_field(f, r, "locations.city_traffic_radius_km", "Радиус трафик (км):", loc.city_traffic_radius_km); r += 1
        self._add_field(f, r, "locations.city_traffic_duration_multiplier", "Множител за време:", loc.city_traffic_duration_multiplier,
                         tooltip="1.4 = +40% заради трафик"); r += 1

    # ── Tab: Изходни данни ───────────────────────────────────

    def _add_output_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 📄 Изходни данни ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        out = self.cfg.output
        r = 0

        ttk.Label(f, text="Карта:", font=("", 9, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", padx=6); r += 1
        self._add_field(f, r, "output.enable_interactive_map", "Генериране на карта:", out.enable_interactive_map, "bool"); r += 1
        self._add_field(f, r, "output.map_output_file", "Файл карта:", out.map_output_file); r += 1
        self._add_field(f, r, "output.routes_output_dir", "Директория HTML маршрути:", out.routes_output_dir,
                         tooltip="Отделни HTML файлове за всеки маршрут"); r += 1
        self._add_field(f, r, "output.map_provider", "Map provider:", out.map_provider,
                         tooltip='google за Google Maps визуализация или osm за стария Folium/OpenStreetMap режим'); r += 1
        self._add_field(f, r, "output.folium_tiles", "Folium tiles:", out.folium_tiles,
                         tooltip='Например "Esri.WorldStreetMap", "Esri.WorldTopoMap", "CartoDB Voyager"'); r += 1
        self._add_field(f, r, "output.google_maps_api_key", "Google Maps API key:", out.google_maps_api_key,
                         tooltip="Ключ само за Maps JavaScript API. Може и чрез GOOGLE_MAPS_API_KEY env variable."); r += 1

        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="we", pady=8); r += 1
        ttk.Label(f, text="Excel:", font=("", 9, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", padx=6); r += 1
        self._add_field(f, r, "output.enable_excel_output", "Генериране на Excel отчет:", getattr(out, "enable_excel_output", True), "bool"); r += 1
        self._add_field(f, r, "output.excel_output_dir", "Директория Excel:", out.excel_output_dir); r += 1
        self._add_field(f, r, "output.routes_excel_file", "Файл маршрути:", out.routes_excel_file); r += 1
        self._add_field(f, r, "output.warehouse_excel_file", "Файл склад:", out.warehouse_excel_file); r += 1
        self._add_field(f, r, "output.efficiency_excel_file", "Файл ефективност:", out.efficiency_excel_file); r += 1
        self._add_field(f, r, "output.excel_bus_number_prefix", "Префикс номер бус:", getattr(out, "excel_bus_number_prefix", "10045010")); r += 1
        self._add_field(f, r, "output.excel_bus_number_digits", "Цифри след префикса:", getattr(out, "excel_bus_number_digits", 2), "int"); r += 1

        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="we", pady=8); r += 1
        ttk.Label(f, text="CSV:", font=("", 9, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", padx=6); r += 1
        self._add_field(f, r, "output.enable_csv_output", "Генериране на CSV:", getattr(out, "enable_csv_output", True), "bool"); r += 1
        self._add_field(f, r, "output.csv_output_file", "Файл CSV:", out.csv_output_file); r += 1

        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="we", pady=8); r += 1
        ttk.Label(f, text="Графики:", font=("", 9, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", padx=6); r += 1
        self._add_field(f, r, "output.enable_charts", "Генериране на графики:", out.enable_charts, "bool"); r += 1
        self._add_field(f, r, "output.charts_output_dir", "Директория графики:", out.charts_output_dir); r += 1

    # ── Tab: Планировчик ────────────────────────────────────

    # API server settings

    def _add_api_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" API сървър ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        api = getattr(self.cfg, "api", None)
        r = 0

        self._add_field(
            f,
            r,
            "api.api_host",
            "Адрес за слушане:",
            getattr(api, "api_host", "0.0.0.0"),
            tooltip="0.0.0.0 = приема заявки от други компютри. 127.0.0.1 = само локално.",
        ); r += 1
        self._add_field(f, r, "api.api_port", "Порт:", getattr(api, "api_port", 8088)); r += 1
        self._add_field(
            f,
            r,
            "api.api_public_url",
            "URL за извикване:",
            getattr(api, "api_public_url", ""),
            tooltip="Празно = автоматично от адреса и порта. Пример: http://10.10.100.134:8088 или https://domain.com/cvrp.",
        ); r += 1
        self._add_field(f, r, "api.api_endpoint", "POST endpoint:", getattr(api, "api_endpoint", "/solve")); r += 1
        self._add_field(f, r, "api.health_endpoint", "Health endpoint:", getattr(api, "health_endpoint", "/health")); r += 1

    def _add_set_data_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" setData ")
        f = self._make_scrollable_frame(tab)
        f.columnconfigure(1, weight=1)
        set_data = getattr(self.cfg, "set_data", None)
        r = 0

        self._add_field(f, r, "set_data.enable_set_data_upload", "Изпращай setData:", getattr(set_data, "enable_set_data_upload", False), "bool",
                        tooltip="Когато е включено, след успешно решение изпраща по една setData заявка за всеки обслужен клиент."); r += 1
        self._add_field(f, r, "set_data.set_data_url", "setData URL:", getattr(set_data, "set_data_url", "http://sio.effect.bg:7080/lubiv_Bizant")); r += 1
        self._add_field(f, r, "set_data.set_data_http_method", "HTTP метод:", getattr(set_data, "set_data_http_method", "GET"),
                         "combo", ["GET", "POST"]); r += 1
        self._add_field(f, r, "set_data.set_data_command", "cmd:", getattr(set_data, "set_data_command", "setData")); r += 1
        self._add_field(f, r, "set_data.set_data_done_flag", "DoneFlag:", getattr(set_data, "set_data_done_flag", "1973")); r += 1
        self._add_field(f, r, "set_data.set_data_id_skld", "IdSkld основно депо:", getattr(set_data, "set_data_id_skld", "128")); r += 1
        self._add_field(f, r, "set_data.set_data_vratza_id_skld", "IdSkld Враца:", getattr(set_data, "set_data_vratza_id_skld", "106")); r += 1
        self._add_field(
            f,
            r,
            "set_data.set_data_depot_id_skld_map",
            "Корекции по депо:",
            getattr(set_data, "set_data_depot_id_skld_map", "Главно депо=128;Враца=106"),
            tooltip="Формат: Главно депо=128;Враца=106. Имената са същите като депата в таб Локации.",
        ); r += 1
        self._add_field(f, r, "set_data.set_data_id_grafik", "IdGrafik:", getattr(set_data, "set_data_id_grafik", "")); r += 1
        self._add_field(
            f,
            r,
            "set_data.set_data_id_grafik_template",
            "IdGrafik шаблон:",
            getattr(set_data, "set_data_id_grafik_template", "{bus_number}"),
            tooltip="Default {bus_number} = номерът на буса от Excel, напр. 1004501001.",
        ); r += 1
        self._add_field(
            f,
            r,
            "set_data.set_data_bukva_template",
            "Bukva шаблон:",
            getattr(set_data, "set_data_bukva_template", "БХ{route_number}-{stop_number}"),
            tooltip="Позволени: {bus_number}, {route_number}, {stop_number}, {vehicle_type}, {customer_id}, {customer_document}, {id_skld}, {id_grafik}, {done_flag}.",
        ); r += 1
        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="we", pady=8); r += 1
        ttk.Label(f, text="Необслужени клиенти:", font=("", 9, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", padx=6); r += 1
        self._add_field(f, r, "set_data.enable_unserved_set_data_upload", "Изпращай необслужени:", getattr(set_data, "enable_unserved_set_data_upload", True), "bool",
                        tooltip="Праща към setData и клиентите за склад/пропуснатите. IdSkld се взима от GET полето IdSkld на клиента."); r += 1
        self._add_field(f, r, "set_data.set_data_unserved_id_grafik", "IdGrafik необслужени:", getattr(set_data, "set_data_unserved_id_grafik", "")); r += 1
        self._add_field(
            f,
            r,
            "set_data.set_data_unserved_id_grafik_template",
            "IdGrafik шаблон необслужени:",
            getattr(set_data, "set_data_unserved_id_grafik_template", "{id_grafik}"),
            tooltip="Позволени: {id_grafik}, {customer_id}, {customer_document}, {id_plas_doc}, {id_skld}, {stop_number}.",
        ); r += 1
        self._add_field(
            f,
            r,
            "set_data.set_data_unserved_bukva_template",
            "Bukva шаблон необслужени:",
            getattr(set_data, "set_data_unserved_bukva_template", "HOF-{id_plas_doc}"),
            tooltip="Позволени: {id_grafik}, {customer_id}, {customer_document}, {id_plas_doc}, {id_skld}, {stop_number}.",
        ); r += 1
        self._add_field(f, r, "set_data.enable_make_group", "Изпращай makeGroup:", getattr(set_data, "enable_make_group", True), "bool"); r += 1
        self._add_field(f, r, "set_data.set_data_make_group_command", "makeGroup cmd:", getattr(set_data, "set_data_make_group_command", "makeGroup")); r += 1
        self._add_field(f, r, "set_data.set_data_timeout_seconds", "Таймаут (сек):", getattr(set_data, "set_data_timeout_seconds", 30)); r += 1

    TASK_PREFIX = "CVRP_Optimizer_Auto"

    def _add_scheduler_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 🕒 Автоматично стартиране ")
        f = ttk.Frame(tab)
        f.pack(fill="both", expand=True, padx=12, pady=12)
        f.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(f, text="Автоматично стартиране:", font=("", 10, "bold")).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1

        ttk.Label(f, text="Име на задача:", anchor="w").grid(row=r, column=0, sticky="w", pady=3)
        self._sched_task_name = tk.StringVar(value=f"{self.TASK_PREFIX}_1")
        ttk.Entry(f, textvariable=self._sched_task_name, width=32).grid(row=r, column=1, sticky="w", pady=3)
        r += 1

        ttk.Label(f, text="Час (ЧЧ:ММ):", anchor="w").grid(row=r, column=0, sticky="w", pady=3)
        self._sched_time = tk.StringVar(value="17:01")
        ttk.Entry(f, textvariable=self._sched_time, width=8).grid(row=r, column=1, sticky="w", pady=3)
        r += 1

        ttk.Label(f, text="Дни:", anchor="nw").grid(row=r, column=0, sticky="nw", pady=3)
        days_frame = ttk.Frame(f)
        days_frame.grid(row=r, column=1, sticky="w", pady=3)
        self._sched_days = {}
        day_names = [("ПН", "MON"), ("ВТ", "TUE"), ("СР", "WED"),
                     ("ЧТ", "THU"), ("ПТ", "FRI"), ("СБ", "SAT"), ("НД", "SUN")]
        for i, (bg, en) in enumerate(day_names):
            var = tk.BooleanVar(value=(i < 5))  # Mon-Fri by default
            cb = ttk.Checkbutton(days_frame, text=bg, variable=var)
            cb.pack(side="left", padx=3)
            self._sched_days[en] = var
        r += 1

        r += 1  # spacer
        btn_frame = ttk.Frame(f)
        btn_frame.grid(row=r, column=0, columnspan=2, sticky="w", pady=12)
        ttk.Button(btn_frame, text="✅ Създай задача", command=self._create_scheduled_task).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="🗑️ Премахни задача", command=self._remove_scheduled_task).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="🔄 Провери статус", command=self._check_task_status).pack(side="left", padx=4)
        r += 1

        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=2, sticky="we", pady=8); r += 1
        self._sched_status = tk.Text(f, width=70, height=8, wrap="word", state="disabled",
                                      background="#f5f5f5")
        self._sched_status.grid(row=r, column=0, columnspan=2, sticky="we", pady=4)
        r += 1

        ttk.Label(f, text="Използва Windows Task Scheduler (schtasks). За повече задачи използвай различни имена.",
                  foreground="gray", font=("", 8)).grid(row=r, column=0, columnspan=2, sticky="w")

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
            "input.gps_column": ("gps_column", "str"),
            "input.client_id_column": ("client_id_column", "str"),
            "input.client_name_column": ("client_name_column", "str"),
            "input.volume_column": ("volume_column", "str"),
            "input.document_column": ("document_column", "str"),
            # Routing
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
            "cvrp.enable_start_time_tracking": ("enable_start_time_tracking", "bool"),
            "cvrp.global_start_time_minutes": ("global_start_time_minutes", "int"),
            "cvrp.enable_parallel_solving": ("enable_parallel_solving", "bool"),
            "cvrp.num_workers": ("num_workers", "int"),
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
            "api.api_endpoint": ("api_endpoint", "str"),
            "api.health_endpoint": ("health_endpoint", "str"),
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

        # ─── Vehicle fields ───
        if self.cfg.vehicles:
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
        return content

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

    def _replace_vehicle_tuple_assignment(self, block, field, new_val):
        import re

        pattern = rf'({field}\s*=\s*)(?:None|\([^)]+\)|depot_[a-zA-Z_]+)'
        if re.search(pattern, block):
            return re.sub(pattern, rf'\g<1>{new_val}', block)

        insert_pattern = r'(\n\s*start_time_minutes\s*=)'
        if field == "start_location":
            return re.sub(insert_pattern, f"\n                start_location={new_val},\\1", block, count=1)

        insert_pattern = r'(\n\s*\)\s*,?)'
        return re.sub(insert_pattern, f"\n                {field}={new_val}\\1", block, count=1)

    def _replace_vehicle_field(self, content, vtype, idx, values, prefix):
        """Замества полета на превозно средство в config.py"""
        import re

        # Намираме блока на VehicleConfig за този тип
        vehicle_fields = {
            "enabled": "bool",
            "count": "int",
            "fixed_cost": "int",
            "capacity": "int",
            "max_time_hours": "int",
            "service_time_minutes": "int",
            "max_customers_per_route": "optional_int",
            "start_depot_name": "depot_choice",
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
            values = self._collect_values()
            changed = self._apply_to_config_file(values)
            if changed:
                messagebox.showinfo("Запазено", "Настройките са записани в config.py")
            else:
                messagebox.showinfo("Без промени", "Няма промени за записване.")
        except Exception as e:
            messagebox.showerror("Грешка", f"Грешка при запис: {e}")

    def _save_and_close(self):
        try:
            values = self._collect_values()
            self._apply_to_config_file(values)
            self.root.destroy()
        except Exception as e:
            messagebox.showerror("Грешка", f"Грешка при запис: {e}")

    def _save_and_run(self):
        try:
            values = self._collect_values()
            self._apply_to_config_file(values)
        except Exception as e:
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
