# CVRP Optimizer (PyVRP / OR-Tools)

Софтуер за дневно планиране на маршрути на бусове с капацитет, работно време, различни депа, зона център, реални пътни разстояния и подробни изходни файлове.

Проектът решава CVRP задача: дадени са клиенти с GPS координати и обем в стекове, налични бусове с различен капацитет и време за обслужване, и целта е да се получат изпълними маршрути с минимално време/разстояние и удобни файлове за диспечиране.

## Какво може програмата

- Чете входни данни от Excel или HTTP JSON.
- Валидира GPS координати, обеми, клиентски номера и документи.
- Разделя заявките между бусове и складова обработка.
- Изчислява матрица с разстояния и времена през OSRM или Valhalla.
- Поддържа два решителя: OR-Tools и PyVRP.
- Позволява solver-ът да оптимизира по километри или по време чрез `objective_metric`.
- Поддържа индивидуално време за обслужване по тип бус.
- Поддържа различни начални депа по тип бус.
- Поддържа различна крайна точка на бус; ако няма крайна точка, маршрутът завършва в стартовото депо.
- Поддържа основна център зона и допълнителни независими център зони. Всяка допълнителна зона може да има собствени правила за кои типове бусове важат приоритетите и глобите.
- Позволява при нужда solver-ът да пропуска заявки, като приоритетно по-лесни за пропускане са големи и близки до депото заявки.
- Генерира обща HTML карта, отделни HTML карти за всеки маршрут, Excel отчет, CSV файл и графики.
- В отделните route карти има сгъваем списък с клиенти: бутон `Клиенти`, избор на клиент, ETA пристигане, popup с обем и бутон за навигация.
- В общата карта има GPS търсачка за временни пинове по координати, с име и обем, ако са подадени.
- Показва посока на движение по маршрутите с разредени стрелки.
- Поддържа отделен `/tsp` режим за текущ маршрут на един шофьор: текуща GPS позиция, optional крайна точка, клиенти, работно време, оборот, количество и коментар.
- Имената на TSP JSON полетата могат да се настройват от GUI, например ако външната система изпраща `Coord` вместо `gps` или `DriverNote` вместо `comment`.
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
| `cvrp_api_server.py` | HTTP API за `/health`, `/run`, `/solve` и `/tsp`. |
| `current_tsp.py` | Отделен TSP режим за текущ маршрут на един шофьор. |
| `setdata_client.py` | Връщане на резултата към Bizant чрез `setData` и `makeGroup`. |
| `build_exe.py` | Автоматизиран build с PyInstaller. |
| `start_cvrp.bat` | Стартира EXE, ако има; иначе стартира през `.venv`. |
| `Settings.bat` | Отваря настройките през EXE или `.venv`. |
| `start_api_server.bat` | Стартира API сървъра с видим прозорец. |
| `start_api_server_hidden.bat` | Стартира API сървъра скрито, подходящо за автоматично стартиране. |

## Архитектура по модули

Програмата е разделена на ясни слоеве. В нормален дневен run потокът е:

```text
config.py / GUI
  -> input_handler.py
  -> warehouse_manager.py
  -> osrm_client.py или valhalla_client.py
  -> cvrp_solver.py или pyvrp_solver.py
  -> output_handler.py
  -> setdata_client.py
```

`main.py` е оркестраторът. Той зарежда конфигурацията, подготвя входните данни, отделя складовите/невалидните заявки, строи матрицата, пуска избрания solver, избира най-доброто решение при паралелен режим, генерира файловете и накрая извиква `setData`, ако е включено.

`config.py` е централният модел на настройките. Там са dataclass конфигурациите за вход, бусове, депа, център зони, трафик зони, solver-и, OSRM/Valhalla, output, API и setData. GUI-то записва промените обратно в този файл, а при стартиране програмата чете текущите стойности от него.

`config_gui.py` е административният екран. Той не решава маршрути, а управлява настройките: входни данни, превозни средства, депа, зони, solver параметри, output, API, TSP полета, setData и автоматично стартиране. В падащите полета скролът с колелцето е блокиран като промяна на стойност, за да не се сменя случайно депо или solver при скролване надолу.

