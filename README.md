# CVRP OR-Tools Optimizer

Софтуер за дневно планиране на маршрути на бусове с капацитет, работно време, различни депа, зона център, реални пътни разстояния и подробни изходни файлове.

Проектът решава CVRP задача: дадени са клиенти с GPS координати и обем в стекове, налични бусове с различен капацитет и време за обслужване, и целта е да се получат изпълними маршрути с минимално време/разстояние и удобни файлове за диспечиране.

## Какво може програмата

- Чете входни данни от Excel или HTTP JSON.
- Валидира GPS координати, обеми, клиентски номера и документи.
- Разделя заявките между бусове и складова обработка.
- Изчислява матрица с разстояния и времена през OSRM или Valhalla.
- Поддържа два решителя: OR-Tools и PyVRP.
- Поддържа индивидуално време за обслужване по тип бус.
- Поддържа различни начални депа по тип бус.
- Поддържа център зона като кръг или начертан полигон през GUI.
- Позволява при нужда solver-ът да пропуска заявки, като приоритетно по-лесни за пропускане са големи и близки до депото заявки.
- Генерира обща HTML карта, отделни HTML карти за всеки маршрут, Excel отчет, CSV файл и графики.
- В отделните route карти има сгъваем списък с клиенти: бутон `Клиенти`, избор на клиент, popup с обем и бутон за навигация.
- Показва посока на движение по маршрутите с разредени стрелки.
- Добавя дата на стартиране към имената на общата карта, Excel файловете и отделните route карти. CSV файлът остава без дата.
- Има GUI за настройки и Windows Task Scheduler интеграция.
- Може да работи като Python проект или да се build-не до `CVRP_Optimizer.exe`.

## Основни файлове

| Файл | Роля |
|---|---|
| `main.py` | Главен workflow за Python режим. |
| `main_exe.py` | Entry point за PyInstaller/EXE режим. |
| `config.py` | Основна конфигурация: вход, бусове, депа, solver, output, routing. |
| `config_gui.py` | Графичен интерфейс за настройките. |
| `input_handler.py` | Четене на Excel/HTTP JSON и парсване на GPS координати. |
| `warehouse_manager.py` | Предварително разпределение към бусове или склад. |
| `cvrp_solver.py` | OR-Tools solver. |
| `pyvrp_solver.py` | PyVRP solver. |
| `osrm_client.py` | OSRM матрици, кеш и route geometry. |
| `valhalla_client.py` | Valhalla интеграция. |
| `output_handler.py` | HTML карти, Excel, CSV и графики. |
| `build_exe.py` | Автоматизиран build с PyInstaller. |
| `start_cvrp.bat` | Стартира EXE, ако има; иначе стартира през `.venv`. |
| `Settings.bat` | Отваря настройките през EXE или `.venv`. |

## Бърз старт с Python

PowerShell:

