# Работни процеси и алгоритми

Този документ описва реалния поток в текущия source код. За потребителски инструкции виж [Ръководство за работа](docs/USER_GUIDE.md), а за точна solver матрица — [Решители и ограничения](docs/SOLVERS_AND_CONSTRAINTS.md).

## 1. Архитектура

```text
Desktop GUI / config.py / Web / HTTP API
                  |
                  v
          input_handler.py
                  |
                  v
       warehouse_manager.py
                  |
                  v
  osrm_client.py / valhalla_client.py
                  |
                  v
  OR-Tools / PyVRP / VROOM / VRP-Rust
                  |
                  v
 native OR-Tools checks / shared adapter audit
                  |
                  v
 central mandatory invariant (all backends)
                  |
                  v
 output_handler.py -> maps / Excel / CSV / charts
                  |
                  v
       setdata_client.py -> setData / makeGroup
```

`main.py` е оркестраторът. `main_exe.py` избира режимите на замразения EXE. `cvrp_api_server.py` управлява публичните HTTP заявки, Web GUI, run subprocess-ите и статусите.

## 2. Конфигурационни scopes

### 2.1 Постоянна конфигурация

`config.py` съдържа `MainConfig`. Desktop GUI записва всички свои позволени секции и създава backup. Web GUI има отделно:

- глобален запис на видимите solver/output/`setData` настройки, без бусовете;
- глобален запис само на списъка `VehicleConfig`;
- потребители, записвани веднага в `data/web_gui_auth.json`.

### 2.2 Request-local API override

`/run` и `/solve` deep-copy-ват базовата конфигурация и прилагат само приетите настройки за конкретната заявка. Отговорът различава `settings_overrides` и `ignored_settings`. `config.py` не се променя.

### 2.3 Web run без запис

Web страницата изпраща временни `cvrp`, `output` и `set_data` стойности към изолиран child process. Input, routing, депа, зони и бусове идват от последно записания global config. Незапазени редакции по бусовете не участват.

Legacy `web_gui_run_defaults_json` и вътрешният save endpoint още съществуват, но текущата Web страница не ги предлага и Web стартът не смесва този preset.

## 3. Нормален CVRP run

1. Зарежда се активният `MainConfig` или request-local copy.
2. Избира се Excel/HTTP/директен JSON вход.
3. Клиентските редове се нормализират и валидират.
4. По желание документи със същия клиент/GPS се групират.
5. Невалидните и предварително складовите заявки се отделят.
6. Изгражда се единен ред на депата и клиентите.
7. Routing engine-ът връща distance и duration матрици.
8. Активните traffic zones преобразуват duration стойностите.
9. Dispatcher-ът стартира избрания solver/backend.
10. При portfolio режим няколко worker-а решават с различни seeds/strategies.
11. PyVRP/VROOM/VRP-Rust кандидатите минават общия независим audit; директният OR-Tools разчита на native dimensions и extraction-time проверки.
12. Всеки backend минава централен mandatory invariant по детерминирано посещение и брой.
13. Избира се най-добрият валиден резултат.
14. Mandatory invariant се повтаря непосредствено преди output и преди `setData`.
15. Маршрутите се преобразуват към карти/Excel/CSV/JSON.
16. По желание се изпълняват route-map upload, `setData` и `makeGroup`.

## 4. Входен workflow

### 4.1 Excel

`input_handler.py` чете настроения лист и mapping на колоните. В текущата версия Excel mapping включва ID, име, GPS, обем, документ, работно време и коментар, но няма отделна колона за индивидуално клиентско service time. За Excel то идва от буса.

### 4.2 HTTP JSON

Заявката използва URL, метод, `cmd`, дата, `Sklad`, `DoneFlag` и допълнителни query параметри. Декодирането опитва подходящи кодировки и има tolerant path за някои външни отговори с raw control characters.

Надеждният формат остава валиден JSON. Два работни прозореца се изпращат така:

```json
{
  "WorkTime": "08:00-13:00\n16:00-18:00"
}
```

### 4.3 Директен `/solve`

Body може да бъде списък клиенти или wrapper с `customers`, `clients`, `orders`, `data`, `items` или `records`. Wrapper-ът може да съдържа request-local `settings`.

### 4.4 Нормализация на клиент

За solver-а се пазят:

- ID, име, документ/документи и оригинален склад;
- GPS координати;
- обем;
- всички валидни времеви прозорци;
- коментар;
- optional индивидуално service time.