`input_handler.py` отговаря за входа. Той чете Excel или HTTP JSON, декодира отговори, извлича клиентски записи, валидира GPS, парсва работно време, пази коментар за доставка и може да групира няколко документа за един и същи клиент/GPS в едно посещение.

`warehouse_manager.py` прави предварителното разпределение. Ако складовата логика е включена, част от заявките може да се отделят за склад според обем, близост, лимити и правила. Останалите клиенти влизат в solver-а.

`osrm_client.py` и `valhalla_client.py` строят реалната матрица. Solver-ите не работят директно с GPS точки, а с таблица от разстояния и времена между депата и клиентите. OSRM е основният бърз режим с Table API и batch логика. Valhalla дава алтернатива с time-dependent/traffic възможности, но при липсващи клетки програмата има fallback, за да не пада целият run.

`cvrp_solver.py` съдържа OR-Tools решението. То създава routing модел с capacity/time dimensions, стартови и крайни депа, възможност за пропускане на клиенти, center zone penalties, service time, time windows и предварително сметнати vehicle cost матрици, за да се намали Python callback overhead.

`pyvrp_solver.py` съдържа PyVRP решението. То създава PyVRP модел със същите клиенти, депа и матрица, поддържа objective по `distance` или `time`, service time, time windows, center zone логика и profile compression за еднакви vehicle правила. В паралелен режим различните worker-и работят с различни seed-ове, а `main.py` избира решението с най-нисък `fitness_score`.

`output_handler.py` генерира резултатите. Тук са общата карта, индивидуалните route карти, списъците с клиенти за шофьора, ETA, route geometry, Excel отчет, CSV файл, графики и upload на HTML картите към външен PHP сървър. Ако отделен output файл даде грешка, модулът логва проблема и продължава с останалите файлове.

`cvrp_api_server.py` е remote control слоят. Той стартира API сървър, показва реалния адрес през `/health`, приема `/run` за стартиране с текущата конфигурация, `/solve` за клиенти в JSON body и `/tsp` за текущ маршрут на един шофьор. Поддържа временни settings override-и само за конкретната заявка, callback URL, API key и скрит subprocess режим за `/run`, когато няма override-и.

`current_tsp.py` е отделен от CVRP solver-ите. Той не разпределя клиенти между много бусове, а подрежда останалите клиенти на един шофьор от текуща позиция до optional крайна точка. Използва OSRM/Valhalla матрица, greedy подреждане с time-window awareness и 2-opt подобрение. После генерира индивидуална карта със същия механизъм като нормалните route карти и при upload изпраща `pData2[]=driver_id`.

`setdata_client.py` подготвя и изпраща резултатите към Bizant. За обслужените клиенти строи редове с `IdPlasDoc`, `DoneFlag`, `IdSkld`, `Bukva` и `IdGrafik`. За необслужените има отделни настройки, включително отделен `DoneFlag`. След успешни `setData` заявки може да изпрати `makeGroup` по склад.

`main_exe.py` и `build_exe.py` са за EXE режима. `main_exe.py` избира дали да стартира основната програма, GUI настройките или API сървъра. `build_exe.py` генерира spec файл, hidden imports, version info, batch файлове и runtime файлове за разпространение.

## Основен workflow

1. Конфигурацията се зарежда от `config.py`.
2. Входът се чете от Excel или HTTP JSON.
3. Невалидни клиенти без GPS се местят към необслужени.
4. Ако складовата логика е активна, част от клиентите се отделят за склад.
5. Активните бусове и депа се подреждат в единен ред.
6. OSRM или Valhalla изчислява матрицата между всички депа и клиенти.
7. OR-Tools или PyVRP решава CVRP задачата.
8. При паралелно решаване се избира решението с най-нисък `fitness_score`.
9. Генерират се HTML карти, Excel, CSV и графики според включените output опции.
10. Ако е включено, `setData` връща резултата към Bizant.
11. API режимът връща JSON резюме с маршрути, необслужени клиенти, файлове и setData статус.

