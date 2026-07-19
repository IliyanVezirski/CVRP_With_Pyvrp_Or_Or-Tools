# Инсталация, build, изходни файлове и диагностика

Това ръководство описва поведението на текущия source код: работа от Python,
изграждане на Windows EXE, изолираните solver worker-и, конфигурационните
файлове, картите и отчетите, `setData`, логовете и основните проверки при
проблем. Командите са за PowerShell и приемат, че проектът е в
`D:\Iliyan\bizant_source`.

## 1. Директории и режими на работа

Приложението има два основни режима:

- **Source режим** — основната програма се изпълнява от `.venv` в
  `D:\Iliyan\bizant_source` и използва
  `D:\Iliyan\bizant_source\config.py`.
- **Build/EXE режим** — `Bizant.exe` използва `config.py`, който се
  намира в същата директория като EXE файла. При стандартен build това е
  `D:\Iliyan\dist\config.py`.

Стандартните build директории са:

```text
D:\Iliyan\bizant_source   source код
D:\Iliyan\build           временни PyInstaller файлове
D:\Iliyan\dist            готовото приложение
```

Относителните runtime пътища в build конфигурацията се разрешават спрямо
директорията на EXE файла. Абсолютните пътища се запазват без промяна.

## 2. Основна Python среда

Основната `.venv` съдържа OR-Tools, стабилния PyVRP 0.13 и библиотеките за
вход, карти и отчети. PyVRP 0.14, VROOM и VRP-Rust не трябва да се инсталират
в нея — те използват отделни среди.

```powershell
Set-Location "D:\Iliyan\bizant_source"
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

За build са необходими още PyInstaller и matplotlib. `branca` обикновено се
инсталира като зависимост на Folium, но може да се провери изрично:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller matplotlib
.\.venv\Scripts\python.exe -c "import pandas,numpy,openpyxl,requests,folium,branca,matplotlib,ortools,pyvrp; print('main environment OK')"
.\.venv\Scripts\python.exe -m pip check
```

`requirements.txt` ограничава основния PyVRP до `>=0.13.4,<0.14`. Не
инсталирайте PyVRP 0.14 в `.venv`, защото двете версии имат едно и също import
име и native модули.

## 3. Стартиране от source

Основен дневен run:

```powershell
.\.venv\Scripts\python.exe run_main.py
```

Run с конкретен Excel файл:

```powershell
.\.venv\Scripts\python.exe run_main.py "D:\data\input.xlsx"
```

Този positional файл се използва само когато `input.input_source = "excel"`.
Ако активният source е `http_json`, HTTP входът има предимство и аргументът се
игнорира. Смени източника предварително през Settings или `config.py`.

Настройки:

```powershell
.\.venv\Scripts\python.exe config_gui.py
```

API и Web портал:

```powershell
.\.venv\Scripts\python.exe cvrp_api_server.py
```

Същите действия могат да се стартират с:

```powershell
.\start_cvrp.bat
.\Settings.bat
.\start_api_server.bat
.\start_api_server_hidden.bat
```

Batch файловете първо търсят `Bizant.exe`. Ако той липсва, използват
локалната `.venv`. `start_cvrp.bat` подава `data\input.xlsx`, когато файлът
съществува; иначе програмата използва входния източник от `config.py`.

## 4. Solver-и и изолирани worker-и

Стойностите на `cvrp.solver_type` са:

| Стойност | Реализация | Runtime |
|---|---|---|
| `or_tools` | OR-Tools | основната `.venv`/главният EXE |
| `pyvrp` | PyVRP 0.13 | основната `.venv`/главният EXE |
| `pyvrp_experimental` | PyVRP 0.14 | `.venv-pyvrp-next` или отделен EXE worker |
| `vroom` | official pyvroom 1.15 | `.venv-vroom` или отделен EXE worker |
| `vrp` | VRP-Rust/`vrp-cli` 1.24 | `.venv-vrp-rust` или отделен EXE worker |

Worker path настройките (`pyvrp_next_worker_path`, `vroom_worker_path` и
`vrp_worker_path`) са инсталационни. Не трябва да се подават от недоверена API
заявка. Когато са празни, runtime-ът първо търси source средата, а после
стандартната worker директория до build-а.

