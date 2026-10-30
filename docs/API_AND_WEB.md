# HTTP API и Web интерфейс

Този документ описва реалното поведение на текущия source код на Bizant 2.0 / CVRP Optimizer. Endpoint-ите са конфигурируеми, затова примерите използват променлива `BASE_URL`.

```text
BASE_URL=http://HOST:PORT
```

При текущите фабрични стойности портът е `8070`, но за работещ build винаги провери `GET /health` или Desktop GUI → **API сървър**. В `GET /health` се връщат точните активни адреси, позволените настройки и примери за заявките.

## 1. Бърза справка

| Endpoint по подразбиране | Метод | Предназначение | Отговор |
|---|---|---|---|
| `/health` | `GET` | Състояние, адреси, текущ рън, възможности и schema на настройките | `200` JSON |
| `/run` | `GET`, `POST` | Стартира пълен CVRP рън с конфигурирания вход | `202`, или `200` при синхронен POST |
| `/run_saturday` | `GET`, `POST` | Стартира специален рън за съботата от текущата седмица | `202`, или `200` при синхронен POST |
| `/solve` | `POST` | Приема клиентите директно в body и връща пълното решение | `200` JSON |
| `/tsp` | `POST` | Подрежда оставащите клиенти на един шофьор | `200` JSON или HTML |
| `/tsp-report` | `GET`, `POST` | Генерира дневен TSP Excel отчет | `200` JSON |
| `/shutdown` | `GET`, `POST` | Спира API процеса и прави опит да спре активния subprocess | `200` JSON |
| `/hell` | `GET` | Защитен Web интерфейс | HTML/login |

Всички пътища могат да бъдат сменени в `APIConfig`. `/hell` също е примерна стойност: реалният Web адрес се връща като `endpoints.web_gui` от `/health`.

## 2. Стартиране на API сървъра

В source режим:

```powershell
python cvrp_api_server.py
```

В build директорията могат да се използват:

- `start_api_server.bat` — видим конзолен прозорец;
- `start_api_server_hidden.bat` — скрит API процес;
- `Bizant.exe --server` — директен старт на сървъра.

Сървърът остава активен след всяка заявка. Настройките `api_host`, `api_port`, `api_public_url` и endpoint-ите определят къде слуша и какъв публичен адрес показва.

## 3. Удостоверяване и сигурност

### 3.1. Публично API

Ако `api.api_key` е празен, публичните endpoint-и не изискват API ключ. Ако е попълнен, `/run`, `/run_saturday`, `/solve`, `/tsp`, `/tsp-report` и `/shutdown` приемат един от следните варианти:

```http
X-CVRP-API-Key: YOUR_KEY
```

```http
Authorization: Bearer YOUR_KEY
```

Поддържат се и `?api_key=...` или `?key=...`, но header е препоръчителен, защото query параметрите могат да останат в proxy/browser логове.

`GET /health` понастоящем е публичен и не проверява API ключ. Той не връща стойността на ключа, но показва endpoint-и, settings schema и състоянието на ръна.

Пример:

```powershell
curl.exe "$env:BASE_URL/health"
curl.exe "$env:BASE_URL/run" -H "X-CVRP-API-Key: YOUR_KEY"
```

### 3.2. Web интерфейс

Web интерфейсът не използва `api_key`. Той има отделни потребители и session удостоверяване:

- потребителите се управляват само от Desktop GUI → **API сървър → Уеб управление**;
- добавянето, смяната на парола и изтриването се записват веднага, без общия бутон „Запази“;
- паролата трябва да бъде поне 8 знака;
- данните са в `data/web_gui_auth.json` до програмата/build-а;
- паролите се пазят като PBKDF2-SHA256 hash, не като обикновен текст;
- Web сесията е в паметта и е валидна до 8 часа;
- промяна на парола или изтриване на потребител прекратява активните му сесии;
- след 5 неуспешни опита за комбинация IP/потребител или 25 опита от IP в 5 минути се връща временно ограничение;
- променящите POST операции изискват session cookie и CSRF token; ако заявката съдържа `Origin`, той също трябва да е допустим. Липсващ `Origin` сам по себе си не се отхвърля от текущия server.

При липсващ credential файл програмата създава ново празно хранилище; докато не бъде добавен потребител през Desktop GUI, никой не може да влезе. Нечетим или повреден credential файл води до `503`, вместо интерфейсът да се отвори без парола. При достъп от други компютри е силно препоръчително да се използва HTTPS/reverse proxy и правилно да се настрои `web_gui_trusted_proxy_ips`.

## 4. Формати на POST body

### 4.1. JSON

Стандартният и препоръчителен формат е:

```http
Content-Type: application/json; charset=utf-8
```

### 4.2. Form POST с параметър `pData`

За съвместимост се приема и `application/x-www-form-urlencoded`. JSON текстът може да бъде в някое от полетата:

```text
pData, payload, json, data, body
```

Пример:

```powershell
curl.exe -X POST "$env:BASE_URL/run_saturday" `
  -H "Content-Type: application/x-www-form-urlencoded" `
  --data-urlencode 'pData={"return_result":false}'
```

Ако няма нито едно от тези полета, се връща `400`.

### 4.3. Размери

- публичен API body: до 50 MiB;
- Web API body: до 1 MiB;
- Web login body: до 16 KiB.

По-голям body връща `413`.

## 5. `GET /health`

`/health` е едновременно health check и машинно четима справка. Връща:

- `service` — име, server time, listen/public URL;
- `endpoints` — точните активни адреси;
- `capabilities` — функции на `/run`, `/run_saturday`, `/tsp` и TSP отчета;
- `current_run`/`run_status` — последното състояние в паметта;
- `commands` — примери за endpoint-ите;
- `settings_schema` — позволени секции и полета за временен API override;
- обратносъвместими flat URL полета.

```powershell
curl.exe "$env:BASE_URL/health"
```

Основните стойности на `current_run.status` са:

| Стойност | Значение |
|---|---|
| `idle` | няма стартиран рън от този API процес |
| `running` | рънът работи |
| `stopping` | поискано е спиране |
| `stopped` | subprocess рънът е спрян |
| `completed` | завършил успешно |
| `failed` | завършил с грешка |

Състоянието се записва и в `logs/api_run_status.json`. След рестарт `/health` започва от новото in-memory състояние; файлът е диагностичен, а не опашка за автоматично възстановяване на рън.

## 6. `GET/POST /run`

`/run` използва `input.input_source` от текущата конфигурация:

- `excel` — чете конфигурирания Excel;
- `http_json` — извиква конфигурирания getData URL.

Не се подават клиентски записи. За клиенти директно в body използвай `/solve`.

### 6.1. Асинхронен старт

```powershell
curl.exe "$env:BASE_URL/run"
```

или:

```powershell
curl.exe -X POST "$env:BASE_URL/run" `
  -H "Content-Type: application/json" `
  --data-raw '{}'
```

Успешният асинхронен старт връща `202`:

```json
{
  "status": "started",
  "settings_overrides": [],
  "ignored_settings": [],
  "callback_url": null,
  "run": {
    "running": true,
    "status": "running",
    "run_id": "1a2b3c4d5e6f"
  }
}
```

Разрешен е само един активен `/run`, `/run_saturday` или Web рън за API процеса. При втори опит се връща `409 already_running` и текущото `run` състояние. Синхронният `/solve` не използва този lock и не трябва да се превръща в паралелна run queue.

### 6.2. Временни настройки само за този рън

```powershell
curl.exe -X POST "$env:BASE_URL/run" `
  -H "Content-Type: application/json" `
  --data-raw '{"settings":{"cvrp":{"solver_type":"pyvrp_experimental","objective_metric":"time","time_limit_seconds":300},"output":{"enable_excel_output":true},"set_data":{"enable_set_data_upload":false}}}'
```

Тези стойности не се записват в `config.py`. Отговорът показва:

- `settings_overrides` — приложените полета;
- `ignored_settings` — непознати, защитени или невалидни полета.

### 6.3. Синхронен рън с пълен JSON резултат

Само POST може да поиска изчакване:

```json
{
  "return_result": true,
  "settings": {
    "solver_type": "pyvrp",
    "objective_metric": "distance",
    "time_limit_seconds": 180
  }
}
```

След края се връща `200` с пълния резултат. Следните имена са еквивалентни на `return_result`: `return_json`, `wait`, `sync`, `include_result`.

### 6.4. Callback след асинхронен рън

```json
{
  "callback_url": "https://example.org/cvrp-complete",
  "settings": {
    "time_limit_seconds": 180
  }
}
```

Приема се и `notify_url`, `webhook_url` или `callback`. След завършване API прави POST JSON към URL-а с `status`, `success`, `run_id`, крайно време и наличната информация за резултат/грешка. Callback timeout е 15 секунди. При `return_result=true` се изпълнява синхронният клон и callback не се използва.

## 7. `GET/POST /run_saturday`

Това е специалният endpoint за съботен рън. Той винаги:

1. изчислява съботата от текущата календарна седмица (понеделник–неделя);
2. задава `input.json_override_date` във формат `DD/MM/YYYY` само за този рън;
3. заменя нормалния Excel bus prefix със `output.saturday_excel_bus_number_prefix`;
4. използва `output.saturday_excel_bus_number_digits`;
5. добавя `YYYY-MM-DD_събота` към генерираните имена на карти и Excel файлове;
6. запазва същите output директории — не създава задължително отделна папка.

Изключение при номерирането: ако `output.center_bus_numbering_enabled=true`, CENTER_BUS курсовете продължават да се номерират от `center_bus_numbering_start_id`. Съботният prefix/digits се прилага към останалото нормално номериране; специалната center серия има предимство.

Минимален старт:

```powershell
curl.exe -X POST "$env:BASE_URL/run_saturday" `
  -H "Content-Type: application/json" `
  --data-raw '{}'
```

Синхронен старт:

```json
{
  "return_result": true
}
```

Префикс само за конкретния съботен рън:

```json
{
  "settings": {
    "output": {
      "saturday_excel_bus_number_prefix": "100450121",
      "saturday_excel_bus_number_digits": 1
    }
  }
}
```

Важно: при `/run_saturday` полето е `saturday_excel_bus_number_prefix`, а не `excel_bus_number_prefix`. Endpoint-ът накрая прехвърля съботната стойност към активния normal prefix, без да отменя отделното center номериране.

Алтернативата `json_override_date: "current_week_saturday"` в обикновен `/run` изчислява динамичната дата и съботния suffix на файловете, но не превключва автоматично към отделния съботен bus prefix. За пълното поведение използвай `/run_saturday`.

В Web страницата има поле за глобалния съботен prefix, но текущият Web бутон **„Стартирай без запис“ извиква обикновен Web `/run`**, а не `/run_saturday`. Самият съботен рън се стартира през публичния endpoint.

## 8. `POST /solve`

`/solve` приема клиентите директно и изпълнява пълния workflow: входна обработка, складова логика, routing матрица, solver, карти/Excel/CSV/графики и по желание `setData`.

Това е синхронна заявка. Тя не участва в single-active-run статуса на `/run` и не трябва да се използва като паралелен заместител на run queue.

### 8.1. Допустим wrapper

Body може да бъде директен JSON списък или обект със списък под:

```text
customers, clients, orders, data, items, records
```

Ако няма предпочитан ключ, входният parser използва първия намерен list в обекта. За предвидимо поведение използвай `customers`.

### 8.2. Полета на клиент по подразбиране

| Поле | Значение |
|---|---|
| `GPS` | координати на клиента |
| `IdCust` | ID на клиента |
| `CustName` | име |
| `Volume` | количество/стекове |
| `IdDoc` | документ |
| `IdPlasDoc` | ID, използван при `setData` |
| `IdSkld` | изходен склад |
| `WorkTime` | един или повече работни прозорци |
| `DeliveryComment` | инструкция за доставка |
| `ServiceTimeMinutes` | optional време за обслужване на този клиент |
| `Mandatory` | optional hard-service флаг; `true` означава, че клиентът не може да бъде пропуснат |

Имената се настройват в `InputConfig`. За работно време, коментар, service time и задължителен клиент има вградени aliases с различни регистри/езици.

### 8.3. Пълен пример

```json
{
  "settings": {
    "solver_type": "pyvrp_experimental",
    "objective_metric": "time",
    "enable_customer_time_windows": true,
    "output": {
      "enable_excel_output": true,
      "enable_interactive_map": true
    },
    "set_data": {
      "enable_set_data_upload": false
    }
  },
  "customers": [
    {
      "IdCust": "C001",
      "CustName": "Клиент 1",
      "GPS": "42.6977,23.3219",
      "Volume": 5,
      "IdDoc": "D001",
      "IdPlasDoc": "P001",
      "IdSkld": "106",
      "WorkTime": "08:00-13:00\n16:00-18:00",
      "DeliveryComment": "Обади се 10 минути преди доставката",
      "ServiceTimeMinutes": 12,
      "Mandatory": true
    }
  ]
}
```

### 8.4. Няколко работни прозореца

Поддържат се и двата прозореца. Безопасни JSON варианти са:

```json
{"WorkTime":"08:00-13:00\n16:00-18:00"}
```

```json
{"WorkTime":"08:00-13:00 16:00-18:00"}
```

```json
{"WorkTime":["08:00-13:00","16:00-18:00"]}
```

В първия пример `\n` е escaped newline в стандартен JSON. API има tolerant decoder и приема и някои legacy body-та със суров newline/tab вътре в string, но това не е валиден стандартен JSON и не трябва да се използва за нова интеграция.

При невалидна част parser-ът я игнорира и записва warning. При включено `enable_customer_time_windows` пълните CVRP solver-и получават всички валидни прозорци; невъзможен клиент може да бъде пропуснат според настройките на решителя.

### 8.5. Индивидуално време за обслужване

`ServiceTimeMinutes` важи за конкретния клиент. При липсваща или невалидна стойност се използва service time на избрания бус. При групирани няколко документа за една физическа спирка и различни времена се избира по-голямата стойност.

Приемат се например:

```json
{"ServiceTimeMinutes":12}
{"service_time_minutes":"12.5"}
{"service_minutes":"00:15"}
```

Отрицателни, boolean и неразпознаваеми стойности се игнорират и задействат fallback към буса.

### 8.6. Абсолютно задължителен клиент

Default полето е `Mandatory`; приема `true/false`, `1/0`, `yes/no` и `да/не`. Поддържат се и aliases `Required`, `IsMandatory`, `IsRequired` и `MustServe`. При групиране на документи общото посещение става задължително, ако поне един документ е маркиран.