## Бърз старт с Python

PowerShell:

```powershell
cd "C:\CVRP_Optimizer"
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
- API сървър: адрес, endpoint-и, API key, примерни команди и TSP имена на входни полета.
- setData: връщане на обслужени/необслужени клиенти и makeGroup.
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

Ако един и същ клиентски номер (`IdCust`) идва повече от веднъж със същите GPS координати, програмата по подразбиране групира тези редове като едно посещение. Обемът се събира, solver-ът вижда един стоп, а оригиналните документи се пазят за отчетите и `setData`. Това може да се изключи от GUI: `Входни данни -> Групирай документи`, или през API/settings с `input.enable_customer_document_grouping=false`.

Минимално валиден клиент има:

- клиентски ID;
- GPS координати;
- положителен обем;
- име или документ не са задължителни за solver-а, но са важни за отчетите и `setData`.

При HTTP JSON полетата се четат от настройките:

| Настройка | Какво значи | Пример |
|---|---|---|
| `json_gps_field` | GPS координати | `GPS` |
| `json_client_id_field` | ID на клиента | `IdCust` |
| `json_client_name_field` | Име на клиента | `CustName` |
| `json_volume_field` | Обем/стекове | `Volume` |
| `json_document_field` | Документ | `IdDoc` |
| `json_plas_doc_field` | Документ за `setData` | `IdPlasDoc` |
| `json_id_skld_field` | Оригинален склад | `IdSkld` |
| `json_time_window_field` | Работно време | `WorkTime` |
| `json_delivery_comment_field` | Коментар към доставка | `DeliveryComment` |

Ако HTTP сървърът връща данни, но програмата казва "Не са намерени валидни клиенти", почти винаги причината е една от тези:

- JSON отговорът не е списък и не е wrapper с разпознаваем ключ като `data`, `items`, `records`, `customers` или `orders`;
- GPS полето в настройките не съвпада с реалното име в JSON;
- обемът идва в друго поле или като невалидна стойност;
- в JSON има суров newline/control character в текстово поле и отговорът не е валиден JSON;
- всички редове са филтрирани преди solver-а заради липсващи координати, нулев обем или складова логика.

## Работно време и коментари

Работното време може да се включва и изключва от GUI. Когато е изключено, клиентите се приемат като отворени през целия ден.

Поддържани входни формати:

```text
08:00-13:00
08:00 - 16:00
08:00-13:00
16:00-18:00
```

Ако има два прозореца, програмата ги подава към solver-ите. OR-Tools използва общия диапазон и забранява паузите между прозорците. PyVRP създава алтернативни варианти на същия клиент и избира точно един прозорец за обслужване. TSP режимът също гледа всички прозорци и изчаква до следващия възможен прозорец, ако шофьорът пристига в пауза.

Ако форматът е невалиден, програмата не спира run-а. Работното време се игнорира за този клиент, записва се предупреждение в лога и клиентът остава в маршрутизацията като нормален клиент.

Коментарът към доставка не влияе на solver-а. Той се пази за шофьора и се показва в индивидуалните route карти. Когато клиент има работно време или коментар, списъкът го маркира визуално, за да се забележи по-лесно по време на доставка.

## Бусове, депа и сервизно време

Всеки `VehicleConfig` има:

- `vehicle_type`
- `enabled`
- `count`
- `capacity`
- `max_time_hours`
- `service_time_minutes`
- `max_customers_per_route`
- `name`
- `start_location`
- `end_location`
- `tsp_depot_location`
- `start_time_minutes`

Важна текуща логика:

- OR-Tools използва vehicle-specific transit callbacks за време.
- PyVRP използва споделени профили за еднакви vehicle правила и добавя service time в edge duration.
- Времето за обслужване вече не е средна стойност за всички бусове.
- При избор на депо през GUI се записва както `start_location`, така и `tsp_depot_location`.
- Ако `end_location` е празна, крайната точка автоматично е стартовото депо.

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

Допълнителните център зони се настройват през `locations.center_zones`. Те са независими от старата основна зона:

```python
center_zones = [
    CenterZoneConfig(
        name="Център 2",
        mode="circle",
        center_coords=(42.7093, 23.3137),
        radius_km=1.2,
        priority_vehicle_types=["center_bus"],
        restricted_vehicle_types=["internal_bus", "external_bus", "vratza_bus"],
        discount_priority_vehicle=0.9,
        priority_vehicle_outside_penalty=0,
        vehicle_penalties={"internal_bus": 40000, "external_bus": 40000, "vratza_bus": 40000},
    )
]
```

При клиент в няколко зони отстъпката за приоритетен бус взема най-добрата приложима отстъпка, а глобите за ограничени бусове се събират. Ако приоритетен бус е извън всички зони, които го таргетират, се прилага най-голямата `priority_vehicle_outside_penalty`.

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
- оптимизация по `distance` или `time`;
- предварително сметнати vehicle cost матрици, така че solver-ът да чете готова цена вместо да пресмята всяка дъга многократно през Python callback;
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
pyvrp>=0.13.3
```