### 4.1. PyVRP 0.14

Необходим е PyVRP 0.14 wheel, съвместим с Python и Windows архитектурата на
`.venv-pyvrp-next`. Подготовка:

```powershell
.\.venv\Scripts\python.exe setup_pyvrp_next.py --wheel "D:\wheels\pyvrp-0.14.0a0-cp314-cp314-win_amd64.whl"
.\.venv-pyvrp-next\Scripts\python.exe pyvrp_next_worker.py --self-check
```

Самостоятелен build на worker-а:

```powershell
.\.venv-pyvrp-next\Scripts\python.exe build_pyvrp_next_worker.py
& "D:\Iliyan\dist\pyvrp-next\CVRP_PyVRP_Next_Worker\CVRP_PyVRP_Next_Worker.exe" --self-check
```

`cvrp.pyvrp_next_worker_timeout_seconds = 0` означава автоматичен timeout:
поне 60 секунди и обичайно solver лимитът плюс 60 секунди. При положителна
стойност тя е общият worker timeout. Startup self-check е ограничен до максимум
60 секунди.

По подразбиране повреден или липсващ 0.14 worker прекратява run-а с ясна
грешка. Само `pyvrp_next_fallback_to_stable = True` разрешава преминаване към
стабилния PyVRP 0.13; резултатът пази поискан и реално използван solver и
причината за fallback-а.

Актуалният worker трябва да показва `capabilities.hard_mandatory_customers=true`
в `--self-check`. Worker от по-стар build без capability-то се отказва умишлено;
изгради го отново от същия source като главния EXE.

### 4.2. VROOM 1.15

Има автоматичен setup скрипт, който създава `.venv-vroom`, инсталира
`pyvroom==1.15.2` и `pyinstaller==6.20.0` и изпълнява self-check:

```powershell
.\.venv\Scripts\python.exe setup_vroom.py
.\.venv-vroom\Scripts\python.exe vroom_worker.py --self-check
.\.venv-vroom\Scripts\python.exe build_vroom_worker.py
& "D:\Iliyan\dist\vroom\CVRP_VROOM_Worker\CVRP_VROOM_Worker.exe" --self-check
```

`vroom_worker_timeout_seconds = 0` използва поне 60 секунди и обичайно solver
лимита плюс 60 секунди. `vroom_threads = 0` означава автоматично
`CPU ядра - 1`.

VROOM 1.15 няма свързани повторни курсове. При включено
`enable_multiple_trips` Web валидирането изисква да се избере PyVRP, OR-Tools
или VRP-Rust вместо VROOM.

### 4.3. VRP-Rust 1.24

В текущия source няма `setup_vrp_rust.py`, затова средата се подготвя ръчно.
Работещата текуща инсталация използва Python 3.12, `vrp-cli==1.24.0` и
`pyinstaller==6.20.0`:

```powershell
py -3.12 -m venv .venv-vrp-rust
.\.venv-vrp-rust\Scripts\python.exe -m pip install --upgrade pip
.\.venv-vrp-rust\Scripts\python.exe -m pip install "vrp-cli==1.24.0" "pyinstaller==6.20.0"
.\.venv-vrp-rust\Scripts\python.exe vrp_rust_worker.py --self-check
.\.venv-vrp-rust\Scripts\python.exe build_vrp_rust_worker.py
& "D:\Iliyan\dist\vrp-rust\CVRP_VRP_Rust_Worker\CVRP_VRP_Rust_Worker.exe" --self-check
```

`vrp_worker_timeout_seconds = 0` използва поне 60 секунди и обичайно solver
лимита плюс 180 секунди. `vrp_threads = 0` оставя автоматичен избор на нишки от
worker-а.

## 5. Пълен Windows build

Преди build спрете работещите `Bizant.exe` и companion worker-и. Иначе
Windows или антивирусен софтуер може да държи стария EXE заключен.

Уверете се, че съществуват и трите изолирани среди, след което изпълнете:

```powershell
Set-Location "D:\Iliyan\bizant_source"
.\.venv\Scripts\python.exe build_exe.py
```

Build скриптът:

1. проверява библиотеките в основната `.venv`;
2. генерира вътрешния PyInstaller spec и version info;
3. изгражда главния `Bizant.exe`;
4. изгражда и self-check-ва PyVRP 0.14 worker-а;
5. изгражда и self-check-ва VROOM worker-а;
6. изгражда и self-check-ва VRP-Rust worker-а;
7. създава четирите batch стартера;
8. копира runtime `config.py` и създава `data` директорията.

Ако някой companion worker липсва или self-check-ът му е неуспешен, главният
EXE може вече да е създаден, но build-ът се отчита като **непълен** и не трябва
да се разпространява като готов release.

### 5.1. Build на друго място

`CVRP_DIST_DIR` задава release директорията, а `CVRP_BUILD_DIR` — временните
PyInstaller файлове. Стойностите се наследяват и от трите worker build скрипта:

```powershell
$env:CVRP_DIST_DIR = "D:\Iliyan\dist_new"
$env:CVRP_BUILD_DIR = "D:\Iliyan\build_new"
.\.venv\Scripts\python.exe build_exe.py
Remove-Item Env:CVRP_DIST_DIR
Remove-Item Env:CVRP_BUILD_DIR
```

### 5.2. Запазване на съществуващия runtime config

По подразбиране build-ът копира source `config.py` в release директорията и
може да замени вече настроения runtime config. За build без промяна на
съществуващия `dist\config.py`:

```powershell
$env:CVRP_PRESERVE_DIST_CONFIG = "1"
.\.venv\Scripts\python.exe build_exe.py
Remove-Item Env:CVRP_PRESERVE_DIST_CONFIG
```

Тази опция пази config само ако той вече съществува в избрания
`CVRP_DIST_DIR`. При нова празна release директория source config се копира.

### 5.3. Задължителна release структура

```text
dist\
  Bizant.exe
  config.py
  start_cvrp.bat
  Settings.bat
  start_api_server.bat
  start_api_server_hidden.bat
  data\
  pyvrp-next\
    CVRP_PyVRP_Next_Worker\
      CVRP_PyVRP_Next_Worker.exe
      _internal\...
  vroom\
    CVRP_VROOM_Worker\
      CVRP_VROOM_Worker.exe
      _internal\...
  vrp-rust\
    CVRP_VRP_Rust_Worker\
      CVRP_VRP_Rust_Worker.exe
      _internal\...
```

Worker-ите са `onedir` packages. Не копирайте само техния `.exe`; необходима е
и съответната `_internal` директория. За разпространение копирайте цялата
release папка.

### 5.4. Проверка на release-а

```powershell
Set-Location "D:\Iliyan\dist"
.\Bizant.exe --settings
.\Bizant.exe --server --host 127.0.0.1 --port 8070
```

В друг PowerShell прозорец:

```powershell
curl.exe "http://127.0.0.1:8070/health"
```

Проверете и трите worker-а с показаните по-горе `--self-check` команди.

## 6. Конфигурация, записване и backup-и

### 6.1. Кой `config.py` се използва

- Source GUI и source run редактират/четат
  `D:\Iliyan\bizant_source\config.py`.
- Built GUI и built run редактират/четат `config.py` до
  `Bizant.exe`.
- Промяна в source config не променя автоматично built config и обратно.

### 6.2. Desktop GUI

При „Запази“ Desktop GUI:

1. валидира стойностите;
2. генерира целия променен Python текст;
3. изпълнява `compile(...)`, преди да докосне config файла;
4. създава backup;
5. записва временен файл и го заменя атомарно с `os.replace`.

Backup-ите са до активния config:

```text
config_backups\config_desktop_gui_YYYYMMDD_HHMMSS_microseconds.py
```

### 6.3. Web портал

В Web портала има четири основни действия:

- **Стартирай без запис** — създава изолиран config само за този Web run.
  `config.py` не се променя.
- **Запази глобално в config.py** — записва показаните solver/output/setData и
  настройки. Те важат за Desktop, Web и публичния `/run`. API host/endpoints и
  Web потребителите се управляват от Desktop Settings.
  Списъкът с бусове не се променя от това действие.
- **Запази само бусовете** — заменя само `VehicleConfig` списъка. За начална
  точка Web порталът приема избрано име на съществуващо депо, а не свободни GPS
  координати.
- **Отхвърли промените и зареди глобалните** — презарежда формата от последно
  записания `config.py`, без да записва текущите редакции.