OR-Tools и PyVRP получават клиента като hard-required. VRP-Rust го получава без skip value. VROOM дава максимален приоритет и независимият audit отхвърля като невалиден всеки отговор, който все пак го оставя unassigned. Допълнителен централен invariant проверява резултата на всеки backend при приемане, преди файловете и преди `setData`; еднакви business ID не могат да маскират липсващо задължително посещение. Невъзможен задължителен клиент води до ясна грешка/невалидно решение, а не до тихо пропускане.

### 8.7. Групиране на документи

При `input.enable_customer_document_grouping=true` записи с еднакви `IdCust` и GPS се обединяват в един stop:

- количествата се събират;
- оригиналните документи се пазят в `grouped_documents`;
- коментарите се обединяват;
- при различно service time се използва по-голямото;
- при различни работни прозорци остава първият набор и се записва warning;
- `setData` може да изпрати оригиналните документи поотделно.

### 8.7. Резултат

Пълният JSON съдържа:

- `solver_requested`, `solver_used`, backend/version и fallback информация;
- execution time и общ брой клиенти;
- `routes_count`, `total_trips`, `second_trips_count`, `total_vehicles_used`;
- разстояние, време и fitness;
- `output_files`;
- `routes[]` с bus number, физически `vehicle_key`, `trip_number`, `route_id`, начало/край, клиенти и schedule;
- `dropped_customers[]`;
- `set_data`, ако е правен опит за upload;
- `settings_overrides` и `ignored_settings`, ако има временни настройки.

## 9. Временни API настройки

Изборът `solver_type="pyvrp_0141"` използва отделен worker с фиксирана версия 0.14.1 и същите правила като 0.14.0 (`pyvrp_experimental`). При техническа грешка няма автоматично преминаване към стара версия. Съществуващият отчетен OR-Tools fallback за неподдържани ограничения остава непроменен (виж [ограниченията на решителите](SOLVERS_AND_CONSTRAINTS.md)). `cvrp.pyvrp_0141_worker_path` е защитена локална настройка и не се приема от HTTP заявка. Таймаутът използва общото поле `pyvrp_next_worker_timeout_seconds`.

Настройките от `/run`, `/run_saturday`, `/solve` и `/tsp` са копие само за конкретната заявка. Те не записват `config.py`.

Поддържат се вложен и dotted стил:

```json
{
  "settings": {
    "cvrp": {"solver_type":"pyvrp","time_limit_seconds":180},
    "output": {"enable_excel_output":true}
  }
}
```

```json
{
  "settings": {
    "solver_type":"pyvrp",
    "time_limit_seconds":180,
    "output.enable_excel_output":true
  }
}
```

Допустимите секции са:

```text
cvrp, routing, osrm, valhalla, input, warehouse, locations, output, set_data, api
```

Има и помощни top-level ключове за `vehicles`, `replace_vehicles`, `vehicle_counts`, `vehicle_counts_by_id`, депа, center zones и traffic zones. Точният списък за стартирания build е в `/health.settings_schema`.

Worker executable пътищата `pyvrp_next_worker_path`, `vroom_worker_path` и `vrp_worker_path` са защитени и не могат да бъдат сменяни от HTTP заявка.

### 9.1. Временни бусове

- `vehicles` patch-ва ред по `config_id`; без `config_id` patch-ва всички редове от съответния `vehicle_type`;
- `replace_vehicles` подменя целия списък само за този рън;
- `vehicle_counts` задава брой по тип;
- `vehicle_counts_by_id` задава брой по стабилен `config_id`.

В API заявка start/end/reload могат да се задават чрез координати или чрез име на депо, например `start_depot_name` и `end_depot_name`.

### 9.2. Динамична дата и output tokens

`input.json_override_date` приема стандартно `DD/MM/YYYY`. API разпознава и:

```json
{"settings":{"input":{"json_override_date":"current_week_saturday"}}}
```

В output пътища, подадени изрично в същата заявка, могат да се използват:

```text
{run_date}          -> YYYY-MM-DD
{run_date_compact}  -> YYYYMMDD
```

## 10. `POST /tsp`

`/tsp` е отделен режим за един шофьор. Той не разпределя клиенти между бусове, а само подрежда подадения списък от текущата позиция до optional фиксиран край.

### 10.1. Основен пример

```json
{
  "driver_id": "BUS001",
  "driver_name": "Иван",
  "driver_location": "42.7000,23.3000",
  "end_location": "42.6950,23.3200",
  "start_time": "08:30",
  "metric": "time",
  "service_time_minutes": 8,
  "use_time_windows": true,
  "enable_two_opt": true,
  "customers": [
    {
      "id": "C001",
      "name": "Клиент 1",
      "document": "D001",
      "IdPlasDoc": "P001",
      "gps": "42.7100,23.3200",
      "quantity": 8,
      "turnover": 245.50,
      "work_time": "08:00-13:00\n16:00-18:00",
      "comment": "Обади се предварително"
    }
  ]
}
```