PyVRP използва същата входна матрица, същите клиенти и същата output структура. Поддържа vehicle-specific service time, различни начални/крайни депа, objective по време или километри и priority dropping чрез prize/penalty логика.

PyVRP профилите се компресират: ако няколко типа бусове имат еднакви правила за цена/център зона/service time, те споделят един edge profile. Началното и крайното депо остават настройка на vehicle type, така че различни депа не се губят при компресията.

Забележка: нито OR-Tools, нито PyVRP в този проект използват видеокарта. По-добрият резултат идва от настройки, време за търсене, матрица с по-точни времена и коректни ограничения, не от GPU.

## Цел на оптимизацията

Настройката е:

```python
cvrp.objective_metric = "distance"
cvrp.objective_metric = "time"
```

`distance` означава, че solver-ът търси по-малко километри. `time` означава, че solver-ът търси по-кратко време по duration матрицата от OSRM/Valhalla. При `time` глобите и fixed cost стойностите се преобразуват към същия мащаб, за да може solver-ът да сравнява пътно време, пропуснати клиенти и center zone penalties в една обща цена.

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

При Valhalla липсващи клетки в матрицата вече не спират целия run. Програмата ги попълва с приблизителна fallback стойност по права линия с road factor и записва предупреждение в лога.

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
- очакван час на пристигане при клиентите, изчислен от същата матрица и service time, които ползва solver-ът.
- GPS търсачка в общата карта за ръчно поставяне на един или много пинове.

В отделните route карти има сгъваем списък:

- бутон `Клиенти`;
- списък с ред на посещение;
- обем в стекове;
- бутон `Навигация`;
- клик върху клиент центрира картата и отваря popup.

## API режими

API сървърът е слой за дистанционно управление. Той не заменя GUI настройките, а ги използва като база. Всяка POST заявка може временно да подаде настройки само за конкретния run.

Основни endpoint-и:

| Endpoint | Метод | Какво прави |
|---|---|---|
| `/health` | `GET` | Връща статус, реалните URL-и, текущ run и `settings_schema`. |
| `/run` | `GET` или `POST` | Стартира програмата с текущия `input_source` от конфигурацията. |
| `/solve` | `POST` | Приема клиенти в JSON body и решава CVRP. |
| `/tsp` | `POST` | Прави текущ маршрут за един шофьор от текуща GPS позиция. |

Адресът се взема от `api.api_public_url`, ако е попълнен. Ако е празен, GUI-то и `/health` показват автоматично засечения адрес на машината и `api.api_port`.

Ако `api.api_key` е попълнен, заявките трябва да подават един от тези варианти:

```text
X-CVRP-API-Key: <key>
Authorization: Bearer <key>
?api_key=<key>
```

### `/run`

`/run` е режимът "стартирай програмата както е настроена". Той е подходящ, когато входните данни вече идват от Excel или HTTP JSON според `input.input_source`.

Пример с текущите настройки:

```cmd
curl "http://IP:8088/run"
```

Пример с временни настройки само за този run:

```cmd
curl -X POST "http://IP:8088/run" -H "Content-Type: application/json" -d "{\"return_result\":true,\"settings\":{\"solver_type\":\"pyvrp\",\"objective_metric\":\"time\",\"time_limit_seconds\":180,\"output\":{\"enable_excel_output\":true,\"route_maps_upload_mode\":\"effect_upload\"},\"set_data\":{\"enable_set_data_upload\":false}}}"
```

Поведение:

- без `return_result` сървърът връща веднага `202 started`, а работата продължава във фонов режим;
- с `return_result=true` заявката чака края и връща пълния JSON резултат;
- с `callback_url` сървърът връща веднага, а след края изпраща POST известие към посочения URL;
- ако вече има активен run, връща `409 already_running`.

### `/solve`

`/solve` е режимът "подавам клиенти и настройки в заявката". Използва се, когато външна система иска да управлява целия run без да променя `config.py`.

Body може да бъде директен списък или wrapper:

```json
{
  "settings": {
    "solver_type": "or_tools",
    "objective_metric": "time"
  },
  "customers": [
    {
      "IdCust": "1001",
      "CustName": "Клиент",
      "GPS": "42.6977,23.3219",
      "Volume": 10,
      "IdDoc": "D001",
      "IdPlasDoc": "P001",
      "IdSkld": "106",
      "WorkTime": "08:00-13:00",
      "DeliveryComment": "Обади се 10 мин преди доставка"
    }
  ]
}
```

`/solve` използва същата логика за матрица, solver-и, output, `setData` и карти като нормалния run.

### `/tsp`

`/tsp` е отделен бърз режим за един шофьор, който вече има списък клиенти и текуща GPS позиция. Не прави CVRP разпределение между бусове. Той само подрежда зададените клиенти в добър ред на доставка.

Алгоритъмът е лек: greedy старт с оценка по време/разстояние и работно време, после 2-opt подобрение. За около 40 клиента обикновено е много по-бърз от пълен CVRP run, защото няма разпределение между много бусове.

Началният час се взема автоматично от текущия час на машината. Може да се подаде ръчно чрез `start_time`, но нормалният режим е да не се подава.

Ако POST заявката не подаде `service_time_minutes`, `/tsp` използва стойността от GUI: `API сървър -> TSP оптимизация -> Обслужване (мин)`. В същата секция се управляват TSP целта `time/distance`, работното време, 2-opt подобрението и тежестите за чакане/закъснение.

Локалните HTML файлове за TSP се управляват само от `api.tsp_generate_html_map` или API alias `tsp_generate_map`/`tsp_local_html`. Това не спира нормалните CVRP route карти. Ако локалната TSP HTML карта е изключена, TSP пак връща JSON реда на доставка, но не създава файл и няма файл за upload.

Ако в TSP заявката има няколко документа за един и същ клиент със същите GPS координати, `/tsp` използва същата настройка `input.enable_customer_document_grouping`. При включена настройка те стават едно посещение, количеството и оборотът се събират, а отделните документи се връщат в `delivery_order[].documents`.

Всяка успешна `/tsp` заявка се записва в TSP дневник. От GUI може да се включи автоматичен дневен Excel отчет: `API сървър -> TSP дневен Excel отчет`. В зададения час API сървърът генерира `tsp_daily_report_YYYY-MM-DD.xlsx` с обобщение на всички TSP маршрути за деня и подробен лист с клиентите. Отчет може да се генерира и ръчно през `GET/POST /tsp-report`.

Пример:

```cmd
curl -X POST "http://IP:8088/tsp" -H "Content-Type: application/json" -d "{\"driver_id\":\"BUS001\",\"driver_location\":\"42.7000,23.3000\",\"end_location\":\"42.7000,23.3000\",\"service_time_minutes\":8,\"customers\":[{\"id\":\"C001\",\"name\":\"Client 001\",\"document\":\"DOC001\",\"quantity\":8,\"turnover\":245.50,\"gps\":\"42.7100,23.3200\",\"work_time\":\"08:00-13:00\",\"comment\":\"Обади се 10 мин. преди доставка.\"}]}"
```