Web записите също са атомарни и създават:

```text
config_backups\config_web_gui_YYYYMMDD_HHMMSS_microseconds.py
```

Полето `api.web_gui_run_defaults_json` е запазено за съвместимост със стар
Web-only preset. Текущият основен Web поток използва текущия глобален config и
стойностите, изпратени от формата за конкретния run; публичният `/run` не
трябва да се влияе от стар Web-only preset.

### 6.4. Възстановяване

Няма автоматично изтриване или автоматичен restore на config backup-и. За
възстановяване:

1. спрете оптимизатора и API сървъра;
2. копирайте избрания backup върху активния `config.py`;
3. стартирайте Settings и проверете валидирането;
4. стартирайте `/health` или кратък тестов run.

Web потребителите не са в `config.py`. Те се съхраняват отделно в
`data\web_gui_auth.json` с PBKDF2-SHA256 hash-ове. При преместване на release-а
копирайте и този файл; config backup не го възстановява.

## 7. Изходни файлове

Изходите се управляват от `OutputConfig`. Грешка при един тип изход се логва,
но не спира опита за генериране на останалите типове.

| Опция | Резултат |
|---|---|
| `enable_interactive_map` | обща карта и индивидуални карти на физическите бусове |
| `enable_excel_output` | общ CVRP Excel workbook |
| `enable_csv_output` | CSV с клиентските редове на маршрутите |
| `enable_charts` | три PNG анализа |

### 7.1. Дата в имената

При нормален run се използва `YYYY-MM-DD`:

- обща карта: `<map_stem>_YYYY-MM-DD.html`;
- Excel: `cvrp_report_YYYY-MM-DD.xlsx`;
- индивидуална карта с един курс: `<vehicle>_YYYY-MM-DD.html`;
- индивидуална карта с няколко курса:
  `<vehicle>_all_courses_YYYY-MM-DD.html`.

При сблъсък между имената на две индивидуални карти в един и същ run се добавя
`_2`, `_3` и т.н. Този механизъм не версионира файловете от предишни runs:
повторен run със същата дата и същите имена може да ги замени. CSV и chart
файловете използват стабилните конфигурирани имена без автоматична дата и също
могат да бъдат заменени от следващ run.

### 7.2. Карти

Общата карта съдържа всички маршрути, депа, клиенти, route geometry, ETA,
обеми, работни прозорци, посоки и GPS търсачка. Поддържат се:

- `map_provider = "osm"` с Folium/Esri tiles;
- `map_provider = "google"` с Google Maps JavaScript API key.

`show_on_map` на center/traffic зона управлява само визуализацията. Скриването
на зона от картата не я изключва от solver ограниченията, multiplier-ите или
отстъпките.

За физически бус с повече от един курс се генерира **една** индивидуална HTML
карта. В нея има филтър за курсовете; изборът на курс показва неговата линия,
неговия номер и клиентската му информация, включително ETA, работно време,
коментар, навигация и Street View. За еднокурсен бус се генерира стандартната
индивидуална карта с панел „Клиенти“.

### 7.3. Excel

Активният основен workflow създава един файл:

```text
<excel_output_dir>\cvrp_report_<date>.xlsx
```

Workbook-ът съдържа:

- `Маршрути` — клиентските редове, движение, GPS, разстояния, време, ETA,
  чакане и time-window статус;
- `Необслужени клиенти` — когато има такива;
- `Обобщение`;
- `Статистики по бусове` — когато има маршрути.

При повторни курсове Excel добавя само колоната `Курс`. В него не се показват
вътрешните `ID курс` и `Ключ бус`. Всеки курс има отделен публичен `ID бус`.

Настройките `warehouse_excel_file`, `routes_excel_file` и
`efficiency_excel_file` са запазени в конфигурационния модел/GUI за
съвместимост, но текущият `generate_all_outputs()` workflow не създава три
отделни Excel файла — използва общия `cvrp_report` workbook.

### 7.4. CSV

CSV файлът се записва в `csv_output_file` с UTF-8 BOM и разделител запетая.
Името не получава дата. При multi-trip CSV добавя:

- `ID бус`;
- `Курс`;
- `ID курс`;
- `Ключ бус`.

