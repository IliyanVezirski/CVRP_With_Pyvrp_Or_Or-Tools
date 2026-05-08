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

Valhalla режимът може да се използва за time-dependent routing, ако има работещ Valhalla сървър.

## Стъпка 2: CVRP решаване

Избор:

```python
cvrp.solver_type = "or_tools"
cvrp.solver_type = "pyvrp"
```

### OR-Tools workflow

Модул: `cvrp_solver.py`

OR-Tools моделът включва:

- location индекси;
- vehicle индекси;
- capacity dimension;
- time dimension;
- vehicle-specific transit callbacks;
- disjunction penalties за пропускане на клиенти;
- ограничения за брой клиенти, време и капацитет;
- depot mapping.

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
- profiles по тип бус;
- service time в edge duration;
- prizes/penalties за пропускане на клиенти;
- извличане на route резултат в общия `CVRPSolution` формат.

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

Отделните route карти имат сгъваем клиентски панел:

```text
Клиенти -> списък -> клик върху клиент -> popup + навигация
```

### Excel

Основният Excel съдържа:

- маршрути;
- необслужени клиенти;
- summary;
- статистики по бусове.

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