Имената на TSP полетата се настройват от GUI. Така може външната система да изпраща например `Coord` вместо `gps` или `DriverNote` вместо `comment`.

## Настройки през API

В API body настройките могат да се подават в `settings`. Поддържат се два стила.

Кратък стил:

```json
{
  "settings": {
    "solver_type": "pyvrp",
    "objective_metric": "time",
    "time_limit_seconds": 180,
    "routing_engine": "osrm",
    "osrm_url": "http://127.0.0.1:5000",
    "enable_excel_output": true
  }
}
```

Структуриран стил:

```json
{
  "settings": {
    "cvrp": {
      "solver_type": "pyvrp",
      "objective_metric": "time",
      "time_limit_seconds": 180
    },
    "routing": {
      "engine": "osrm"
    },
    "osrm": {
      "base_url": "http://127.0.0.1:5000",
      "profile": "driving"
    },
    "output": {
      "enable_excel_output": true,
      "routes_output_dir": "D:\\CVRP_Output\\Routes"
    }
  }
}
```

Най-важните секции:

| Секция | Какво управлява |
|---|---|
| `cvrp` | solver, време за решаване, objective, PyVRP/OR-Tools настройки, паралелност, time windows. |
| `input` | Excel/HTTP JSON вход, дата, склад, DoneFlag, имена на JSON полета, групиране на документи. |
| `vehicles` | типове бусове, брой, капацитет, имена, старт/край, service time, лимити. |
| `locations` | депа, основна център зона, допълнителни център зони, трафик зони. |
| `routing` | избор между OSRM и Valhalla. |
| `osrm` | OSRM URL, profile, timeout, chunk size. |
| `valhalla` | Valhalla URL, profile, timeout, batch настройки. |
| `output` | карти, Excel, CSV, chart-ове, директории, upload на route карти. |
| `set_data` | `setData`, DoneFlag, складове, необслужени клиенти, `makeGroup`. |

За пълния списък извикай:

```cmd
curl "http://IP:8088/health"
```

В отговора има `commands` и `settings_schema`, които показват текущите endpoint-и, примерни body-та и всички позволени полета.

## Upload на индивидуални карти

Индивидуалните route карти могат едновременно да се генерират като локални HTML файлове и да се качват към PHP endpoint.

Режими:

| Стойност | Поведение |
|---|---|
| `disabled` | Не качва карти. |
| `legacy` | Запазва старото поведение. |
| `effect_upload` | Качва HTML файловете към `route_maps_upload_url`. |

При `effect_upload` се изпраща multipart POST:

- token поле, по подразбиране `pData`;
- token стойност от настройките;
- файлове в `files[]`;
- ID на буса в `pData2[]`, подредено в същия ред като файловете.

Това не променя локалното генериране на файловете. Ако `enable_interactive_map` и route map output са включени, файловете се записват локално; upload-ът е допълнителна стъпка.

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

### HTTP входът връща данни, но програмата казва, че няма клиенти

Провери първо имената на JSON полетата в GUI. Най-важни са GPS, клиентски ID и обем. Ако външният сървър връща `Coord`, а в програмата е настроено `GPS`, всички редове ще изглеждат като невалидни.

Провери и дали JSON отговорът е валиден. Непозволени control characters в коментар или работно време могат да счупят `json.loads`, преди програмата изобщо да стигне до обработка на клиенти.