Тези технически колони остават в CSV за интеграции и проследяване, въпреки че
са скрити от Excel и общата карта.

### 7.5. Графики

При `enable_charts = True` се създават:

- `efficiency_analysis.png` — капацитет, обслужени/необслужени и време;
- `route_comparison.png` — сравнение на маршрутите;
- `volume_distribution.png` — разпределение на обемите.

Имената се управляват от `OutputConfig` и не получават автоматична дата.
Графиките изискват `matplotlib`; ако библиотеката липсва, те се пропускат с
warning, без да падне целият run.

## 8. Физически бус, курс, маршрут и публичен номер

Приложението различава няколко идентификатора:

| Поле | Значение | Къде се вижда |
|---|---|---|
| `vehicle_key` | постоянен вътрешен ключ на физическия бус | CSV/вътрешна логика |
| `trip_number` | номер на курса за този физически бус: 1, 2, 3... | Excel `Курс`, CSV, индивидуална карта |
| `route_id` | стабилен вътрешен ID на курса, често `<vehicle_key>:trip:<n>` | CSV и metadata на индивидуалната карта |
| номер на маршрут | позицията на курса в подредения общ резултат | Excel/CSV/карти |
| `ID бус`/`bus_number` | публичен бизнес номер | Excel, индивидуална карта, upload, `setData IdGrafik` |

Важно поведение:

- два курса на един физически бус имат общ `vehicle_key`, но различни
  `trip_number`, `route_id` и публичен `ID бус`;
- общата карта не включва solver `route_id`; при multi-trip показва само
  необходимото обозначение на курса;
- индивидуалната карта използва вътрешния ID само за надеждно превключване и
  връзка с upload-а;
- физическите бусове се държат заедно, курсовете им се подреждат по номер, а
  `CENTER_BUS` се подреждат последни.

Обичайният публичен номер е:

```text
<excel_bus_number_prefix><пореден номер с excel_bus_number_digits цифри>
```

Ако специалното center номериране е включено, center курсовете започват от
`center_bus_numbering_start_id`, а останалите курсове прескачат заетите center
номера, за да няма дублиране.

## 9. Съботен run

`GET` или `POST /run_saturday` прави request-local run със следните правила:

1. изчислява съботата на текущата календарна седмица;
2. подава датата към HTTP входа във формат `DD/MM/YYYY`;
3. използва `saturday_excel_bus_number_prefix` и
   `saturday_excel_bus_number_digits`;
4. използва file stamp `YYYY-MM-DD_събота`;
5. не записва тези временни стойности в `config.py`.

Ако `center_bus_numbering_enabled = True`, специалната CENTER_BUS серия от
`center_bus_numbering_start_id` има предимство. Следователно съботният prefix
се прилага към нормално номерираните курсове, но не заменя CENTER_BUS серията.

Пример:

```powershell
curl.exe -X POST "http://127.0.0.1:8070/run_saturday" `
  -H "Content-Type: application/json" `
  --data-raw "{}"
```

Ако е зададен API key, добавете `X-CVRP-API-Key`. Съботният stamp се добавя
към общата карта, route картите и Excel файла в същите конфигурирани директории.
CSV и chart имената остават без дата/`събота`. `setData` използва крайния
публичен `bus_number` за всеки курс: съботната серия за нормално номерираните
бусове и специалната center серия за CENTER_BUS, когато тя е включена.

Същият динамичен stamp се активира и при обикновен `/run` с:

```json
{
  "settings": {
    "input": {
      "json_override_date": "current_week_saturday"
    }
  }
}
```

За гарантиран съботен prefix използвайте специалния `/run_saturday` endpoint.

## 10. Upload на индивидуалните карти

`route_maps_upload_mode` приема:

| Стойност | Поведение |
|---|---|
| `disabled` | без HTTP upload |
| `legacy` | запазва старото поведение без новия multipart upload |
| `effect_upload` | изпраща генерираните индивидуални HTML карти |

При `effect_upload` се изпраща multipart `POST` към
`route_maps_upload_url` със:

- token поле от `route_maps_upload_token_field`;
- файлово поле от `route_maps_upload_file_field`, обичайно `files[]`;
- bus ID поле от `route_maps_upload_bus_id_field`, обичайно `pData2[]`;
- timeout от `route_maps_upload_timeout_seconds`.

