# Build инструкции за CVRP_Optimizer.exe

Този документ описва как се създава Windows EXE версията на проекта.

## Какво build-ваме

Build процесът използва `build_exe.py` и PyInstaller, за да създаде:

```text
..\dist\CVRP_Optimizer.exe
..\dist\start_cvrp.bat
..\dist\Settings.bat
..\dist\config.py
```

EXE entry point-ът е `main_exe.py`, не директно `main.py`.

## Препоръчителен build workflow

Отвори PowerShell в папката на проекта:

```powershell
cd "C:\Programming\Bizant 2.0\cvrp-ortools-optimizer"
```

Създай/обнови виртуалната среда:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller
```

Провери критичните зависимости:

```powershell
.\.venv\Scripts\python.exe -c "import ortools; print('ortools', ortools.__version__)"
.\.venv\Scripts\python.exe -c "import pyvrp; print('pyvrp ok')"
.\.venv\Scripts\python.exe -c "import main, main_exe, config_gui; print('imports ok')"
```

Стартирай build-а:

```powershell
.\.venv\Scripts\python.exe build_exe.py
```

## Какво прави `build_exe.py`

1. Проверява зависимости.
2. Инсталира липсващи зависимости, ако е нужно.
3. Открива пътищата до OR-Tools и PyVRP.
4. Генерира `CVRP_Optimizer.spec`.
5. Генерира `file_version_info.txt`.
6. Стартира PyInstaller със spec файла.
7. При неуспех пробва fallback build с директни PyInstaller опции.
8. Създава `start_cvrp.bat` и `Settings.bat`.
9. Копира `config.py` в `dist`.

## Очаквана структура след build

```text
Bizant 2.0/
  cvrp-ortools-optimizer/
    build_exe.py
    CVRP_Optimizer.spec
    config.py
    ...
  dist/
    CVRP_Optimizer.exe
    config.py
    start_cvrp.bat
    Settings.bat
    data/
      input.xlsx
```

Забележка: `build_exe.py` използва `..\dist`, тоест `dist` е една папка над project folder-а.

## Стартиране след build

Постави входния файл тук:

```text
..\dist\data\input.xlsx
```

Стартиране:

```powershell
cd "C:\Programming\Bizant 2.0\dist"
.\start_cvrp.bat
```

Настройки:

```powershell
.\Settings.bat
```

## Работа без EXE

В development папката `start_cvrp.bat` и `Settings.bat` имат fallback:

- ако има `CVRP_Optimizer.exe`, стартират EXE;
- ако няма EXE, стартират през `.venv\Scripts\python.exe`.

Това позволява да работиш веднага с Python режим, без build.

## Чести build грешки

### OR-Tools не е инсталиран

Провери:

```powershell
.\.venv\Scripts\python.exe -m pip show ortools
.\.venv\Scripts\python.exe -c "from ortools.constraint_solver import pywrapcp; print('ok')"
```

Ако командите работят, но build-ът пада, вероятно PyInstaller не намира hidden imports. Увери се, че build-ът се пуска със същия Python:

```powershell
.\.venv\Scripts\python.exe build_exe.py
```

Не използвай глобален `python build_exe.py`, ако пакетите са инсталирани във `.venv`.

### PyInstaller липсва

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
```

### `CVRP_Optimizer.exe` не се появява

Провери:

- има ли грешка в конзолата;
- има ли `..\build` и `..\dist`;
- дали antivirus не блокира EXE файла;
- дали командата е пусната от project folder-а;
- дали `main_exe.py`, `config.py`, `output_handler.py` се импортват успешно.

### Грешка от PyVRP

В `requirements.txt` PyVRP е pin-нат към:

```text
pyvrp>=0.13.3
```

Ако се инсталира несъвместима версия, върни правилната:

```powershell
.\.venv\Scripts\python.exe -m pip install "pyvrp>=0.13.3"
```

### Грешка от pandas/numpy/protobuf

Пусни:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

Ако има конфликт, най-чистият вариант е нова виртуална среда:

```powershell
Remove-Item -Recurse -Force .venv
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller
```

Използвай тази команда за изтриване само ако си сигурен, че `.venv` е локалната среда на проекта.

## Какво трябва да има в build-а

PyInstaller spec-ът включва hidden imports за:

- `ortools`
- `ortools.constraint_solver`
- `ortools.constraint_solver.pywrapcp`
- `ortools.constraint_solver.routing_enums_pb2`
- `pyvrp`
- `pandas`
- `numpy`
- `openpyxl`
- `folium`
- `requests`
- `matplotlib`
- `tkinter`

Ако при build се появи `ModuleNotFoundError`, липсващият модул трябва да се добави в hidden imports в `build_exe.py`/`CVRP_Optimizer.spec`.