## Интеграции и подробни настройки

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
https://YOUR-DATA-SERVER/PATH?cmd=getData&Date=YYYY-MM-DD&Sklad=106&DoneFlag=1973
```

Датата остава автоматична, ако не е зададена ръчно. Полетата, които се четат от отговора, са:

- `GPS`
- `IdCust`
- `CustName`
- `Volume`
- `IdDoc`
- `IdPlasDoc`
- `IdSkld`
- `WorkTime` във формат `08:00-13:00` или `08:00 - 16:00`
- `DeliveryComment` за коментар/инструкция към доставката
- `DoneFlag`

`IdPlasDoc` се пази за връщане към `setData`, а `IdSkld` се пази като оригинален склад на клиента и се използва при необслужени клиенти.
Ако `WorkTime` е подадено на два реда, например `08:00-13:00` и `16:00-18:00`, програмата пази и двата прозореца и ги подава към solver-а. Работното време и коментарът се виждат в индивидуалните route карти; клиентите с работно време са оцветени леко в червено в списъка.
При няколко документа за един и същ `IdCust` и същ GPS, настройката `enable_customer_document_grouping` определя поведението. Включена стойност означава едно посещение със сборен обем и отделни `setData` заявки за всеки `IdPlasDoc`. Изключена стойност означава всеки документ да остане отделен стоп.

### API сървър

Добавен е локален/отдалечен API сървър с POST endpoint:

```text
POST http://IP:8088/solve
GET  http://IP:8088/run
POST http://IP:8088/run
POST http://IP:8088/tsp
GET  http://IP:8088/tsp-report
GET  http://IP:8088/shutdown
```

`/solve` приема JSON клиенти и връща резултата след решаване. `/run` не очаква входни данни: само стартира оптимизацията с текущата конфигурация и връща веднага `202 started`. Ако вече има активен run, връща `409 already_running`.

`/tsp` е отделен режим за текущ маршрут на един шофьор. Той приема `driver_id`, текуща GPS позиция `driver_location`, optional `end_location`, клиенти, работно време, оборот, количество, номер на документ и коментар. Връща реда на доставка, ETA данни, генерира индивидуална HTML карта и при upload изпраща `pData2[]=driver_id` заедно с HTML файла.

Default service time и настройките за TSP подреждане се настройват в GUI: `API сървър -> TSP оптимизация`. Генерирането и качването на TSP HTML карта са отделно в `API сървър -> TSP HTML и upload`. Ако POST подаде `service_time_minutes`, `metric`, `tsp_local_html` или друга TSP настройка, тя има приоритет само за конкретната заявка.

TSP дневният Excel отчет се настройва отделно в GUI. Полетата са: включено/изключено, час на отчета, папка за отчети, файл на TSP дневника и дали да има подробен лист с клиентите. Ръчно генериране за текущия ден:

```cmd
curl "http://IP:8088/tsp-report"
```

За конкретна дата:

```cmd
curl "http://IP:8088/tsp-report?date=2026-05-29"
```

Спиране на API сървъра/програмата:

```cmd
curl "http://IP:8088/shutdown"
```

Алтернативно:

```cmd
curl "http://IP:8088/solve?cmd=shutdown"
```

Ако API-то е достъпно от мрежата, задължително задай `api.api_key`, защото `/shutdown` е remote stop команда.

Адресът за извикване не е хардкоднат само за TSP. Всички API примери използват общия адрес от `api.api_public_url`, а ако той е празен, сървърът/GUI-то показват автоматично засечения IP адрес на машината и `api.api_port`.

Имената на TSP полетата се настройват в GUI: `API сървър -> TSP полета във входната заявка`. Така външна система може да изпраща например `Coord` вместо `gps`, `DocNo` вместо `document` или `DriverNote` вместо `comment`, без да се променя кодът. Може да се зададат и няколко имена със запетая:

```text
gps,GPS,Coord,coordinates
comment,DeliveryComment,DriverNote,note
```

Trigger може да се извика и през `/solve` с query или JSON команда:

```text
cmd=run
cmd=start
cmd=trigger
cmd=solve_config
cmd=start_program
```

Адресът, портът и endpoint-ите се настройват в GUI. Сървърът може да слуша само локално или от мрежата според зададения host. За отдалечен достъп host трябва да е например `0.0.0.0`, а firewall/port forwarding трябва да позволяват връзката.

Пример:

```cmd
curl -X POST "http://IP:8088/solve" -H "Content-Type: application/json" -d "{\"customers\":[{\"GPS\":\"42.6977, 23.3219\",\"IdCust\":\"C001\",\"CustName\":\"Client 001\",\"Volume\":3,\"IdDoc\":\"D001\",\"IdPlasDoc\":\"P001\",\"IdSkld\":\"106\",\"WorkTime\":\"08:00-13:00\",\"DeliveryComment\":\"Обади се 10 мин преди доставка\"}]}"
curl "http://IP:8088/run"
curl -X POST "http://IP:8088/tsp" -H "Content-Type: application/json" -d "{\"driver_id\":\"BUS001\",\"driver_location\":\"42.7000,23.3000\",\"end_location\":\"42.7000,23.3000\",\"service_time_minutes\":8,\"customers\":[{\"id\":\"C001\",\"name\":\"Client 001\",\"document\":\"DOC001\",\"gps\":\"42.7100,23.3200\",\"work_time\":\"08:00-13:00\",\"turnover\":120.5,\"quantity\":5,\"comment\":\"Обади се 10 мин преди доставка\"}]}"
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