Локалните файлове се запазват независимо от HTTP резултата. Upload се изпълнява
само след генериране на индивидуални карти, следователно изисква включено
генериране на карти.

При няколко курса една и съща комбинирана дневна карта на физическия бус може
да бъде изпратена по веднъж за всеки курс, като всеки запис получава уникалния
публичен bus ID на курса. Ако все пак се открият дублирани bus ID стойности,
upload-ът се пропуска, за да не се изпрати двусмислена връзка.

Upload token-ът е чувствителна стойност. Не публикувайте runtime `config.py` и
неговите backup-и. Web API не връща самия token към браузъра, а само дали е
конфигуриран.

## 11. `setData` и `makeGroup`

`enable_set_data_upload` включва реално изпращане към външната система след
успешното решаване. За всеки оригинален документ на обслужен клиент се
изпращат:

```text
cmd
IdPlasDoc
DoneFlag
IdSkld
Bukva
IdGrafik
```

Ако няколко документа са групирани в един solver stop, `setData` пак изпраща
отделен ред за всеки оригинален `IdPlasDoc`.

`set_data_http_method` може да бъде `GET` или `POST`. При `POST` данните са
`application/x-www-form-urlencoded`, не JSON.

### 11.1. Шаблони

`set_data_id_grafik_template` по подразбиране е `{bus_number}`. Така всеки
втори/трети курс получава различния публичен bus номер като `IdGrafik`.

В `IdGrafik` и `Bukva` шаблоните могат да участват:

```text
{bus_number} {route_number} {trip_number} {route_id} {vehicle_key}
{planned_start} {planned_end} {stop_number}
{vehicle_type} {vehicle_name}
{customer_id} {customer_document} {id_plas_doc}
{id_skld} {id_grafik} {done_flag} {volume}
```

`IdSkld` се определя от депото на маршрута и може да се коригира чрез
`set_data_depot_id_skld_map`. За необслужените клиенти се предпочита
оригиналният `IdSkld` от входния документ.

### 11.2. Необслужени клиенти

При `enable_unserved_set_data_upload = True` се изпращат и складовите плюс
solver-dropped клиенти. Те имат отделни настройки за DoneFlag, IdGrafik и
Bukva. Празен `set_data_unserved_done_flag` означава използване на общия
`set_data_done_flag`.

### 11.3. `makeGroup`

При `enable_make_group = True` се изгражда по една `makeGroup` заявка за всеки
уникален `IdSkld`. Те се изпращат **само ако всички предходни `setData` заявки
са успешни**. При поне една грешка `makeGroup` се пропуска.

Няма транзакционен rollback на вече успешните външни заявки. Преди реален run
проверете URL, DoneFlag, IdSkld mapping, шаблоните и bus prefix-а; Web порталът
показва допълнително потвърждение, когато `setData` е включено.

## 12. Логове и статус

| Файл | Съдържание |
|---|---|
| `logs\cvrp.log` | основен optimisation log от `LoggingConfig` |
| `logs\cvrp_exe.log` | стартиране и грешки на frozen entry point-а |
| `logs\cvrp_api_server.log` | API/Web заявки и server грешки |
| `logs\api_run_<run_id>.log` | stdout/stderr на конкретен API/Web subprocess run |
| `logs\api_run_status.json` | последен API run статус и кратко резюме |
| `logs\api_server_stdout.log` | stdout при hidden API starter |
| `logs\api_server_stderr.log` | stderr при hidden API starter |
| `logs\tsp_routes_history.jsonl` | TSP история, когато не е зададен друг history path |

Web порталът показва края на API лога и активния per-run лог. Server payload-ът
ограничава показването приблизително до последните 50 000 знака и 250 реда.

`LoggingConfig` има полета `max_log_size_mb` и `backup_count`, но текущите main
и API handlers използват обикновен `logging.FileHandler`. Автоматична log
rotation в момента не се прилага; старите логове трябва да се архивират или
изчистват оперативно.

## 13. Диагностика

### Грешен config или стар build

Проверете от коя директория стартирате. Built приложението винаги чете
`config.py` до EXE файла. Source и `dist` config са независими. След промяна на
config рестартирайте API server-а или използвайте Web/GUI reload.