Невалидна част от работния график се игнорира с warning. Ако не остане валиден прозорец, клиентът се приема без специфичен прозорец. Настройките `customer_time_window_default_start_minutes/end_minutes` в момента не променят този parser workflow.

### 4.5 Групиране на документи

Ключът е клиентски ID + координати. При групиране:

- обемите се сумират;
- solver-ът вижда един stop;
- документите остават отделни за report/`setData`;
- ако service times се различават, използва се по-голямото валидно време.

### 4.6 Невалидни и складови заявки

Липсващ GPS, непарсваема координата или невалиден обем не стигат до solver-а. Складовият модул може допълнително да отдели големи/неизпълними заявки според `WarehouseConfig`.

## 5. Депа и matrix workflow

### 5.1 Единен ред на точките

`build_ordered_depots()` започва с главното депо и добавя уникалните start/end точки. При включени втори курсове добавя и reload точките. След депата се добавят клиентите.

Индексите трябва да останат еднакви в:

- distance матрицата;
- duration матрицата;
- solver client/depot моделите;
- route extraction-а и output-а.

### 5.2 OSRM

Table API връща метри и секунди. Batch-овете пазят посоката: A→B и B→A се изчисляват отделно. При недостъпна клетка може да има fallback приблизителна стойност според настройките.

OSRM cache key в текущия код не version-ва надеждно server URL, profile и всички routing modes. След смяна на OSRM данни/profile изчисти cache-а или го дръж изключен, докато това не бъде коригирано.

### 5.3 Valhalla

Valhalla може да работи с time-dependent дата/час, curbside/preferred side и truck dimensions. `null` клетки се заменят индивидуално с fallback, вместо да спират целия run.

### 5.4 Traffic zones

След суровата матрица активните traffic правила умножават duration arcs. Тази операция е еднаква преди всички backend-и. `show_on_map` не участва — то управлява само визуализацията.

## 6. Solver dispatch

| `solver_type` | Workflow |
|---|---|
| `or_tools` | Директен Python OR-Tools модел. |
| `pyvrp` | Стабилен PyVRP 0.13.x (минимум 0.13.4) в основната `.venv`. |
| `pyvrp_experimental` | JSON IPC към изолиран PyVRP 0.14 worker. |
| `vroom` | JSON IPC към official VROOM/pyvroom worker. |
| `vrp` | JSON IPC към VRP-Rust/vrp-cli worker. |

VROOM и VRP-Rust използват собствена вътрешна паралелизация, затова външният portfolio mode се изключва. OR-Tools и PyVRP могат да използват няколко outer worker процеса.

При `num_workers=-1` текущият избор е приблизително CPU ядра минус едно. `performance.max_workers` и `memory_limit_mb` не налагат строг runtime cap.

## 7. Objectives

### 7.1 `distance`

Основната транспортна цена е distance матрицата. Върху нея backend-ът добавя fixed costs, skip penalties/prizes и center zone soft costs.

### 7.2 `time`

Основната транспортна цена е duration. При OR-Tools/PyVRP `time_objective_include_waiting=true` оценява пълния shift span: travel + service + waiting + reload. При `false` чакането остава ограничение/отчет, без пряка objective цена.

VROOM и VRP-Rust имат собствена time objective семантика. Числовият fitness между различни engines не е директно сравним.

### 7.3 Center zone разлика

Center zone правилата са soft, не абсолютни забрани. В pure shift-duration `time` режим multiplicative zone discount може да няма ефект при OR-Tools/PyVRP, когато edge cost е нулирана; additive penalties могат да останат. VROOM прилага зоните върху custom time cost, а VRP-Rust не представя center zones в `time` режим.

Ако зоната е критично бизнес предпочитание в текущата версия, `distance` дава най-последователна семантика между backend-ите.

## 8. Constraints workflow

### 8.1 Capacity

Demand се натрупва по route. При reload капацитетът се възстановява само за следващия курс.

### 8.2 Работен ден

Start time + travel + service + waiting + reload формират дневния span. Максимумът е vehicle-specific. Post-solve audit допуска до 1 минута rounding tolerance.

### 8.3 Клиентски прозорци

- OR-Tools използва общия диапазон и забранява интервалите между прозорците.
- PyVRP 0.13 използва alternative clients; 0.14 използва group semantics зад същия adapter.
- VROOM и VRP-Rust получават native списъци от прозорци.

### 8.4 Фиксиран край