```powershell
cd "C:\Programming\Bizant 2.0\cvrp-ortools-optimizer"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Проверка, че OR-Tools е инсталиран в правилната среда:

```powershell
.\.venv\Scripts\python.exe -c "import ortools; print(ortools.__version__)"
```

Стартиране на оптимизатора:

```powershell
.\.venv\Scripts\python.exe main.py
```

Стартиране с конкретен Excel файл:

```powershell
.\.venv\Scripts\python.exe main.py "input\input.xlsx"
```

Стартиране през batch:

```powershell
.\start_cvrp.bat
```

## GUI настройки

```powershell
.\Settings.bat
```

или:

```powershell
.\.venv\Scripts\python.exe config_gui.py
```

GUI-то има табове за:

- Входни данни.
- Превозни средства.
- Предварителна оптимизация/склад.
- Solver настройки.
- Локации и депа.
- Изходни файлове.
- Автоматично стартиране през Windows Task Scheduler.

В таб `Автоматично стартиране` може да се създават повече от една Windows Task Scheduler задача. Въведи различно име за всяка задача, например:

```text
CVRP_Optimizer_Auto_1
CVRP_Optimizer_Auto_2
CVRP_Optimizer_Auto_Morning
```

Ако използваш същото име, Windows ще обнови тази задача. Ако използваш ново име, ще се създаде отделен schedule с отделни дни и час.

В таб `Локации` могат да се добавят допълнителни депа във формат:

```text
Име на депо: 42.123456, 23.123456
```

След запис/презареждане тези депа могат да се избират по име за начално депо на видовете бусове.

## Входни данни

Настройките са в `config.py`, клас `InputConfig`.

Поддържани режими:

- `input_source = "excel"`
- `input_source = "http_json"`

По подразбиране Excel файлът е:

```text
input/input.xlsx
```

Основни колони:

| Поле | Примерна колона |
|---|---|
| Клиентски номер | `IdCust` |
| Име на клиент | `Клиент` |
| GPS | `GPS` |
| Обем | `Брой стекове` |
| Документ | `Документ` |

GPS стойностите се очакват като latitude/longitude, например:

```text
42.697357, 23.323810
```

HTTP JSON режимът поддържа URL с дата, декодиране на `utf-8`, `windows-1251`, `latin-1` и mapping на полетата в `InputConfig`.

## Бусове, депа и сервизно време

Всеки `VehicleConfig` има:

- `vehicle_type`
- `enabled`
- `count`
- `capacity`
- `max_time_hours`
- `service_time_minutes`
- `max_customers_per_route`
- `start_location`
- `tsp_depot_location`
- `start_time_minutes`

Важна текуща логика:

- OR-Tools използва vehicle-specific transit callbacks за време.
- PyVRP използва отделни профили по тип бус и добавя service time в edge duration.
- Времето за обслужване вече не е средна стойност за всички бусове.
- При избор на депо през GUI се записва както `start_location`, така и `tsp_depot_location`.

## Център зона

Център зоната може да работи в два режима:

```python
center_zone_mode = "circle"
center_zone_mode = "polygon"
```

При `circle` се използват:

- `center_location`
- `center_zone_radius_km`

При `polygon` се използва:

- `center_zone_polygon`

GUI бутонът `Чертай на карта` отваря локален Leaflet редактор, в който зоната може да се начертае и запише като полигон.

Зоната влияе върху:

- приоритети на center bus;
- ограничения и penalties за други бусове;
- визуализацията върху картите.

## Solver-и

### OR-Tools

Избира се с:

```python
cvrp.solver_type = "or_tools"
```

OR-Tools режимът поддържа:

- capacity dimension;
- time dimension с vehicle-specific service time;
- индивидуални депа;
- пропускане на клиенти чрез `AddDisjunction`;
- индивидуални penalties при priority dropping;
- паралелни стратегии, ако `enable_parallel_solving = True`.

### PyVRP

Избира се с:

```python
cvrp.solver_type = "pyvrp"
```

В `requirements.txt` PyVRP е pin-нат към:

```text
pyvrp>=0.5.0,<0.6.0
```

PyVRP използва същата входна матрица, същите клиенти и същата output структура. Поддържа vehicle-specific service time и priority dropping чрез prize/penalty логика.

Забележка: нито OR-Tools, нито PyVRP в този проект използват видеокарта. По-добрият резултат идва от настройки, време за търсене, матрица с по-точни времена и коректни ограничения, не от GPU.

## Приоритетно пропускане на заявки

Когато `allow_customer_skipping = True`, solver-ът може да остави част от заявките необслужени, ако всички ограничения не могат да се изпълнят.

Когато `enable_priority_dropping = True`, penalty/prize се изчислява индивидуално:

- по-голям обем означава по-лесно пропускане;
- по-близо до депо означава по-лесно пропускане;
- малки и далечни заявки получават по-висока защита.

Основни настройки:

```python
enable_priority_dropping = True
drop_volume_weight = 0.70
drop_closeness_weight = 0.30
min_customer_drop_penalty = 20000
max_customer_drop_penalty = 120000
```

Това не е строго двуфазно правило "само ако няма решение"; това е objective bias. Ако solver-ът пропуска твърде лесно, увеличи `min_customer_drop_penalty` и `max_customer_drop_penalty`.

## Routing engine

Изборът е в `config.routing.engine`:

- `osrm`
- `valhalla`

OSRM е основният режим. Използва:

- централен кеш;
- batch Table API;
- route geometry за визуализация;
- fallback логика при липсващи данни.

Важно: междублоковите връзки вече не се приемат за симетрични. A->B и B->A се попълват отделно, защото еднопосочни улици, забрани и завои могат да правят двете посоки различни.

## Изходни файлове

Основни output директории:

```text
output/
output/routes/
output/excel/
output/charts/
logs/
```

Генерирани файлове:

- обща HTML карта;
- отделна HTML карта за всеки маршрут;
- Excel отчет `cvrp_report_YYYY-MM-DD.xlsx`;
- Excel файлове за склад и маршрути, ако са активни;
- CSV `routes.csv`;
- PNG графики;
- log файл.

Дата се добавя към:

- общата карта;
- отделните route карти;
- Excel файловете.

CSV файлът не се променя по име, за да остане стабилен за външни процеси.

## Карти

Поддържат се два режима:

- Folium/OpenStreetMap/Esri tiles (`map_provider = "osm"`);
- Google Maps (`map_provider = "google"`, изисква API key).

В картите има:

- депа;
- център зона като кръг или полигон;
- цветни маршрути;
- разредени стрелки за посока на движение;
- popup-и с информация за клиенти;
- Google Maps navigation link.

В отделните route карти има сгъваем списък:

- бутон `Клиенти`;
- списък с ред на посещение;
- обем в стекове;
- бутон `Навигация`;
- клик върху клиент центрира картата и отваря popup.

## Build до EXE

Подробно е описано в [BUILD_EXE_README.md](BUILD_EXE_README.md).

Кратко:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe build_exe.py
```