## Runtime файлове

В папката до EXE трябва да има:

- `CVRP_Optimizer.exe`
- `config.py`
- `start_cvrp.bat`
- `Settings.bat`
- `data/input.xlsx`, ако се работи с Excel файл;
- директории `output/`, `logs/`, `cache/` се създават при нужда.

## Проверка на готовия EXE

От `dist`:

```powershell
.\CVRP_Optimizer.exe --settings
```

или:

```powershell
.\Settings.bat
```

После:

```powershell
.\start_cvrp.bat
```

Ако възникне проблем, първо провери `logs/cvrp.log`.

## Какво влиза в новия build

Build-ът включва:

- `CVRP_Optimizer.exe` за основното решаване;
- Settings GUI за промяна на всички настройки;
- API server модул за POST заявки към `/solve` и trigger заявки към `/run`;
- `start_api_server.bat` за видим старт на API сървъра;
- `start_api_server_hidden.bat` за скрит старт на API сървъра без отворен CMD прозорец;
- настройки за PyVRP, OR-Tools fallback, OSRM/Valhalla, групиране на документи, `setData` и `makeGroup`.

API сървърът работи и в build режим. След като приеме една POST заявка, връща резултата и остава активен за следваща заявка. `GET/POST /run` стартира програмата с текущата конфигурация без входен payload и връща веднага `202 started`.

## API настройки в EXE режима

В Settings GUI могат да се сменят:

- host, порт, `/solve` endpoint и trigger endpoint на API сървъра;
- дали сървърът да слуша само локално или от мрежата;
- URL за Bizant GET/POST;
- URL и метод за `setData`;
- дали да се изпраща `makeGroup`;
- дали да се изпращат необслужени клиенти към `setData`.

За достъп от друга машина host трябва да позволява външни връзки, например `0.0.0.0`, и Windows firewall трябва да допуска избрания порт.

## setData в build режима

След успешно решение EXE версията може да изпраща:

- `cmd=setData` за обслужените клиенти;
- `cmd=setData` за необслужените клиенти, ако отметката е включена;
- `cmd=makeGroup` по веднъж за всеки склад, ако всички `setData` заявки са успешни и отметката е включена.

За обслужени клиенти `Bukva` е във формат `БХ{маршрут}-{клиент}`, например `БХ1-1`.

За необслужени клиенти `Bukva` е във формат `HOF-{id_plas_doc}`, например `HOF-12345`. `IdSkld` за тях се взема от оригиналното GET поле `IdSkld`.

## Отчети в build режима

Excel отчетът показва:

- време в часове;
- текст “бусове”;
- ID на буса във формат `1004501001`, `1004501002`, ...;
- отделна колона с номер на маршрут `1`, `2`, `3`, ...

CSV структурата не е променяна.

## Нови неща в build-а

Build-ът включва и последните runtime възможности:

- `CVRP_Optimizer.exe --server` стартира API сървъра.
- `start_api_server.bat` стартира API сървъра във видим прозорец.
- `start_api_server_hidden.bat` стартира API сървъра скрито.
- `/run` може да стартира оптимизация с текущата конфигурация.
- `/run` и `/solve` могат да приемат временни `settings`, без да променят постоянно `config.py`.
- `return_result=true` връща пълния JSON резултат в отговора.
- `callback_url`, `notify_url` или `webhook_url` изпращат POST известие след успешен или неуспешен background run.
- `objective_metric` може да избира между оптимизация по километри (`distance`) и по време (`time`).
- OR-Tools използва предварително сметнати vehicle cost матрици, а PyVRP компресира еднакви профили.
- Route картите и Excel отчетът могат да показват ETA пристигане, очаквано тръгване, чакане и time-window статус.
- Общата карта има GPS търсачка за временни пинове по координати.
- Индивидуалните route HTML карти могат по избор да се качват към Effect upload endpoint чрез `output.route_maps_upload_mode = "effect_upload"`.

## PyInstaller бележки

`build_exe.py` съдържа workaround за Windows/PyInstaller случаи, при които build-ът стига до края, но пада при:

```text
set_exe_build_timestamp
update_exe_pe_checksum
```

Тези стъпки не променят бизнес логиката на програмата. Ако Windows или antivirus държи EXE файла заключен, първо затвори стартиран `CVRP_Optimizer.exe`, CMD прозорци и API сървъра, после пусни build-а отново.

## setData за необслужени клиенти

В build режима вече има отделен DoneFlag за необслужени клиенти:

```python
set_data_unserved_done_flag = ""
```

Празна стойност означава: използвай общия `set_data_done_flag`. Ако се зададе например `1975`, само необслужените клиенти ще се изпратят с `DoneFlag=1975`.