## Обобщение на възможностите

Текущата версия може да се управлява много по-пълно от GUI и API:

- Бусовете могат да имат име. Ако е зададено, то се показва в Excel, CSV и route картите.
- Всеки бус може да има отделна начална и крайна GPS точка. Ако крайна точка не е зададена, маршрутът завършва в стартовото депо.
- Могат да се добавят допълнителни депа и трафик зони през опростени GUI полета.
- Работно време на клиентите може да се чете от Excel/JSON и да се включва или изключва от GUI.
- Клиенти с няколко документа могат да се групират по `IdCust + GPS` в един стоп; настройката може да се изключи от GUI или API.
- API режимът поддържа `/run`, `/solve`, callback URL, JSON резултат и временни настройки само за конкретната заявка.
- През API могат да се подават бусове, депа, трафик зони, независими център зони с правила по тип бус, OSRM настройки, solver настройки, output пътища и `setData` настройки.
- `setData` има отделна настройка за необслужени клиенти: `set_data_unserved_done_flag`. Ако е празна, се използва общият `set_data_done_flag`.
- Output генераторът продължава работа, ако отделен файл не може да се създаде, и логва грешката без да спира останалите файлове.
- `objective_metric` може да се подаде през GUI, `/run` или `/solve` като `distance` или `time`.
- Индивидуалните route HTML карти могат да се качват към URL от настройката `output.route_maps_upload_url` чрез `output.route_maps_upload_mode = "effect_upload"`. Режим `disabled` не качва, а `legacy` запазва старото поведение.
- OR-Tools използва предварително сметнати vehicle cost матрици за по-малко Python callback overhead.
- PyVRP компресира еднаквите профили, без да губи различните депа на бусовете.
- При Excel вход Враца бусовете не се изключват автоматично само защото липсва `IdSkld`; тази автоматична филтрация важи само за HTTP JSON вход.

### Пример `/run` с настройки

```powershell
curl -X POST "http://IP:8088/run" -H "Content-Type: application/json" -d "{\"return_result\":true,\"settings\":{\"solver_type\":\"pyvrp\",\"objective_metric\":\"time\",\"time_limit_seconds\":180,\"set_data\":{\"enable_set_data_upload\":false,\"set_data_unserved_done_flag\":\"1975\"},\"output\":{\"enable_excel_output\":true,\"excel_output_dir\":\"D:\\\\CVRP_Output\\\\Run1\",\"route_maps_upload_mode\":\"effect_upload\",\"route_maps_upload_url\":\"https://YOUR-UPLOAD-SERVER/upload-files.php\",\"route_maps_upload_token\":\"YOUR_UPLOAD_TOKEN\"}}}"
```

Замени `IP` с адреса, който GUI-то показва в таб `API сървър`, или виж `public_url` от `GET /health`.

### Пример API center_zones

```json
{
  "settings": {
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
  }
}
```

### Пример vehicle с крайна точка

```json
{
  "vehicle_type": "internal_bus",
  "name": "Бус 1",
  "count": 1,
  "capacity": 385,
  "start_location": [42.700000, 23.300000],
  "end_location": [42.710000, 23.320000]
}
```

Ако `end_location` липсва или е празно, solver-ът използва стартовото депо като край.