Start/end индексите се подават в native vehicle/shift модела. Пътят до end участва в objective и ограниченията. Празен end се нормализира към start depot.

### 8.5 Индивидуално service time

JSON/HTTP клиентът може да има `ServiceTimeMinutes`. При липса solver callback/profile използва времето от конкретния бус. Service time участва в arrival/working-time логиката.

JSON/HTTP клиентът може да има и `Mandatory=true` (aliases: `Required`, `IsMandatory`, `IsRequired`, `MustServe`), а Excel — колоната `Задължителен`. Този клиент не получава skip механизъм. Предварителното разпределение му резервира капацитет първо; невъзможен mandatory клиент прекратява ръна с ясна грешка.

### 8.6 Километри и клиенти за деня

При multi-trip те не се нулират. Някои backend комбинации използват fallback или се отказват, за да не дадат тихо невалидно решение.

## 9. Втори курсове

### 9.1 Общ модел

`enable_multiple_trips=true` разрешава reload activity. Броят курсове се изчислява динамично според клиентите и дневните лимити.

- capacity — на курс;
- time/distance/customers — на физически бус за деня;
- reload time — между курсовете;
- `vehicle_key` — общ за физическия бус;
- `trip_number` и output bus number — различни по курс.

### 9.2 Backend-и

- OR-Tools моделира optional reload nodes и кумулативни day dimensions.
- PyVRP използва reload depots/max reloads, но fallback-ва към OR-Tools при активен реален дневен customer limit или при `time + max_distance_km`.
- VROOM отказва multi-trip.
- VRP-Rust използва shift reloads, но отказва `max_customers_per_day + multiple trips`.

## 10. Пропускане

При разрешено skipping penalty/prize се изчислява по конфигурацията. Priority dropping в текущата формула защитава по-силно малки и далечни заявки, а по-големи и близки до депо са по-лесни за пропускане.

Backend преобразуване:

- OR-Tools — `AddDisjunction`;
- PyVRP — prize/required;
- VROOM — приближена priority скала;
- VRP-Rust — job value и ordered objectives.

Известно текущо ограничение: single-trip OR-Tools extraction маркира всяко решение с dropped клиент като infeasible дори при разрешено skipping. Това може да отхвърли иначе допустим penalty резултат.

## 11. Portfolio и избор на резултат

Worker конфигурациите могат да използват различни seeds, first solution strategies и metaheuristics. След завършване:

- невалидните/failed кандидати не участват;
- в `time` режим се предпочитат по-малко dropped клиенти, след това по-нисък fitness;
- в `distance` режим се избира най-нисък fitness.

Провери metadata за **реалния** engine, защото PyVRP adapter може да е fallback-нал към OR-Tools.

## 12. Проверка на hard constraints след solve

След native solve PyVRP, VROOM и VRP-Rust маршрутите се преизчисляват от общия audit с оригиналните входни данни. Проверяват се:

- всички задължителни/повторени клиенти;
- capacity;
- time windows;
- start/end;
- service, wait и reload;
- max day time и 1-minute tolerance;
- max distance;
- max customers per day;
- dropped semantics.

Решение от тези адаптери с нарушение се маркира невалидно и не се използва за файлове/интеграции. Директният OR-Tools backend не извиква същия общ audit: ограниченията са моделирани в неговите Capacity/Distance/Stops/Time dimensions и се допълват от проверки при извличане на решението.

След това отделен централен mandatory invariant се изпълнява за **всички** backend-и. Той сравнява задължителните посещения по стабилен fingerprint и occurrence count, издържа на pickle round-trip и различава посещения с еднакво business ID. Проверката се повтаря преди output и преди `setData`.

## 13. Output workflow

### 13.1 Номериране

`vehicle_numbering.py` подрежда физическите бусове, center buses и курсовете. Един физически бус може да има общ `vehicle_key`, но всеки курс получава различен business output number.

### 13.2 Карти

Общата карта показва маршрутите без solver route ID като видим надпис. Center/traffic зоните се рисуват само при съответния `show_on_map`.

За физически бус с много курсове се създава една индивидуална карта. Тя съдържа course filter, отделни линии/клиенти и правилен клиентски panel за избрания курс.

### 13.3 Excel

Активният workflow създава един общ `cvrp_report_<date>.xlsx` с отделни sheets. При multi-trip sheet `Маршрути` добавя само колоната `Курс`; не добавя `vehicle_key`/`route_id` като бизнес колони.

