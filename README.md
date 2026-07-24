# CVRP Optimizer / Bizant 2.0

Windows приложение за ежедневно разпределяне и подреждане на доставки с реални пътни матрици, различни бусове и депа, работни прозорци, зони, втори курсове, карти, Excel отчети и Bizant интеграция.

Поддържани backend-и:

- стабилен PyVRP 0.13.x, минимум 0.13.4 (`pyvrp`);
- изолиран PyVRP 0.14 worker (`pyvrp_experimental`);
- OR-Tools (`or_tools`);
- official VROOM 1.15 worker (`vroom`);
- експериментален VRP-Rust 1.24 worker (`vrp`).

## Документация

| Ръководство | Съдържание |
|---|---|
| [Индекс на документацията](docs/README.md) | Навигация и термини. |
| [Ръководство за работа](docs/USER_GUIDE.md) | Desktop GUI, Web GUI и ежедневен workflow. |
| [HTTP API и Web интерфейс](docs/API_AND_WEB.md) | Endpoint-и, JSON тела, scopes, auth и грешки. |
| [Решители и ограничения](docs/SOLVERS_AND_CONSTRAINTS.md) | Backend матрица, objectives, зони, прозорци и втори курсове. |
| [Конфигурационен справочник](docs/CONFIGURATION_REFERENCE.md) | Всички секции и полета в `config.py`. |
| [Инсталация, build и outputs](docs/INSTALLATION_BUILD_OUTPUTS.md) | Source среда, worker-и, EXE, карти, Excel, upload и `setData`. |
| [Работни процеси](WORKFLOWS_DOCUMENTATION.md) | Вътрешен поток на данните и алгоритмите. |
| [Обяснение без технически жаргон](ОБЯСНЕНИЕ_НА_ПРОГРАМАТА.md) | Какво прави програмата от гледна точка на диспечер. |
| [PyVRP 0.14 backend](PYVRP_EXPERIMENTAL.md) | Изолирана среда, worker и диагностика. |

`GET /health` на стартирания build е runtime справочникът за активните endpoint-и, solver-и и допустими API настройки.

## Основни възможности

- Excel или HTTP JSON вход с конфигурируеми имена на полетата.
- Групиране на документи със същия `IdCust + GPS` в едно посещение.
- Клиентски работни прозорци, включително няколко интервала на отделни редове.
- Клиентско време за обслужване с fallback към времето от конкретния бус.
- Различен капацитет, брой, fixed cost, дневно време, километри и брой клиенти по конфигурация на бус.
- Именувани начални депа, отделна крайна точка и reload депо.
- Динамични втори/следващи курсове в рамките на общите дневни лимити.
- Основна center зона и неограничен брой допълнителни `circle/polygon` зони.
- Приоритетни типове бусове, zone discounts, outside penalties и penalties за ограничени типове.
- Независим switch дали всяка center/traffic зона да се вижда на картата.
- OSRM или Valhalla distance/duration матрица, curbside и Valhalla truck/time-dependent настройки.
- Objective по разстояние или време.
- Разрешено/забранено пропускане, индивидуална защита чрез penalty/prize и абсолютно задължителни клиенти чрез `Mandatory`.
- Паралелни solver рънове с различни seed/strategy конфигурации, когато backend-ът го позволява.
- Обща карта, една индивидуална карта на физически бус с филтър по курсове, Excel, CSV и графики.
- Upload на индивидуални HTML карти.
- `setData` за обслужени/необслужени клиенти и последващ `makeGroup`.
- HTTP API, защитен Web GUI, TSP endpoint и дневен TSP Excel отчет.
- Специален `/run_saturday` с динамична съботна дата, отделен bus prefix и `_събота` в output имената. При включено специално center номериране `center_bus_numbering_start_id` има предимство за CENTER_BUS.
- Windows Settings GUI, Task Scheduler и EXE build с companion worker-и.

## Бърз старт от source

Изисква се съвместим Windows Python за основната `.venv`:

```powershell
cd "D:\Iliyan\bizant_source"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Старт:

```powershell
.\Settings.bat
.\start_cvrp.bat
.\start_api_server.bat
```

Директни Python entry points:

```powershell
.\.venv\Scripts\python.exe config_gui.py
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe cvrp_api_server.py
```

Допълнителните solver-и изискват изолирани среди/worker-и. Виж [build ръководството](docs/INSTALLATION_BUILD_OUTPUTS.md).

## Вход

### HTTP JSON

Типичен клиент:

```json
{
  "IdCust": "1000001",
  "CustName": "Клиент 1",
  "GPS": "42.6977,23.3219",
  "Volume": 10.5,
  "IdDoc": "DOC001",
  "IdPlasDoc": "PLAS001",
  "IdSkld": "106",
  "WorkTime": "08:00-13:00\n16:00-18:00",
  "DeliveryComment": "Обади се 10 минути предварително.",
  "ServiceTimeMinutes": 6,
  "Mandatory": true
}
```

`WorkTime` с два реда е валиден, когато новият ред е JSON escape `\n`. Всички solver адаптери получават нормализираните прозорци според собствените си възможности.

`ServiceTimeMinutes` е optional. При липса се използва `service_time_minutes` от буса, който обслужва клиента.

`Mandatory=true` прави клиента абсолютно задължителен. Той не получава skip penalty и никое валидно решение не може да го остави необслужен. При невъзможни GPS, обем или общ капацитет run-ът спира с ясна грешка вместо да го премести към склада.

### Excel

Имената на колоните се настройват от Desktop GUI. Минимално са нужни валиден ID, GPS и положителен обем. Работното време, коментарът и колоната `Задължителен` са optional.

## Бусове и втори курсове

Един `VehicleConfig` задава правила и `count`. При `enable_multiple_trips=true` solver-ът може да върне физическия бус в reload депото и да създаде следващ курс.

- `capacity` важи за един курс;
- `max_time_hours`, `max_distance_km` и `max_customers_per_day` са общи за деня;
- `reload_time_minutes` се начислява между курсовете;
- броят курсове се определя от изпълнимостта, не от предварително число;
- всеки курс има отделен output bus number;
- една индивидуална карта групира курсовете на физическия бус и ги филтрира;
- Основният Excel отчет добавя само бизнес колоната **Курс** при multi-trip. CSV е технически export и в multi-trip режим включва още `ID бус`, `ID курс` и `Ключ бус` за машинна корелация.

VROOM изисква вторите курсове да са изключени. Провери [solver ограниченията](docs/SOLVERS_AND_CONSTRAINTS.md) преди смяна на backend-а.

## Зони

Center зоните могат да са кръг или полигон. Всяка допълнителна зона определя:

- `enabled`;
- `show_on_map`;
- приоритетни и ограничени vehicle types;
- отстъпка за приоритетния бус;
- outside penalty;
- penalty по тип бус.

`show_on_map=false` скрива само геометрията. Solver правилото остава активно, докато `enabled=true`.

Traffic зоните умножават duration стойностите. Те също имат независима видимост.

## Solver и routing

OSRM/Valhalla строят матриците. Solver-ът после използва избрания `objective_metric`:

- `distance` — основната пътна цена идва от distance матрицата;
- `time` — основната пътна цена идва от duration матрицата и backend-specific time objective логиката.

Капацитет, работно време, time windows, max distance, max customers, крайни точки и reload остават ограничения независимо от objective-а, доколкото избраният backend ги поддържа.

Не приемай, че една и съща числова fitness стойност е сравнима между различни solver-и. Сравнявай обслужени клиенти, валидност, реални километри/време и еднакъв input/config.

## HTTP API

Default портът идва от `api.api_port`; използвай URL-а от `GET /health`.

| Endpoint | Метод | Действие |
|---|---|---|
| `/health` | GET | Runtime статус, URLs, schema и команди. |
| `/run` | GET/POST | Run с текущия configured input; допуска request-local settings. |
| `/run_saturday` | GET/POST | Run за съботата от текущата седмица със съботното номериране. |
| `/solve` | POST | Клиенти и настройки директно в JSON. |
| `/tsp` | POST | Подреждане на текущ маршрут на един шофьор. |
| `/tsp-report` | GET/POST | Дневен TSP Excel отчет. |
| `/shutdown` | GET/POST | Контролирано спиране; защити го с API key. |
| `/hell` | браузър | Web GUI, ако е включен; endpoint-ът е конфигурируем. |

Пример за нормален background run:

```powershell
curl.exe -X POST "http://SERVER:PORT/run" `
  -H "Content-Type: application/json" `
  --data-raw '{"settings":{"solver_type":"pyvrp_experimental","objective_metric":"distance"}}'