Build-ът създава `CVRP_Optimizer.exe` в `..\dist`.

## Чести проблеми

### "OR-Tools не е инсталиран"

Провери, че стартираш правилния Python:

```powershell
.\.venv\Scripts\python.exe -c "import ortools; print(ortools.__version__)"
```

Ако това работи, но програмата казва, че OR-Tools липсва, стартираш с друг Python или стар стартиращ файл. Използвай:

```powershell
.\start_cvrp.bat
```

### Няма `CVRP_Optimizer.exe`

`start_cvrp.bat` и `Settings.bat` вече имат fallback към `.venv`. Можеш да работиш без EXE.

### Няма route карти или Excel

Провери:

- `output.enable_interactive_map`;
- output директориите;
- правата за запис;
- `logs/cvrp.log`.

### Няма видима посока на картата

Генерирай картите наново. Старите HTML файлове не се обновяват автоматично.

### Списъкът с клиенти не се вижда в отделните карти

Генерирай route картите наново. Новият списък е зад бутон `Клиенти`, за да не закрива картата.

## Последни промени по проекта

### PyVRP, бусове и глоби

- PyVRP е основният solver, когато е инсталиран. OR-Tools остава fallback, ако PyVRP липсва.
- В GUI има глоба за `CENTER BUS`, когато излезе извън център зоната.
- Всеки тип бус има собствена цена за използване. Тази цена се подава към solver-а като fixed cost, за да не избира евтин/неподходящ бус само заради капацитет.
- Поддържат се отделни настройки за бусове, капацитети, депа, работно време, глоба за пропускане и глоба за център зона.

### OSRM/Valhalla и посоки

