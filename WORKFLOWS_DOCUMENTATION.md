# Работни процеси и алгоритми

Този документ описва как реално минава една оптимизация от входния Excel/JSON до финалните HTML, Excel и CSV файлове.

## Общ workflow

```text
Потребител / Scheduler / start_cvrp.bat
  -> main.py или main_exe.py
  -> config.py
  -> input_handler.py
  -> warehouse_manager.py
  -> osrm_client.py или valhalla_client.py
  -> cvrp_solver.py или pyvrp_solver.py
  -> output_handler.py
  -> output/, logs/
```

Основните стъпки в `main.py` са:

1. Подготовка на данни.
2. Изчисляване на матрица с разстояния.
3. Решаване на CVRP.
4. Обработка на резултата.
5. Генериране на изходни файлове.

## Стъпка 1: Подготовка на данни

Модул: `input_handler.py`

Входът може да бъде:

- Excel файл;
- HTTP JSON endpoint.

За всеки клиент се създава `Customer`:

```python
Customer(
    id=...,
    name=...,
    coordinates=(lat, lon),
    volume=...,
    original_gps_data=...,
    document=...
)
```

GPS parser-ът приема координати като текст и връща `(latitude, longitude)`. Невалидните координати се логват и не се използват в solver-а.

Ако входът съдържа няколко реда за един и същ клиент и една и съща GPS точка, `input_handler.py` може да ги групира в едно посещение. Това е включено по подразбиране чрез `input.enable_customer_document_grouping = True`. Solver-ът получава един клиент със сборен обем, но `grouped_documents` пази оригиналните документи, `IdPlasDoc`, `IdSkld` и обемите, за да могат отчетите и `setData` да работят по документи. Ако настройката е изключена, всеки документ остава отделен клиент/стоп.

HTTP JSON режимът:

- добавя дата към URL;
- използва ръчно зададена дата, ако има;
- иначе изчислява следващ работен ден;
- пробва няколко encoding режима;
- поддържа JSON list или object с list ключ.

## Стъпка 1.1: Предварително складово разпределение

Модул: `warehouse_manager.py`

Целта е да се отделят заявки, които не трябва или не могат да влязат в solver-а.

Логиката отчита:

- максимален обем за клиент;
- капацитет на най-големия бус;
- общ капацитет на активните бусове;
- `capacity_toleranse`;
- сортиране по обем и разстояние;
- център зона.

Важно: warehouse manager работи преди OR-Tools/PyVRP и подава към solver-а само клиентите за бусове.

## Стъпка 1.5: Матрица с разстояния

Модули:

- `osrm_client.py`
- `valhalla_client.py`

Матрицата включва:

- всички уникални депа;
- всички клиенти, които са останали за бусове.

OSRM режимът:

- първо проверява централен кеш;
- ако няма кеш, прави Table API заявки;
- за средни dataset-и използва batch заявки;
- попълва междублокови връзки по посока;
- използва fallback само при нужда.

Важна корекция: A->B и B->A вече не се копират симетрично. Това е важно при еднопосочни улици, завои, забрани и различни времена по посока.

Valhalla режимът може да се използва за time-dependent routing, ако има работещ Valhalla сървър. Ако Valhalla върне липсваща клетка (`None` distance/time), програмата попълва приблизителна fallback стойност и продължава, вместо batch-ът да счупи целия run.

## Стъпка 2: CVRP решаване

Избор:

```python
cvrp.solver_type = "or_tools"
cvrp.solver_type = "pyvrp"
cvrp.objective_metric = "distance"  # най-къси километри
cvrp.objective_metric = "time"      # най-кратко време
```

`objective_metric` определя коя цена минимизира solver-ът. И двата solver-а използват една и съща входна distance/duration матрица, но при `time` основната цена е duration матрицата, а глобите/fixed cost стойностите се преобразуват към същия мащаб.

### OR-Tools workflow

Модул: `cvrp_solver.py`

OR-Tools моделът включва:

- location индекси;
- vehicle индекси;
- capacity dimension;
- time dimension;
- vehicle-specific transit callbacks;
- precomputed vehicle cost matrices за arc cost;
- disjunction penalties за пропускане на клиенти;
- ограничения за брой клиенти, време и капацитет;
- depot mapping.