### Worker „is not installed“

Проверете цялата onedir структура и изпълнете `--self-check`. При source режим
проверете съответната `.venv-*\Scripts\python.exe`; при build режим — worker-а
до EXE. Не местете само worker `.exe` без `_internal`.

### Worker protocol/version mismatch

Rebuild-нете companion worker-а със съответния source код и фиксираната версия:

- PyVRP `0.14.*`;
- pyvroom `1.15.*`;
- vrp-cli `1.24.*`.

Не смесвайте worker директории от различни releases.

### Worker timeout

Положителната `*_worker_timeout_seconds` стойност ограничава целия процес,
включително startup и solve. Ако тя е по-малка или почти равна на solver
лимита, увеличете я или задайте `0` за автоматичния margin. PyVRP startup
self-check има отделен максимум 60 секунди.

### Недостатъчно RAM/pagefile

Много паралелни PyVRP 0.14 processes могат едновременно да заредят NumPy,
OpenBLAS и solver native библиотеки. При `paging file is too small`,
`Memory allocation failed` или NumPy import грешки:

1. намалете `cvrp.num_workers`;
2. временно изключете outer parallel solving;
3. затворете стари worker/EXE процеси;
4. осигурете достатъчно Windows pagefile;
5. повторете self-check преди нов run.

### Build не може да замени EXE

Спрете `Bizant.exe`, API server-а и companion worker-ите. Проверете
Task Manager и антивирусния quarantine/lock. След това повторете build-а.

### Няма карта, Excel, CSV или chart

Проверете съответната `enable_*` опция, output path, drive достъп и края на
`cvrp.log`. Един неуспешен output не доказва, че целият run е неуспешен —
останалите изходи продължават да се генерират.

### Съботният suffix липсва от CSV или chart

Това е текущото очаквано поведение. `YYYY-MM-DD_събота` се добавя към общата
карта, индивидуалните карти и Excel; CSV и PNG имената са стабилни.

### Raw newline/tab в JSON

Входният decoder първо опитва стандартен JSON и при `invalid control
character` прави tolerant decode. Така стар endpoint може да изпрати работно
време на два реда, но се записва warning. Предпочитаният валиден JSON формат е
с escape `\n`, например:

```json
{
  "WorkTime": "08:00-13:00\n16:00-18:00"
}
```

### `setData` е частично успешен

Вижте `setData резултат` в run лога. Резюмето пази attempted/succeeded/failed и
до 20 подробни грешки. При грешка `makeGroup` не се изпраща. Преди повторение
проверете външната система, защото вече успешните заявки не се връщат назад.

## 14. Release checklist

Преди предаване на нов build:

1. изпълнете `pip check` в основната и трите companion среди;
2. build-нете в нова или проверена release директория;
3. потвърдете, че config е копиран или запазен според избрания режим;
4. изпълнете self-check на трите frozen worker-а;
5. отворете Settings и проверете активния config;
6. стартирайте API на localhost и проверете `/health`;
7. направете тест без `setData` и без route upload;
8. проверете обща карта, индивидуални карти, Excel и multi-trip филтъра;
9. проверете имената и bus номерата с `/run_saturday`;
10. едва след това включете реалните upload/`setData` интеграции.

## 15. Къде е имплементирано

За последваща проверка в source кода:

- build и environment променливи: `build_exe.py`;
- PyVRP 0.14 setup/build/runtime: `setup_pyvrp_next.py`,
  `build_pyvrp_next_worker.py`, `pyvrp_next_runtime.py`;
- VROOM setup/build/runtime: `setup_vroom.py`, `build_vroom_worker.py`,
  `vroom_runtime.py`;
- VRP-Rust build/runtime: `build_vrp_rust_worker.py`, `vrp_runtime.py`;
- EXE config и runtime paths: `main_exe.py`;
- Desktop config backup: `config_gui.py`;
- Web temporary/global/vehicle saves: `cvrp_api_server.py`;
- bus/course ID правила: `vehicle_numbering.py`;
- карти, Excel, CSV, charts и route upload: `output_handler.py`;
- `setData` и `makeGroup`: `setdata_client.py`;
- tolerant JSON decode: `input_handler.py`.