Задължителни са:

- `driver_id`;
- непразен списък `customers` с поне един валиден GPS.

`driver_location` и `end_location` са optional. При липсваща, `null` или празна начална локация се използва главното депо София от `locations.depot_location`. При липсваща, `null` или празна **крайна** локация маршрутът завършва при последния клиент, избран от PyVRP, без връщане до депото. Ако е подадена крайна локация, пътят до нея участва в оптимизацията, общите километри и времето. Невалидни непразни координати връщат грешка, вместо да се заменят мълчаливо. Началната точка не определя крайната. При свободен край отговорът съдържа `end_location: null` и `has_fixed_end_location: false`, а картата завършва при последния клиент.

Координатите могат да бъдат string `"lat,lon"`, списък `[lat,lon]` или object `{"lat":...,"lon":...}`.

### 10.2. Имена и aliases

TSP има конфигурируеми имена за driver ID/name/location, край, списък клиенти и клиентските id/name/document/GPS/quantity/turnover/work time/comment. Конфигурираното име се добавя пред вградените aliases; сравняването е case-insensitive.

Списъкът може да е в `customers`, `clients`, `orders`, `data`, `items` или `records`.

### 10.3. Цел и алгоритъм

Изборът на TSP версия е независим: `api.tsp_solver_type` или `tsp_solver_type` в JSON заявката, със стойности `pyvrp_experimental` (0.14.0, запазен default) и `pyvrp_0141` (0.14.1). Той не променя CVRP решителя или останалите правила. Версиите използват отделни постоянно заредени процеси.

`metric`/`objective`/`objective_metric` приема:

- `time` — използва duration матрицата;
- `distance` — използва distance матрицата.

Ако не е подадено, се използва `api.tsp_objective_metric`, а след това общата CVRP цел.

Алгоритъмът е:

- API заявките винаги имат фиксиран край: подадената точка или главното депо София;
- редът се създава от изолирания PyVRP 0.14 worker (ILS), а не от нашия предишен cheapest-insertion/2-opt алгоритъм;
- един бус, фиксиран час на тръгване, всяко посещение точно веднъж; алтернативните работни прозорци са задължителна група с избор на един прозорец;
- предпочита се маршрут без закъснения. Ако такъв не е намерен, се запазва най-добрият пълен кандидат на PyVRP според реалния график и наказанията за чакане/закъснение;
- наблюдателят само оценява предложени от PyVRP подредби: не пренарежда клиенти. Реалните ETA не използват вътрешния time-warp часовник;
- отговорът съдържа `solver`, `time_window_violations` и `late_minutes` за всеки клиент. Картата и логът показват закъсненията;
- при липсващ/несъвместим worker се връща техническа грешка, без скрито превключване към друг решител. Само нарушение на работното време не е грешка.

Настройки в body:

| Настройка | Aliases | Default от config |
|---|---|---|
| цел | `metric`, `objective`, `objective_metric`, `tsp_metric` | `api.tsp_objective_metric` |
| работни прозорци | `use_time_windows`, `time_windows`, `tsp_use_time_windows` | `api.tsp_use_time_windows` |
| тежест чакане | `wait_weight`, `time_window_wait_weight`, `tsp_wait_weight` | `api.tsp_time_window_wait_weight` |
| тежест закъснение | `late_weight`, `time_window_late_weight`, `tsp_late_weight` | `api.tsp_time_window_late_weight` |
| време за търсене (1–120 сек.) | `tsp_search_seconds` | `api.tsp_search_seconds`, 10 сек. при стар config |

Старите настройки за 2-opt се пазят за съвместимост, но вече не управляват TSP.

При стартиране на API сървъра PyVRP се зарежда в постоянен изолиран процес.
Връзката е през частни JSON pipes, без допълнителен мрежов порт. Всяка заявка
създава нов модел; едновременните заявки изчакват реда си и не споделят клиенти.
При прекъсване на процеса той се стартира отново и заявката се опитва още веднъж.
При спиране на сървъра или затваряне на parent pipe процесът приключва.
`/health` → `capabilities.tsp.worker` показва готовност, PID и брой готови заявки.
Началното зареждане не е част от всяка заявка. Общото време все пак включва
матрицата, търсенето и картата; при натоварване включва и чакане на ред.
Запазен е отделният TSP subprocess при едновременно пълно CVRP изчисление;
неговият PyVRP процес е локален за тази subprocess заявка.

Работните прозорци в TSP са част от score и графика. Алгоритъмът чака до следващ подходящ прозорец, а закъснението получава penalty. Това не е hard feasibility модел като пълния CVRP solver: късен stop може да остане в реда и да се върне с `time_window_status` „След работно време“.

Няколко прозореца се подават по същия начин както при `/solve`: escaped `\n`, интервал или JSON list. В отговора се показва кой прозорец е използван.

### 10.4. Време за обслужване в TSP

За клиент без индивидуално обслужване TSP избира базовото време в следния ред:

1. `service_time_minutes` от body;
2. `api.tsp_default_service_time_minutes`;
3. service time на избрания `vehicle_type`, само за по-стари config-и без TSP default;
4. общ CVRP fallback.

Индивидуалното клиентско обслужване има приоритет. При липса се използва зададеното време/формула според текущите настройки; PyVRP получава същите изчислени секунди, които се използват в крайния график.

`start_time`/`current_time`/`departure_time` е optional. При липса се използва текущият локален час на машината.

### 10.5. Невалидни клиенти и групиране

- запис, който не е JSON object, се връща в `skipped_customers`;
- клиент с липсващ/невалиден GPS също се връща там;
- ако няма нито един валиден клиент, заявката връща `400`;
- quantity/turnover с невалидна стойност стават `0`;
- невалидно работно време се игнорира с warning.

При включено `input.enable_customer_document_grouping` записи с еднакви customer ID и GPS стават един stop. Количеството и оборотът се събират, а документите се връщат в `delivery_order[].documents`. При различни time windows за групирани документи се пази първият набор.

### 10.6. Routing и truck профили

TSP използва избрания общ routing engine. Ако `driver_id` съвпада с конфигуриран Valhalla truck профил:

- матрицата и геометрията използват Valhalla `truck`;
- прилагат се height, width, length, weight, axle load/count, hazmat и HGV penalty;
- при недостъпна Valhalla има fallback към OSRM.

Traffic zones променят duration матрицата. Следователно те влияят пряко при `metric=time`; при `metric=distance` разстоянието остава основната матрична цена, но duration продължава да участва в ETA и time-window оценката.

### 10.7. JSON или HTML отговор

Форматът може да се избере чрез body/query:

```text
response, response_type, return_type, format, tsp_response_format
```

- `json` — връща JSON;
- `html` — връща HTML картата като response body.

`return_json=true` принудително избира JSON. Ако нищо не е подадено, се използва `api.tsp_response_format`.

JSON резултатът съдържа:

- driver и metric;
- реално използваните TSP настройки;
- start/end и `has_fixed_end_location`;
- общи разстояние, време, quantity и turnover;
- `skipped_customers`;
- `delivery_order[]` с sequence, customer, документи, GPS, arrival/departure, работно време, статус, distance, travel/wait и comment;
- `map_file` и `map_upload`;
- `tsp_process` — `inline` при свободен CVRP или `subprocess`, когато има активен CVRP рън.

Когато пълен CVRP рън работи, `/tsp` остава достъпен чрез отделен worker с `api.tsp_worker_timeout_seconds`. Timeout или worker crash връща `500` с път до диагностичния лог.

### 10.8. Локална карта и upload

Това са три отделни действия:

1. HTML body към заявителя;
2. локален HTML файл;
3. upload на локалния файл.

Локалният файл се управлява чрез:

```text
generate_map, generate_html_map, generate_local_html_map,
local_html_map, tsp_generate_map, tsp_local_html
```

Upload се управлява чрез:

```text
upload_map, upload_html_map, tsp_upload_map, tsp_upload_html_map
```

URL/token/mode могат временно да се подадат като `upload_url`, `upload_token`, `upload_mode` и техните aliases. При upload driver ID се изпраща като bus ID (`pData2[]` при Effect режима).

Upload изисква локален файл. `upload_map=true` принудително включва генерирането му. Само HTML response може да бъде върнат без запис на диск.

## 11. `GET/POST /tsp-report`

Всяка успешна `/tsp` заявка се записва в JSONL история. `/tsp-report` генерира Excel отчет за избрана дата:

```powershell
curl.exe "$env:BASE_URL/tsp-report"
curl.exe "$env:BASE_URL/tsp-report?date=2026-07-19"
```

POST вариант:

```json
{"date":"2026-07-19"}
```

Приема се и `report_date`. Форматът е `YYYY-MM-DD`; при липса се използва текущият ден. Отговорът съдържа `date` и `report_file`. В текущия handler невалидна дата през POST се връща като `400`, а невалидна дата през GET минава през общия report exception handler и се връща като `500`; интеграцията не трябва да изпраща друг формат.

Автоматичният дневен отчет се настройва чрез:

- `api.tsp_daily_report_enabled`;
- `api.tsp_daily_report_time` (`HH:MM`);
- output директория и history файл;
- включване на подробния лист с stops.

## 12. `/shutdown` и командни aliases

Предпочитаният начин е:

```powershell
curl.exe -X POST "$env:BASE_URL/shutdown" -H "X-CVRP-API-Key: YOUR_KEY"
```

Сървърът връща `shutting_down`, след което спира API процеса и прави опит да прекрати активния subprocess tree.

За обратна съвместимост `/solve` разпознава `cmd`, `command` или `action`:

```json
{"command":"run"}
```

Trigger стойности:

```text
run, start, trigger, solve_config, start_program
```

Shutdown стойности:

```text
shutdown, stop, stop_program, exit, quit
```

Поддържа се и `GET /solve?cmd=run` или `GET /solve?cmd=shutdown`. За нови интеграции използвай директно `/run`, `/run_saturday` и `/shutdown`.

## 13. Web интерфейс

### 13.1. Достъп

Отвори `endpoints.web_gui` от `/health`, например:

```text
http://bizant/hell
```

Без валидна сесия основната страница пренасочва към `/hell/login`. След вход се виждат:

- разбираемо състояние на планирането;
- отделни карти/панели за бусовете;
- отделни раздели „Тестове“ и „Основни настройки“;
- източник на заявки, дата, метод и цел за планиране;
- output файлове и upload;
- `setData`/необслужени/makeGroup;
- live logs;
- действия за старт, стоп, shutdown и logout.

Списъкът с бусове е постоянно разгънат и е общ за двата раздела. Секциите
**„Заявки за доставка“**, **„Изчисление и правила“**, **„Карти и отчети“** и
**„Изпращане на резултатите“** са сгъваеми панели и първоначално са затворени;
отвори само панела, който редактираш. Свиването на панел не изтрива или нулира
стойностите във формата.

### 13.2. „Стартирай тест“ — без автоматично записване

Основният бутон е в раздел „Тестове“. Използва текущата форма, включително:

- `input`: източник, Excel файл/лист, адрес/метод за получаване, складове, статус, дата, допълнителни условия, изчакване и групиране на документи;
- `cvrp`: solver, objective, waiting-in-time, multiple trips, time limit и фините настройки на всички solver-и;
- `output`: карти, Excel, CSV, chart, имена/пътища, съботен bus prefix и route-map upload;
- `set_data`: обслужени/необслужени, URL/method, DoneFlag, IdSkld, IdGrafik/Bukva шаблони и makeGroup.

Web рънът се изпълнява в изолиран subprocess. Формата не променя `config.py`. При избор на solver се виждат само неговите панели: PyVRP quality/penalties, допълнителният 0.14 worker, OR-Tools strategy/LNS/GLS и паралелни strategy списъци, VROOM threads/exploration или VRP-Rust threads/generations. Скритите стойности не се губят.

Routing, депа, зони, имената на входните полета и списъкът с бусове остават общи. При незаписани промени по бусовете страницата не позволява старт, докато те не бъдат записани или отхвърлени.

Преди всеки старт има обобщение и потвърждение: настройки, бусове, вход, дата, цел, качване на карти, изпращане към системата и папка за отчети. Качването на карти е независимо от `setData`. При включено `setData` се показва изрично предупреждение. Ако запазените настройки или автопаркът са променени от друг супервайзор транспорт след зареждане на страницата, `expected_revision` спира старта и изисква обновяване.

Динамичната събота се запазва като символична стойност и се изчислява при изпълнение. За този старт се използват съботните номера на бусовете и означението `_събота` в имената на файловете.

### 13.3. Два независими записа

**„Запази настройките“** в „Тестове“ записва отделен Web профил. Той не променя основните input/cvrp/output/set_data стойности, автопарка или публичните `/run` и `/solve`.

**„Запази основните настройки“** записва показаните input/cvrp/output/set_data полета в `config.py`. Те важат за локално изпълнение и публичното API. Вече записаният Web профил остава непроменен. Преди запис се създава backup в `config_backups`.

Този бутон не записва бусовете. Той също не е интерфейс за API host/endpoints или Web потребители — те се управляват от Desktop GUI.

**„Върни записаните настройки“** отхвърля само редакциите в активния раздел. Смяната между двата раздела пази незаписаните им редакции отделно. Основният раздел няма видим основен бутон за старт: „Допълнителни действия“ съдържа старт със записаните основни настройки и отделно изключване на програмата.

### 13.4. „Запази бусовете“

Подменя само списъка `VehicleConfig` в `config.py` и не променя solver, output, `setData` или други секции.

Всеки Web bus panel има:

- тип, име, active и count; стабилното `config_id` се пази вътрешно;
- capacity и fixed cost;
- max hours, max km/day и customers/day;
- service time и start time (часът се въвежда като `08:30`, а се записва в минути);
- **начално депо само като избор по име**;
- optional end GPS;
- reload GPS и reload minutes за multiple trips.

Web записът отхвърля `start_location` GPS и изисква `start_depot_name`. Това предпазва началото от разминаване с именуваните депа и `setData`. Край и reload могат да останат GPS според текущата имплементация.

### 13.5. Stop и shutdown

- **Спри планирането** прекратява Web subprocess-а, но оставя API сървъра активен;
- **Изключи програмата** в допълнителните действия прекратява API процеса и активния subprocess;
- **Изход** прекратява само текущата Web сесия.

### 13.6. Вътрешни Web endpoint-и

Те са за страницата, не са заместител на публичното integration API:

| Път спрямо `/hell` | Метод | Функция |
|---|---|---|
| `/login` | `GET` | login HTML |
| `/api/login` | `POST` | username/password, създава session |
| `/api/logout` | `POST` | прекратява session |
| `/api/config` | `GET` | връща двата набора настройки, общия автопарк и revision |
| `/api/config` | `POST` | глобален запис без бусове |
| `/api/vehicles` | `POST` | запис само на бусовете |
| `/api/customer-exclusions` | `POST` | запис на общите изключени клиентски номера и отделното им направление |
| `/api/run` | `POST` | изолиран Web рън с `run_settings` |
| `/api/status` | `GET` | run status |
| `/api/logs` | `GET` | последни редове от логовете |
| `/api/session` | `GET` | текущ username |
| `/api/stop-run` | `POST` | спира активния subprocess |
| `/api/shutdown` | `POST` | спира програмата |
| `/api/run-defaults` | `POST` | записва независим Web профил |

Всички освен login изискват валидна Web сесия; state-changing POST заявките изискват CSRF.

### 13.7. Съвместимост и съхранение на Web профила

Новият профил се пази в `api.web_gui_run_defaults_json` като `{"version":2,"settings":{...}}`. Записва се пълен набор от разрешените оперативни настройки, без превозни средства и общите изключени клиенти/направление. Паролата/кодът за качване не се връща в отговорите към браузъра; връща се само индикатор дали е зададен. Премахването му от Web профила не възстановява скрито основния код.

Старите неактивни записи без версия не се активират автоматично след обновяване. До първото натискане на „Запази настройките“ порталът зарежда основните стойности. Публичното API никога не използва Web профила. Вътрешният `/api/run` приема `profile="web"` (по подразбиране) или `profile="global"`; временните `run_settings` се прилагат само в копие на избраната конфигурация.

## 14. HTTP status и типични грешки

| HTTP | Типичен случай |
|---|---|
| `200` | health, `/solve`, `/tsp`, report, синхронен `/run`, успешен Web action |
| `202` | асинхронен `/run` или `/run_saturday` е стартиран |
| `303` | Web redirect към login/основна страница |
| `400` | празен/невалиден JSON, невалидна настройка, дата или задължително TSP поле |
| `401` | липсващ/невалиден API key или Web session |
| `403` | невалиден Web Origin или CSRF token |
| `404` | непознат endpoint |
| `409` | вече има активен `/run`/Web рън |
| `413` | body над разрешения размер |
| `415` | Web login не е `application/json` |
| `429` | временно ограничение след неуспешни Web login опити |
| `500` | solver/routing/output/TSP worker грешка |
| `503` | Web credential store липсва или е повреден |

Стандартна грешка:

```json
{
  "status": "error",
  "error": "Описание на грешката"
}
```

Невалиден JSON добавя `detail`. Неизвестни временни настройки не винаги прекратяват заявката: те могат да се върнат в `ignored_settings`, затова интеграцията трябва да проверява това поле.

## 15. Практически checklist за интеграция

1. Извикай `/health` и вземи точните endpoint-и.
2. Задай API key, ако server-ът не слуша само на localhost.
3. Изпращай UTF-8 `application/json`.
4. За два работни прозореца използвай escaped `\n` или JSON list.
5. Проверявай `ignored_settings`.
6. Използвай `config_id`, когато patch-ваш конкретен бус.
7. За съботната дата и prefix използвай `/run_saturday`.
8. За customer-specific service time използвай пълния CVRP вход, не `/tsp`.
9. Не приемай `200` само по себе си като гаранция, че няма dropped/skipped клиенти — провери съответните масиви и counts.
10. При Web управление пази `data/web_gui_auth.json` заедно с build/config backup-а.

## 16. Имплементационни източници

Основните места в source кода, срещу които е проверен документът:

- `cvrp_api_server.py:82-1704` — endpoint-и, settings, dynamic Saturday date и prefix;
- `cvrp_api_server.py:1707-2752` — Web полета, temporary/global save и vehicles-only save;
- `cvrp_api_server.py:3708-3900` — API key и `/health`;
- `cvrp_api_server.py:3933-4692` — run status, callback, TSP worker, stop/shutdown;
- `cvrp_api_server.py:4706-5380` — HTTP handlers, кодове, body parsing и Web endpoint-и;
- `current_tsp.py:48-245` — TSP request/response;
- `current_tsp.py:332-411` — TSP клиенти и работни прозорци;
- `current_tsp.py:601-766` — TSP metric, service time и настройки;
- `current_tsp.py:900-1415` — матрица, фиксиран край, 2-opt и schedule;
- `input_handler.py:44-101` — aliases за работно време, коментар и service time;
- `input_handler.py:191-227` — customer service time fallback;
- `input_handler.py:399-499` — един или повече работни прозорци;
- `input_handler.py:840-935` — `/solve` JSON mapping;
- `main.py:667-790` — пълният CVRP JSON резултат;
- `web_gui_auth.py:1-18`, `313-355`, `596-742` — credential store, rate limiting и sessions.