```

Пример за събота без JSON body:

```powershell
curl.exe -X POST "http://SERVER:PORT/run_saturday"
```

Пример за синхронен `/solve`:

```json
{
  "settings": {
    "solver_type": "pyvrp",
    "objective_metric": "time",
    "set_data": {
      "enable_set_data_upload": false
    }
  },
  "customers": [
    {
      "IdCust": "1",
      "GPS": "42.6977,23.3219",
      "Volume": 10,
      "WorkTime": "08:00-13:00\n16:00-18:00"
    }
  ]
}
```

Настройките в публичните `/run` и `/solve` са временни и не записват `config.py`.

## Web GUI и scopes

Web потребителите се създават от Desktop Settings. Credentials се пазят в `data/web_gui_auth.json` като salted PBKDF2 записи; Web интерфейсът използва session cookie, CSRF защита и login throttling.

Web GUI разделя:

- **текущ рън без записване**;
- **глобални настройки** в `config.py`, без бусовете;
- **бусове**, записвани отделно от всички останали секции.

Текущият Web рън изпраща временните solver/output/`setData` стойности, но използва последно записаните вход, routing, депа, зони и бусове. Незаписаните редакции по бусове не участват. Legacy `web_gui_run_defaults_json`/`run-defaults` още съществува в кода, но текущата страница не го предлага и стартът не го прилага.

## Изход

Рънът може да създаде:

- обща HTML карта;
- индивидуални HTML карти по физически бус;
- един Excel workbook с отделни sheets за маршрути, необслужени, обобщение и статистика;
- CSV;
- PNG графики;
- JSON резултат през API;
- TSP HTML/JSON и дневен TSP Excel отчет.

При втори курсове индивидуалната карта е обща за физическия бус. Курсовете имат различни номера и филтър с отделен списък клиенти.

При `/run_saturday` output директориите не е необходимо да се сменят. Към date stamp-а се добавя `_събота`, а Excel/карта файловете остават различими от нормалния рън. Съботният prefix важи за нормалното номериране; при `center_bus_numbering_enabled=true` CENTER_BUS запазва отделната серия от `center_bus_numbering_start_id`.

## `setData` и upload

След валидно решение по желание се изпращат:

- обслужени документи чрез `cmd=setData`;
- необслужени документи с отделен DoneFlag/Bukva/IdGrafik;
- `cmd=makeGroup` по използван склад, след успешните `setData` заявки.

Индивидуалните карти могат независимо да бъдат качени като multipart файлове със съответните bus IDs. За първи тест остави интеграционните switches изключени.

## Build

Основна команда:

```powershell
.\.venv\Scripts\python.exe build_exe.py
```

По желание друга директория:

```powershell
$env:CVRP_DIST_DIR = "D:\Iliyan\dist_candidate"
.\.venv\Scripts\python.exe build_exe.py
```

Пълният build изисква и трите подготвени изолирани worker среди. Той се счита за успешен само ако включи и провери главния EXE и worker-ите в `pyvrp-next`, `vroom` и `vrp-rust`; при липсващ/неуспешен worker build процесът прекратява release-а като непълен. Не променяй production `config.py`, освен ако изрично не искаш да разпространиш нови defaults.

## Сигурност и диагностика

- При `api_host=0.0.0.0` настрой `api_key`.
- За Web GUI извън защитена LAN използвай HTTPS reverse proxy.
- Не публикувай API keys, upload token-и и `data/web_gui_auth.json`.
- `409 already_running` означава, че вече има основен CVRP рън.
- Липсващ companion worker означава грешна/непълна `dist` папка или неподготвена source среда.
- PyVRP 0.14 може да използва много памет при голям `num_workers`; настрой конкретен безопасен брой.
- Проверявай `logs/cvrp.log`, `logs/cvrp_api_server.log`, run-specific API логовете и `logs/api_run_status.json`.
- Общият hard-constraint audit може да отхвърли кандидат от PyVRP/VROOM/VRP-Rust, който нарушава ограничения. Директният OR-Tools използва native dimensions и проверки при извличане на решението. Отделният mandatory invariant е fail-closed за всички backend-и и се повтаря преди output/`setData`.

## Основни модули

| Файл | Роля |
|---|---|
| `main.py`, `main_exe.py` | Оркестрация и entry points. |
| `config.py`, `config_gui.py` | Модел и Desktop Settings. |
| `input_handler.py`, `warehouse_manager.py` | Вход, нормализация и склад. |
| `osrm_client.py`, `valhalla_client.py` | Матрици и route geometry. |
| `cvrp_solver.py` | OR-Tools и общите route модели. |
| `pyvrp_solver.py`, `pyvrp_next_runtime.py` | PyVRP 0.13/0.14. |
| `vroom_solver.py`, `vroom_runtime.py` | VROOM adapter/worker bridge. |
| `vrp_solver.py`, `vrp_runtime.py`, `vrp_rust_prototype.py` | VRP-Rust adapter/worker bridge. |
| `current_tsp.py`, `tsp_daily_report.py` | TSP и дневни отчети. |
| `output_handler.py`, `vehicle_numbering.py` | Карти, Excel/CSV и numbering. |
| `setdata_client.py` | Bizant `setData`/`makeGroup`. |
| `cvrp_api_server.py`, `web_gui_auth.py` | HTTP API, Web GUI и auth. |
| `build_exe.py`, `build_*_worker.py` | Главен build и companion worker-и. |

## Проверка след промяна

Тестовете са в `tests`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

След промяна на API или документацията провери още:

1. `GET /health` и всички URL-и;
2. входен JSON с два работни прозореца;
3. run с и без втори курсове;
4. скрита, но активна зона;
5. normal и `/run_saturday` output имена;
6. Web run без записване, global save и vehicles-only save;
7. build worker self-checks.