- Добавена е опция за curbside/side-of-street routing, за да се отчита от коя страна на улицата е клиентът.
- При OSRM се използва `approaches=curb`, когато опцията е включена.
- При Valhalla се подава предпочитана страна чрез `preferred_side`.
- Ако бус не трябва да минава по дадени улици, правилният професионален подход е това да се решава от routing engine-а чрез ограничения/профил/изключени сегменти, а не само със solver глоба.

### Входни данни от Bizant

Новият GET адрес се настройва от GUI и използва:

```text
http://sio.effect.bg:7080/lubiv_Bizant?cmd=getData&Date=YYYY-MM-DD&Sklad=106&DoneFlag=1973
```

Датата остава автоматична, ако не е зададена ръчно. Полетата, които се четат от отговора, са:

- `GPS`
- `IdCust`
- `CustName`
- `Volume`
- `IdDoc`
- `IdPlasDoc`
- `IdSkld`
- `DoneFlag`

`IdPlasDoc` се пази за връщане към `setData`, а `IdSkld` се пази като оригинален склад на клиента и се използва при необслужени клиенти.

### API сървър

Добавен е локален/отдалечен API сървър с POST endpoint:

```text
POST http://IP:8088/solve
```

Адресът, портът и endpoint-ът се настройват в GUI. Сървърът може да слуша само локално или от мрежата според зададения host. За отдалечен достъп host трябва да е например `0.0.0.0`, а firewall/port forwarding трябва да позволяват връзката.

Пример:

```cmd
curl -X POST "http://10.10.100.134:8088/solve" -H "Content-Type: application/json" -d "{\"customers\":[{\"GPS\":\"42.6977, 23.3219\",\"IdCust\":\"C001\",\"CustName\":\"Client 001\",\"Volume\":3,\"IdDoc\":\"D001\",\"IdPlasDoc\":\"P001\",\"IdSkld\":\"106\"}]}"
```

След една заявка API сървърът не спира. Той връща резултат и остава да чака следваща заявка.

### setData и makeGroup

След успешно решение програмата може автоматично да върне резултата към същия Bizant URL чрез:

```text
cmd=setData
```

Параметрите са:

- `IdPlasDoc`
- `DoneFlag`
- `IdSkld`
- `Bukva`
- `IdGrafik`

За обслужени клиенти:

- `IdPlasDoc` идва от GET полето `IdPlasDoc`.
- `IdSkld` се определя от депото/буса. В GUI могат да се сменят складовете за основно депо и Враца.
- `Bukva` по подразбиране е `БХ{route_number}-{stop_number}`, например `БХ1-1`.
- `IdGrafik` по подразбиране е Excel номерът на буса, например `1004501001`.

За необслужени клиенти:

- могат да се изпращат към `setData` чрез отделна отметка в GUI;
- `IdSkld` се взема от оригиналните GET данни на клиента;
- `Bukva` по подразбиране е `HOF-{id_plas_doc}`, тоест `HOF-` + номерът на заявката от `IdPlasDoc`;
- `IdGrafik` има отделно GUI поле/шаблон;
- ако клиентът няма `IdSkld` в GET отговора, програмата използва fallback стойността от настройките.

След всички успешни `setData` заявки може да се изпрати:

```text
cmd=makeGroup
par: IdSkld
```

Изпраща се по веднъж за всеки използван склад, последователно. Има отметка в GUI за включване/изключване.

### Отчети и файлове

- В CVRP Excel отчета времето е в часове.
- Текстът е променен от “автобуси” на “бусове”.
- Excel отчетът показва ID на буса като `1004501001`, `1004501002`, `1004501003` и т.н.
- Добавена е отделна колона за номер на маршрут `1`, `2`, `3`, `4`.
- CSV файлът не е променян по тази логика.
- В GUI има опции да се изключват Excel, CSV, карти и chart-ове поотделно.

### Build/EXE

Build-ът включва основната програма, Settings GUI, API сървъра и стартовите batch файлове. Има и скрит старт на API сървъра чрез `start_api_server_hidden.bat`, когато трябва сървърът да работи без отворен CMD прозорец.