OR-Tools вече не пресмята пълната бизнес цена на всяка дъга при всяко питане от solver-а. За всеки vehicle предварително се смята cost matrix, в която са включени objective по време/км, service time, center zone penalties и fixed cost мащабиране. По време на търсенето callback-ът само връща готово число от таблица.

Времето за обслужване е по конкретен бус:

```text
internal_bus -> service_time_minutes на internal_bus
center_bus   -> service_time_minutes на center_bus
...
```

Това е по-точно от стария подход със средно време.

### PyVRP workflow

Модул: `pyvrp_solver.py`

PyVRP моделът включва:

- отделни депа;
- clients;
- vehicle types;
- компресирани profiles за еднакви vehicle правила;
- service time в edge duration;
- prizes/penalties за пропускане на клиенти;
- извличане на route резултат в общия `CVRPSolution` формат.

PyVRP profile compression споделя един profile между бусове, които имат еднакъв service time и еднакви правила за center zone цена. Стартовото и крайното депо не са част от profile signature, защото PyVRP ги пази на vehicle type; така различните депа остават коректни.

PyVRP и OR-Tools използват еднакъв output contract, така че `output_handler.py` не се интересува кой solver е използван.

## Паралелно решаване

Ако `enable_parallel_solving = True` и има повече от едно CPU ядро, `main.py` стартира няколко worker-а.

Всеки worker получава:

- едно и също warehouse allocation;
- една и съща distance matrix;
- различна solver стратегия;
- отделен worker id.

След края се избира валидно решение. При равни условия се предпочита по-добър fitness score и обслужен обем.

## Приоритетно пропускане на клиенти

Функция:

```python
calculate_customer_drop_penalties(...)
```

Идеята е, когато не всички заявки могат да се вместят, solver-ът да предпочете за пропускане:

- големи заявки;
- близки до депо заявки.

Причината е оперативна: голяма и близка заявка е по-лесна за отделна доставка или складова обработка от малка далечна заявка, която би развалила маршрута.

Настройки:

```python
enable_priority_dropping
drop_volume_weight
drop_closeness_weight
min_customer_drop_penalty
max_customer_drop_penalty
```

При OR-Tools това влияе на `AddDisjunction` penalty.

При PyVRP това влияе на prize/penalty логиката.

## Център зона

Център зоната се определя от:

```python
is_location_in_center_zone(...)
```

Режими:

- `circle`: център + радиус;
- `polygon`: начертан полигон.

GUI поток за polygon:

1. В таб `Локации` се избира `polygon`.
2. Натиска се `Чертай на карта`.
3. Отваря се локална Leaflet карта.
4. Потребителят чертае/редактира полигон.
5. Полигонът се записва в `center_zone_polygon`.

Тази зона се използва и в solver логика, и във визуализацията.

## Депа

Има вградени депа:

- `Главно депо`;
- `Център`;
- `Враца`.

Могат да се добавят и допълнителни:

```python
depot_locations = {
    "Ново депо": (42.123456, 23.123456),
}
```

GUI формат:

```text
Ново депо: 42.123456, 23.123456
```

Всеки тип бус може да избере начално депо по име.

## Output workflow

Модул: `output_handler.py`

Генерира:

- обща карта;
- отделни route карти;
- Excel report;
- CSV routes;
- charts.

### HTML карти

Картите поддържат:

- Folium/Esri tiles;
- Google Maps;
- депа;
- център зона;
- route geometry от OSRM/Valhalla;
- fallback прави линии;
- посока на движение;
- popup-и;
- navigation links.
- ETA пристигане за клиентите.
- GPS търсачка в общата карта за временни пинове по координати.
- optional upload на индивидуалните route HTML файлове към Effect endpoint.

Отделните route карти имат сгъваем клиентски панел:

```text
Клиенти -> списък -> ETA -> клик върху клиент -> popup + навигация
```

### Excel

Основният Excel съдържа:

- маршрути;
- необслужени клиенти;
- summary;
- статистики по бусове.
- ETA пристигане, чакане и time-window статус, когато има schedule данни.

Колоната `Посока на движение` показва от коя спирка към коя спирка се движи маршрутът.