### 13.4 CSV

CSV е отделен технически export. При multi-trip той добавя `ID бус`, `Курс`, `ID курс` и `Ключ бус`, за да пази машинната връзка между курс и физически бус.

### 13.5 Графики и upload

Chart-овете се създават само при включен switch. Route-map upload е отделна стъпка след локалното генериране и не заменя локалните файлове.

## 14. Нормален run през `/run`

### Background

GET или POST без `return_result=true` създава `run_id`, записва status и стартира child thread/process. Втори основен run връща `409`.

### Synchronous

POST с `return_result`, `return_json`, `wait` или `sync` чака края и връща пълния резултат.

### Callback

`callback_url`/aliases изпраща финален POST payload след background run.

## 15. `/run_saturday`

Специалният endpoint:

1. намира съботата от текущата седмица;
2. записва request-local `json_override_date`;
3. заменя request-local normal bus prefix/digits със Saturday стойностите;
4. задава output stamp `YYYY-MM-DD_събота`;
5. стартира същия CVRP workflow.

Постоянният normal prefix в `config.py` не се променя. При включено `center_bus_numbering_enabled` специалната CENTER_BUS серия от `center_bus_numbering_start_id` има предимство пред съботния prefix. CSV и charts запазват собствените си naming правила и не получават задължително Saturday suffix.

## 16. Web workflow

1. Login проверява PBKDF2 credential store и login throttling.
2. Успешният вход връща session cookie; state-changing POST изисква CSRF token.
3. Config GET презарежда `config.py` от диска.
4. Run изпраща временните Web-editable полета към child process.
5. Global save прави backup и атомарно заменя позволените полета.
6. Vehicles save атомарно подменя само vehicle list-а.
7. Status/log endpoints захранват dashboard-а.
8. Stop прекратява active run process; shutdown спира и сървъра.

Web страницата няма собствен Saturday бутон; `/run_saturday` се извиква през публичния API.

## 17. TSP workflow

`/tsp` не използва CVRP разпределение. Потокът е:

1. валидира driver ID, current GPS, optional end и клиенти;
2. групира документи според общата input настройка;
3. избира normal routing или Valhalla truck profile по driver ID;
4. строи distance/duration матрица;
5. прави greedy initial order;
6. по желание изпълнява 2-opt;
7. изчислява ETA, waiting/late status и totals;
8. връща JSON или HTML и по желание записва/upload-ва карта;
9. добавя запис в TSP history за дневния Excel отчет.

TSP time windows са soft score/ETA, не hard feasibility — late stop може да остане. Service time е една обща стойност за всички stops: body `service_time_minutes`, TSP default или fallback. Клиентското `ServiceTimeMinutes` не се прилага в TSP.

## 18. `setData` workflow

За всеки оригинален `IdPlasDoc` се подготвят `DoneFlag`, `IdSkld`, `Bukva` и `IdGrafik`. Групиран клиент може да създаде няколко `setData` заявки, въпреки че е един solver stop.

Необслужените имат отделни шаблони и flags. След успешни заявки и при включен switch се изпраща `makeGroup` по веднъж на използван склад.

## 19. Статус и откази

API status се пази и в `logs/api_run_status.json`. Run-specific логът е `logs/api_run_<run_id>.log`.

Типични прекъсвания:

- невалиден вход — 400;
- липсващ API key — 401;
- вече активен run — 409;
- прекалено голям body — 413;
- неподдържан content type — 415;
- worker липсва/protocol mismatch/timeout — failed run;
- memory/pagefile/OpenBLAS failure — failed worker/run;
- hard-audit нарушение — кандидатът е отхвърлен.

## 20. Известни текущи разминавания

1. `customer_time_window_default_*` и `enable_start_time_tracking` не управляват очакваната отделна логика.
2. Single-trip OR-Tools dropped резултат може да бъде маркиран infeasible при разрешено skipping.
3. Center zone правилата са soft и pure `time` семантиката се различава по backend.
4. OSRM cache key не включва всички server/profile/mode данни.
5. Excel няма client service-time mapping; JSON/HTTP има.
6. Legacy Web run defaults не се прилагат от текущата страница.
7. Logging rotation и performance memory/max-worker полетата не се налагат стриктно от runtime-а.

Тези точки са описани, за да не се приема настройка в GUI като гарантирано приложена. Пълният списък и препоръчителният избор на backend са в [solver документа](docs/SOLVERS_AND_CONSTRAINTS.md).