### CSV

CSV файлът остава със стабилно име:

```text
output/routes.csv
```

Това е умишлено, за да не се чупят външни процеси.

### Дата в имената

Дата се добавя към:

- HTML обща карта;
- отделни route HTML карти;
- Excel файлове.

Пример:

```text
interactive_map_2026-04-28.html
route_1_2026-04-28.html
cvrp_report_2026-04-28.xlsx
```

## Scheduler workflow

GUI табът `Автоматично стартиране` използва Windows `schtasks`.

Вече могат да се създават няколко schedule задачи. В GUI има поле `Име на задача`; всяко различно име създава отделна Windows Task Scheduler задача. Примерни имена:

```text
CVRP_Optimizer_Auto_1
CVRP_Optimizer_Auto_2
CVRP_Optimizer_Auto_Morning
```

Ако името е същото, задачата се обновява. Ако името е различно, старата задача остава и се добавя нова.

В EXE режим Scheduler стартира `start_cvrp.bat`, ако го намери.

В Python режим Scheduler стартира:

```text
python main.py
```

спрямо текущата project директория.

## Runtime режими

### Python режим

```powershell
.\.venv\Scripts\python.exe main.py
```

### Settings режим

```powershell
.\.venv\Scripts\python.exe config_gui.py
```

### Batch режим

```powershell
.\start_cvrp.bat
.\Settings.bat
```

### EXE режим

```powershell
.\CVRP_Optimizer.exe
.\CVRP_Optimizer.exe --settings
```

## Какво да гледаш при проблем

1. `logs/cvrp.log`
2. дали `.venv\Scripts\python.exe` е правилният Python;
3. дали `ortools` и `pyvrp` се импортват;
4. дали OSRM/Valhalla сървърът отговаря;
5. дали input Excel има правилните колони;
6. дали output директориите са writable;
7. дали новите HTML карти са регенерирани след промени по визуализацията.

## Нов работен поток с Bizant API

### 1. Вземане на клиенти

Клиентите могат да се вземат от Bizant чрез настройваем URL:

```text
http://sio.effect.bg:7080/lubiv_Bizant?cmd=getData&Date=YYYY-MM-DD&Sklad=106&DoneFlag=1973
```

Датата остава по старата логика: ако не е зададена ръчно в GUI, програмата я изчислява автоматично.

От GET отговора се четат стандартните клиентски полета плюс:

- `IdPlasDoc` за връщане към `setData`;
- `IdSkld` като оригинален склад на заявката.

При няколко документа за един и същ `IdCust` и същ GPS, включеното групиране прави един стоп със сборен обем. Това намалява изкуственото дублиране на спирки, но не губи документите: при `setData` се изпраща отделна заявка за всеки оригинален `IdPlasDoc`.

### 2. Решаване през GUI или API

Решението може да се стартира от основната програма или чрез API:

```text
POST /solve
```

API сървърът остава активен след всяка заявка и чака следваща. Host, порт и endpoint се сменят от GUI.

### 3. Генериране на файлове

От GUI може да се включва/изключва отделно:

- Excel отчет;
- CSV;
- интерактивни карти;
- chart-ове.

В Excel отчета времето е в часове, използва се “бусове”, има ID на буса от серията `1004501001...` и отделна колона за номер на маршрут.

### 4. Връщане към Bizant със setData

Ако `setData` е включено, след успешно решение се изпраща заявка за всеки обслужен клиент:

```text
cmd=setData&IdPlasDoc=...&DoneFlag=...&IdSkld=...&Bukva=...&IdGrafik=...
```

За обслужени клиенти:

- `IdPlasDoc` идва от GET;
- `IdSkld` се определя по депото/буса;
- `Bukva` е `БХ{route_number}-{stop_number}`;
- `IdGrafik` може да бъде Excel номерът на буса.

### 5. Необслужени клиенти

Ако настройката е включена, необслужените клиенти също се изпращат към `setData`.

За тях:

- `IdSkld` се взема от оригиналния GET запис на клиента;
- `Bukva` е `HOF-{id_plas_doc}`;
- `IdGrafik` се задава от отделно GUI поле/шаблон.

### 6. makeGroup

Накрая, ако всички `setData` заявки са успешни и отметката е включена, програмата изпраща:

```text
cmd=makeGroup&IdSkld=...
```

Командата се изпраща по веднъж за всеки различен склад, последователно.

## Крайна точка на бус

Всеки `VehicleConfig` вече има:

```python
start_location = (...)
end_location = None
```

Ако `end_location` е зададена, OR-Tools и PyVRP получават различен краен depot index за този тип бус. Ако е празна, крайната точка пада обратно към `start_location`.

Това влияе на:

- матрицата, защото крайните координати се включват към списъка с депа;
- solver модела, защото start и end depot могат да са различни;
- преизчисляването на километри и време след решението;
- Excel, CSV и HTML route картите.

Когато крайната точка е различна от стартовата, TSP reorder-ът не пренарежда маршрута като затворен цикъл към стартовото депо, а запазва реда от solver-а и преизчислява метриките към реалната крайна точка.

## Работно време на клиентите

Клиентите могат да имат работно време от Excel или JSON. Поддържа се общо поле във формат:

```text
08:00 - 16:00
08:00-13:00
```

или с няколко реда:

```text
08:00-13:00
16:00-18:00
```

Работното време се подава само през едно поле, например `WorkTime` в JSON или колоната `Работно време` в Excel. При подадени два реда програмата използва първия прозорец, защото текущата конфигурация на solver-ите работи с един прозорец на клиент. Ако клиентът няма работно време, се използва default прозорецът от настройките. Ако `enable_customer_time_windows = False`, solver-ите игнорират тези прозорци, но работното време пак се показва в индивидуалните route карти.

Коментар към доставката се чете от `DeliveryComment` или близки fallback имена като `DeliveryNote`, `Comment`, `Note`. Коментарът се показва в индивидуалните route карти, popup-ите, Excel/CSV отчетите и JSON резултата.

## API управление на настройките

API-то може да стартира програмата по два начина:

- `POST /solve` приема клиентски записи и настройки в същото body.
- `GET/POST /run` стартира текущо конфигурирания вход, но може да приеме временни настройки.

Настройките могат да се подават така:

```json
{
  "return_result": true,
  "settings": {
    "solver_type": "pyvrp",
    "objective_metric": "time",
    "time_limit_seconds": 180,
    "vehicles": [
      {
        "vehicle_type": "internal_bus",
        "name": "HELL 1",
        "count": 1,
        "capacity": 385,
        "start_location": [42.695785, 23.231659],
        "end_location": [42.700000, 23.400000]
      }
    ],
    "set_data": {
      "enable_set_data_upload": false,
      "set_data_unserved_done_flag": "1975"
    }
  }
}
```

`settings` може да съдържа solver, OSRM/Valhalla, output, депа, трафик зони, center зона, бусове и `setData` настройки. Override-ите важат само за заявката и не записват автоматично `config.py`.

За качване на индивидуалните HTML карти през API:

```json
{
  "settings": {
    "output": {
      "route_maps_upload_mode": "effect_upload",
      "route_maps_upload_url": "https://effect.bg/dragon/hellbizante/upload-files.php",
      "route_maps_upload_token": "Effect-Bizante-Token",
      "route_maps_upload_file_field": "files[]"
    }
  }
}
```

`disabled` изключва качването. `legacy` запазва старото поведение без нов HTTP upload.

Кратки aliases, които са удобни за API:

```json
{
  "settings": {
    "solver": "or_tools",
    "objective": "distance",
    "optimize_by": "time",
    "time_limit": 300,
    "routing_engine": "osrm",
    "group_customer_documents": true
  }
}
```

При Excel вход програмата не изключва автоматично Враца бусовете заради липсващ `IdSkld`. Автоматичното скриване на Враца бусове по склад се прилага само при `input_source = "http_json"`, където `IdSkld` идва от Bizant.

## JSON резултат

Когато `return_result=true`, `/run` изчаква края на оптимизацията и връща JSON резултат. Това е удобно за външна система, която иска веднага да получи маршрути, необслужени клиенти, файлове и summary.

При background run може да се подаде:

```json
{
  "callback_url": "https://example.com/cvrp-finished"
}
```

След края API сървърът изпраща POST към този URL със статус `completed` или `failed`.
