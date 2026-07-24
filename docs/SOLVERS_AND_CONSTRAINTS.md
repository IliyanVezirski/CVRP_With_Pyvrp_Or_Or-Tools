# Решители, цели, ограничения и матрица

Този документ описва **текущото поведение на source кода**, а не желаното поведение и не исторически версии на програмата. Актуален е към 19.07.2026 г.

Основните източници са [`config.py`](../config.py), [`main.py`](../main.py), [`cvrp_solver.py`](../cvrp_solver.py), [`pyvrp_solver.py`](../pyvrp_solver.py), [`vroom_solver.py`](../vroom_solver.py), [`vrp_rust_prototype.py`](../vrp_rust_prototype.py) и модулите за вход и routing матрици.

## 1. Налични решители

Програмата приема пет стойности за `cvrp.solver_type` ([`config.py`, редове 15–19](../config.py#L15-L19)):

| `solver_type` | Реален engine | Версия/изпълнение | Предназначение |
|---|---|---|---|
| `pyvrp` | PyVRP | 0.13.x в главната Python среда | Стабилен основен решител. |
| `pyvrp_experimental` | PyVRP | 0.14.x в изолиран worker | Нова версия за сравнение, без да заменя 0.13. |
| `or_tools` | Google OR-Tools Routing | Инсталираната версия, минимумът в `requirements.txt` е 9.7.2996 | Най-пълно директно моделиране на ограниченията. |
| `vroom` | VROOM чрез `pyvroom` | Поддържа се 1.15.x | Бърз алтернативен решител, но само за един курс. |
| `vrp` | VRP-Rust `vrp-cli` | Поддържа се 1.24.x | Експериментален Rust решител с вътрешна паралелизация. |

Текущият default в source конфигурацията е `pyvrp_experimental` ([`config.py`, редове 804–820](../config.py#L804-L820)). Това не гарантира, че даден build използва същия default: build-ът може да има собствено копие на `config.py`.

PyVRP 0.14 не се импортва в основния процес. Той се стартира като отделен worker, проверява версията/capabilities и комуникира с програмата чрез доверен локален pickle envelope. И двата PyVRP варианта използват една и съща адаптерна логика от [`pyvrp_solver.py`](../pyvrp_solver.py); разликата е библиотечната версия и наличните operators/API.

## 2. Матрица на възможностите

Легенда:

- **Да** — ограничението е моделирано директно от решителя.
- **Да, с адаптер** — адаптерът преобразува проблема към възможностите на engine-а.
- **Fallback** — програмата автоматично изпълнява OR-Tools вместо избрания PyVRP.
- **Одит** — engine-ът може да върне решение, но програмата го проверява след решаването и го отхвърля при нарушение.
- **Не** — комбинацията не се поддържа или настройката няма ефект.

| Функция | `pyvrp` 0.13 | `pyvrp_experimental` 0.14 | `or_tools` | `vroom` | `vrp` Rust |
|---|---|---|---|---|---|
| Капацитет/обем | Да | Да | Да | Да | Да |
| Различни начални и крайни депа | Да | Да | Да | Да | Да |
| Работно време на буса | Да | Да | Да | Да | Да |
| Максимални километри | Да; fallback при `time` | Да; fallback при `time` | Да | Да | Да, с ограничение при зони + `distance` |
| Общ брой клиенти за деня | Fallback, когато лимитът е активен | Fallback, когато лимитът е активен | Да | Да за един курс | Да за един курс; не с reload |
| Повече от един прозорец на клиент | Да, чрез alternative clients | Да, чрез client groups | Да, чрез забранени интервали | Да, native | Да, native |
| Индивидуално време за обслужване | Да | Да | Да | Да | Да, чрез матричен адаптер |
| Пропускане с наказание | Да, чрез prize | Да, чрез prize | Да, чрез disjunction | Приближено чрез priority | Да, чрез value/objectives |
| Втори и следващи курсове | Да | Да | Да | Не | Да |
| Reload време и място | Да | Да | Да | Не | Да |
| Center zone предпочитания при `distance` | Да | Да | Да | Да | Да |
| Center zone предпочитания при `time` | Частично; виж §8 | Частично; виж §8 | Частично; виж §8 | Да, върху custom time cost | Не |
| Traffic zone множители | Да | Да | Да | Да | Да |
| `time_objective_include_waiting` | Да | Да | Да | Не | Не |
| Общ независим audit след solve | Да | Да | Не; native dimensions и extraction checks | Да | Да |
| Централен mandatory invariant преди export/`setData` | Да | Да | Да | Да | Да |
| Вътрешна паралелизация | Ограничена от PyVRP | Ограничена от PyVRP | Worker ниво | Да | Да |

Важни уточнения:

1. PyVRP fallback не е грешка. Когато моделът не може надеждно да представи комбинацията от ограничения, адаптерът изпълнява OR-Tools и записва реалния engine в metadata ([`pyvrp_solver.py`, редове 201–234 и 381–409](../pyvrp_solver.py#L201-L234)).
2. VROOM отказва стартиране, ако `enable_multiple_trips=True` ([`vroom_solver.py`, редове 108–120](../vroom_solver.py#L108-L120)).
3. VRP-Rust отказва комбинацията `max_customers_per_day + multiple trips`, защото `tourSize` брои и reload дейностите ([`vrp_rust_prototype.py`, редове 268–278](../vrp_rust_prototype.py#L268-L278)).
4. „Ограничена/забранена зона“ в текущата реализация е **soft penalty**, не абсолютна забрана.

## 3. Избор и изпълнение на решителя

Dispatcher-ът е в [`main.py`, редове 464–570](../main.py#L464-L570). Той реконструира конфигурацията в worker процеса и стартира избрания backend.

VROOM и VRP-Rust използват вътрешни threads, затова за тях външното parallel portfolio решаване е изключено ([`main.py`, редове 838–847](../main.py#L838-L847)). За OR-Tools и двата PyVRP варианта програмата може да стартира няколко worker-а; автоматичният брой е приблизително `CPU cores - 1` ([`main.py`, редове 849–875](../main.py#L849-L875)).

При няколко резултата изборът е:

- в `time` режим: първо най-малко пропуснати клиенти, след това най-нисък `fitness_score`;
- в `distance` режим: най-нисък `fitness_score`.

Това поведение е в [`main.py`, редове 886–902](../main.py#L886-L902).

## 4. Входни данни и общи единици

### 4.1 Клиент

Основните полета на `Customer` са описани в [`input_handler.py`, редове 25–41](../input_handler.py#L25-L41). За решаването са важни:

- GPS координати;
- обем/товар;
- един или повече времеви прозореца;
- индивидуално `service_time_minutes`;
- идентификатор и описателни полета за изходите.

Клиент без валидни GPS координати се отделя преди изграждането на матрицата и не влиза в решителя ([`main.py`, редове 270–281](../main.py#L270-L281)).

### 4.2 Превозно средство

`VehicleConfig` съдържа ([`config.py`, редове 96–116](../config.py#L96-L116)):

| Поле | Значение |
|---|---|
| `vehicle_type` | Тип, използван и от зоновите правила. |
| `capacity` | Капацитет на един курс; при reload се възстановява. |
| `count` | Брой физически бусове от този конфигурационен ред. |
| `config_id` | Стабилен ключ на конфигурационния ред. |
| `name` | Име за GUI и отчети. |
| `fixed_cost` | Цена за използване на бус. |
| `max_distance_km` | Общ лимит километри за работния ден. |
| `max_time_hours` | Общ лимит време за работния ден. |
| `service_time_minutes` | Fallback време за клиент без собствено време. |
| `enabled` | Дали този ред участва. |
| `start_location` | Начално депо. |
| `end_location` | Крайно депо; ако липсва, се използва началното. |
| `max_customers_per_route` | Legacy лимит. За нови настройки се предпочита дневният лимит. |
| `reload_location` | Място за връщане/презареждане между курсове. |
| `reload_time_minutes` | Време за reload между курсовете. |
| `max_customers_per_day` | Общ брой клиенти на физическия бус за целия ден. |
| `start_time_minutes` | Реален старт на работния ден. |
| `tsp_depot_location` | Специално депо за TSP workflow. |

### 4.3 Единици

- разстоянията в матрицата са в **метри**;
- времената в матрицата са в **секунди**;
- работните времена и service/reload настройките в конфигурацията са в минути/часове според името на полето;
- `fitness_score` се нормализира според избраната цел — виж следващия раздел.

## 5. Целева функция: `distance` или `time`

Настройката е `cvrp.objective_metric` и приема `distance` или `time`.

### 5.1 `distance`

Основната транспортна цена е матричното разстояние в метри. Върху нея могат да се добавят:

- fixed cost за използван бус;
- наказание за пропуснат клиент;
- отстъпки/наказания от center zones;
- други engine-специфични soft costs.

Времето, прозорците, service time и max time остават ограничения, дори когато не са основната цел.

### 5.2 `time`

Основната цена е времето за движение, а при някои engine-и — и service/wait/reload времето. Общият `fitness_score` в резултата е общото преизчислено време в секунди ([`cvrp_solver.py`, редове 109–142](../cvrp_solver.py#L109-L142)).

Fixed costs и penalty стойности, които исторически са зададени като meter-like cost, се превеждат във времева цена с коефициент `0.09`, приблизително еквивалентен на 40 km/h ([`cvrp_solver.py`, редове 201–208](../cvrp_solver.py#L201-L208), [`pyvrp_solver.py`, редове 310–317](../pyvrp_solver.py#L310-L317)). Това не означава, че километри участват като отделна цел; коефициентът само прави soft costs сравними с времевата цел.

### 5.3 `time_objective_include_waiting`

Тази опция има реален ефект само при OR-Tools и PyVRP:

| Стойност | OR-Tools / PyVRP поведение |
|---|---|
| `True` | Цената се формира от пълната продължителност на смяната: travel + service + waiting + reload. |
| `False` | Оптимизира се основно travel + service; чакането е валидирано като време/ограничение, но не се наказва директно в objective. |

Реализацията е в [`cvrp_solver.py`, редове 380–392 и 494–513](../cvrp_solver.py#L380-L392) и [`pyvrp_solver.py`, редове 1135–1204](../pyvrp_solver.py#L1135-L1204).

VROOM използва собствена duration cost матрица плюс service time, но не включва waiting като пряка objective цена. VRP-Rust използва `minimize-duration` и също не следва този toggle.

### 5.4 Как да се избере целта

- Използвай `distance`, когато основната бизнес цел са километри/гориво или когато center zone отстъпките трябва надеждно да влияят върху разпределението при всеки engine.
- Използвай `time`, когато работният ден, трафикът и краткото реално изпълнение са по-важни от километража.
- При `time` и OR-Tools/PyVRP избери `time_objective_include_waiting=True`, ако ранното пристигане и чакането са реален разход.
- Не приемай, че route, избран по най-бързо матрично време, автоматично е и най-късият по километри. OSRM/Valhalla връщат отделни матрици за двете величини.

## 6. Работно време, старт и фиксиран край

Всеки физически бус може да има:

- собствено начално депо;
- собствено крайно депо;
- собствен начален час;
- общ дневен лимит `max_time_hours`;
- общ дневен лимит `max_distance_km`.

Ако `end_location` е празно, крайното депо е началното. Ако е зададено, всички пет backend-а моделират отделен start и end. OR-Tools подава отделни start/end индекси на `RoutingIndexManager` ([`cvrp_solver.py`, редове 229–233](../cvrp_solver.py#L229-L233)); данните се изграждат в [`cvrp_solver.py`, редове 1094–1147](../cvrp_solver.py#L1094-L1147).

Крайният преход участва в objective и във валидирането на време/разстояние. Следователно „фиксиран край“ не е само визуален waypoint.

Настройката `enable_start_time_tracking` съществува в конфигурацията/GUI, но в текущия solver код не управлява отделен branch. Реалното absolute time tracking се активира автоматично, когато има времеви прозорци или multiple trips ([`cvrp_solver.py`, редове 1032–1034](../cvrp_solver.py#L1032-L1034)).

## 7. Времеви прозорци на клиентите

### 7.1 Поддържани формати

Parser-ът е в [`input_handler.py`, редове 399–542](../input_handler.py#L399-L542). Приема:

```json
{
  "work_time": "08:00-13:00\n16:00-18:00"
}
```

```json
{
  "work_time": "08:00-13:00 16:00-18:00"
}
```

```json
{
  "work_time": ["08:00-13:00", "16:00-18:00"]
}
```

```json
{
  "work_time": [
    {"start": "08:00", "end": "13:00"},
    {"start": "16:00", "end": "18:00"}
  ]
}
```

В истински JSON новият ред трябва да бъде escape-нат като `\n`. HTTP decoder-ът има толерантност и към raw control characters, но escape-натият JSON е правилният и преносим формат.

Parser-ът извлича часовете по двойки, затова разделителят може да е нов ред, интервал, `;` и др. Прозорците се deduplicate-ват и сортират. Ако краят е по-ранен от началото, прозорецът се приема като преминаващ през полунощ и към края се добавят 24 часа.

### 7.2 Как се моделират двата прозореца

- **OR-Tools:** задава общия диапазон и премахва интервалите между валидните прозорци ([`cvrp_solver.py`, редове 523–542](../cvrp_solver.py#L523-L542)).
- **PyVRP 0.13:** създава alternative client за всеки прозорец и разрешава точно една алтернатива.
- **PyVRP 0.14:** използва client group и group replacement operators, когато API-то е налично ([`pyvrp_solver.py`, редове 687–699 и 869–906](../pyvrp_solver.py#L687-L699)).
- **VROOM:** подава native `time_windows` ([`vroom_solver.py`, редове 188–206](../vroom_solver.py#L188-L206)).
- **VRP-Rust:** подава native pragmatic `times` ([`vrp_rust_prototype.py`, редове 166–171](../vrp_rust_prototype.py#L166-L171)).

Следователно примерът с `08:00-13:00` и `16:00-18:00` се обработва като **два отделни допустими прозореца**, не като един непрекъснат период.

### 7.3 Невалидни и липсващи прозорци

Невалидните части се игнорират с warning. Ако не остане нито един валиден прозорец, клиентът е без времево ограничение ([`input_handler.py`, редове 464–499](../input_handler.py#L464-L499)).

Полетата `customer_time_window_default_start_minutes` и `customer_time_window_default_end_minutes` присъстват в `CVRPConfig`, но **не се използват от текущите solver-и**. Липсващ прозорец не получава автоматично тези default стойности; той е open. Това е известна разлика между налична GUI настройка и реално изпълнение.

При `enable_customer_time_windows=False` всички клиентски прозорци се игнорират.

## 8. Center zones, traffic zones и `show_on_map`

### 8.1 Center zone

`CenterZoneConfig` поддържа ([`config.py`, редове 130–156](../config.py#L130-L156)):

- `mode="circle"` с center/radius;
- `mode="polygon"` със списък от точки;
- `enabled`;
- `show_on_map`;
- priority vehicle types;
- restricted vehicle types;
- отстъпка за priority bus;
- penalty за priority bus извън неговите зони;
- отделни restricted penalties по тип бус.

Legacy основната center zone и динамично добавените `center_zones` се обединяват в един списък правила ([`config.py`, редове 319–394](../config.py#L319-L394)). Circle и polygon containment са реализирани в [`config.py`, редове 397–423](../config.py#L397-L423).

При припокриващи се зони:

- priority bus получава най-добрия/най-ниския multiplier;
- приложимите restricted penalties се сумират;
- priority bus извън всички свои таргетирани зони получава най-голямото приложимо outside penalty.

Логиката е в [`config.py`, редове 442–512](../config.py#L442-L512).

`show_on_map=False` скрива зоната само от картата. То **не** изключва влиянието ѝ върху оптимизацията. За динамична допълнителна зона използвай `enabled=False`. Legacy основната center зона няма отделно `enabled`; за да изключиш логиката ѝ, изключи едновременно `enable_center_zone_priority` и `enable_center_zone_restrictions`.

Зоновите „ограничения“ са soft penalties. Бус от restricted тип все още може да бъде изпратен в зоната, ако общото решение е по-добро или няма валидна алтернатива.

### 8.2 Влияние на center zones според objective и solver

| Solver | `distance` | `time` |
|---|---|---|
| OR-Tools | Отстъпки и penalties влияят върху arc cost. | При `time_objective_include_waiting=True` base arc cost е нула; multiplicative discount може да няма ефект. Additive penalties могат да влияят. |
| PyVRP 0.13/0.14 | Отстъпки и penalties влияят върху edge cost. | Същото ограничение: при пълен shift-duration objective multiplicative discount върху нулева edge cost няма ефект; additive penalties могат да влияят. |
| VROOM | Влияят върху custom distance cost. | Влияят върху custom duration cost. |
| VRP-Rust | Влияят върху distance cost. | Не се представят; worker-ът записва warning ([`vrp_rust_prototype.py`, редове 125–143](../vrp_rust_prototype.py#L125-L143)). |

Практическо правило: ако разпределянето по center zones е задължително бизнес предпочитание, използвай `distance` или направи правилото hard constraint в бъдеща версия. В сегашния код pure `time` не гарантира еднаква зоновa семантика между решителите.

### 8.3 Traffic zones

Traffic zones са различни от center zones. Те променят **duration matrix**, а не vehicle preference. Активни са само при `enable_city_traffic_adjustment=True`. Ако двете крайни точки на arc са в една и съща traffic zone, се прилага най-силният приложим multiplier ([`config.py`, редове 543–603](../config.py#L543-L603)).

Множителят се прилага след получаването на суровата OSRM/Valhalla матрица във всички backend-и:

- OR-Tools: [`cvrp_solver.py`, редове 330–356](../cvrp_solver.py#L330-L356);
- PyVRP: [`pyvrp_solver.py`, редове 989–1035](../pyvrp_solver.py#L989-L1035);
- VROOM: [`vroom_solver.py`, редове 388–395](../vroom_solver.py#L388-L395);
- VRP-Rust: [`vrp_rust_prototype.py`, редове 409–423](../vrp_rust_prototype.py#L409-L423).

`show_on_map` и при traffic zones е визуална настройка, не solver switch.

## 9. Време за обслужване на клиент

HTTP/JSON входът може да подаде индивидуално време за обслужване. Текущото default име на полето е `ServiceTimeMinutes` ([`config.py`, около ред 780](../config.py#L780)); parser-ът е в [`input_handler.py`, редове 873–917](../input_handler.py#L873-L917).

Правилото е:

1. ако клиентът има валидно неотрицателно `service_time_minutes`, използва се то;
2. ако липсва, е празно или е невалидно, използва се `vehicle.service_time_minutes`;
3. индивидуалното време участва във времевите ограничения и в `time` objective според engine-а.

Backend реализации:

- OR-Tools: [`cvrp_solver.py`, редове 358–378](../cvrp_solver.py#L358-L378);
- PyVRP: [`pyvrp_solver.py`, редове 1058–1084](../pyvrp_solver.py#L1058-L1084);
- VROOM: [`vroom_solver.py`, редове 152–186](../vroom_solver.py#L152-L186);
- VRP-Rust: service времето се добавя към outbound travel time, за да се запази arrival-window семантиката ([`vrp_rust_prototype.py`, редове 382–423](../vrp_rust_prototype.py#L382-L423)).

Текущият Excel input reader не чете отделна колона за индивидуален service time; тази функция е налична през JSON/HTTP. За Excel клиентите се използва стойността на буса.

## 10. Втори курсове, връщане и дневни лимити

### 10.1 Основен модел

`enable_multiple_trips=True` позволява един физически бус да изпълни няколко курса в рамките на един работен ден. Броят курсове не е фиксирана бизнес настройка. Адаптерът изчислява достатъчен брой reload възможности според броя клиенти, дневния customer лимит, наличното време, service и reload времето.

При втори курс:

- капацитетът се възстановява при reload;
- натрупаното работно време не се нулира;
- натрупаните километри не се нулират;
- `max_customers_per_day` е общ за физическия бус;
- reload activity използва `reload_location` и `reload_time_minutes`;
- ако reload location липсва, адаптерът използва подходящото депо според backend логиката.

### 10.2 OR-Tools

OR-Tools добавя опционални reload nodes за всеки бус. Те имат отрицателен demand, който възстановява capacity. Dimension-ите Distance, Stops и Time остават кумулативни за деня ([`cvrp_solver.py`, редове 269–310](../cvrp_solver.py#L269-L310), [`cvrp_solver.py`, редове 972–1030](../cvrp_solver.py#L972-L1030)).

Това е най-пълната реализация на:

- capacity per trip;
- max time per day;
- max distance per day;
- max customers per day;
- reload time;
- различни start/end/reload locations.

### 10.3 PyVRP 0.13 и 0.14

PyVRP използва reload depots и `max_reloads`; адаптерът добавя service само между курсовете, не при началния старт ([`pyvrp_solver.py`, редове 734–809 и 1149–1205](../pyvrp_solver.py#L734-L809)).

Има две автоматични fallback ситуации:

1. активен реален `max_customers_per_day`, който е по-малък от броя клиенти — OR-Tools моделира дневното броене по-надеждно;
2. `objective_metric="time"` заедно с активен `max_distance_km` — PyVRP adapter използва distance channel за времевата цел и не може едновременно да гарантира реалния km dimension.

В тези случаи резултатът е валиден OR-Tools резултат, макар първоначално да е избран PyVRP. Metadata пази реалния solver ([`pyvrp_next_runtime.py`, редове 387–427](../pyvrp_next_runtime.py#L387-L427)).

### 10.4 VROOM

VROOM адаптерът не поддържа reload/multiple trips и прекъсва с ясна грешка, ако опцията е включена. За VROOM изключи `enable_multiple_trips`.

### 10.5 VRP-Rust

VRP-Rust използва shift reloads и поддържа multiple trips. Не поддържа едновременно `max_customers_per_day` и multiple trips, защото native `tourSize` брои reload activity като stop. При тази комбинация адаптерът отказва проблема, вместо тихо да наруши лимита.

## 11. Пропускане на клиенти и penalties

Когато `allow_customer_skipping=False`, клиентите са задължителни. При backend, който все пак върне unassigned jobs, независимият одит трябва да отхвърли решението.

Отделно от глобалната настройка всеки входен клиент може да има `mandatory=True` (`Mandatory`, `Required`, `IsMandatory`, `IsRequired` или `MustServe` в JSON). Този флаг остава hard constraint дори когато `allow_customer_skipping=True`:

- OR-Tools не добавя `AddDisjunction` за него;
- PyVRP създава required client/client group;
- VRP-Rust не добавя job `value`, тоест задачата не е optional;
- VROOM използва priority 100, държи optional клиентите под 100 и независимият audit отхвърля всеки резултат с unassigned mandatory job.

Складовото предварително разпределение обработва mandatory клиентите първо. Липсващ GPS, обем над най-големия бус или недостатъчен общ дневен капацитет водят до ясна грешка — mandatory клиентът не се мести към склада като допустимо решение.

Независимо от native модела, главният процес сравнява всяко задължително посещение по детерминиран fingerprint и брой срещу финалните маршрути. Липсващо, дублирано, едновременно обслужено и dropped или останало в склада посещение прекратява ръна преди карти, Excel и `setData`. PyVRP 0.14 worker-ът трябва да декларира capability `hard_mandatory_customers`; стар worker без него се отказва и трябва да бъде изграден отново.

Когато skipping е разрешен:

- OR-Tools използва `AddDisjunction`;
- PyVRP използва prize/required семантика;
- VROOM преобразува penalty ranking към priority 1–100, затова стойността е приближение, а не идентична monetary penalty;
- VRP-Rust добавя job `value` и подрежда objectives като maximize-value/minimize-unassigned преди route cost.

Penalty изчислението е в [`config.py`, редове 653–698](../config.py#L653-L698). При включен priority dropping текущата формула прави:

- по-големия обем по-лесен за пропускане;
- по-близкия до депо клиент по-лесен за пропускане;
- малките и далечни клиенти по-силно защитени.

Това е съзнателна текуща формула, но трябва да се провери дали отговаря на бизнес политиката. Ако не е желана, изключи priority dropping или промени теглата/формулата.

Известно поведение: single-trip OR-Tools extraction в момента маркира резултата като infeasible при всеки dropped customer, дори когато `allow_customer_skipping=True` ([`cvrp_solver.py`, редове 1677–1680](../cvrp_solver.py#L1677-L1680)). Така главният процес може да отхвърли иначе допустим penalty резултат. Това е текущо ограничение/кандидат за корекция, не препоръчана семантика.

## 12. Независима проверка на ограниченията

След native резултата общият audit преизчислява PyVRP, VROOM и VRP-Rust маршрутите с оригиналните данни и проверява поне:

- capacity;
- времеви прозорци;
- service/wait/reload време;
- start/end;
- максимално работно време;
- максимални километри;
- дневен customer limit;
- повторен/липсващ клиент;
- dropped customers спрямо режима.

PyVRP одитът е в [`pyvrp_solver.py`, редове 487–601](../pyvrp_solver.py#L487-L601). VROOM и VRP-Rust използват същата обща проверка след преобразуване на route резултата ([`vroom_solver.py`, редове 584–593](../vroom_solver.py#L584-L593), [`vrp_rust_prototype.py`, редове 563–572](../vrp_rust_prototype.py#L563-L572)).

Директният OR-Tools backend не извиква тази обща функция. Той налага ограниченията чрез native Capacity/Distance/Stops/Time dimensions и прави допълнителни проверки при извличане на решението. Затова колоната му в матрицата не трябва да се чете като „без проверка“, а като различен механизъм.

Отделният централен mandatory invariant се изпълнява за резултатите на **всички** backend-и, включително OR-Tools. Той се повтаря непосредствено преди output и преди `setData`, така че адаптерна или worker грешка не може да изнесе план без задължителен клиент.

За work-time се допуска толеранс от 1 минута, за да не се отхвърля решение заради rounding на секунди/минути ([`pyvrp_solver.py`, редове 90–94](../pyvrp_solver.py#L90-L94)). Толерансът не добавя планирано работно време към objective; той е само граница при валидиране.

## 13. OR-Tools — подробности и fine настройки

OR-Tools изгражда отделни dimensions за Capacity, Distance, Stops и Time ([`cvrp_solver.py`, редове 254–325 и 494–542](../cvrp_solver.py#L254-L325)). Това го прави най-надеждния избор при много едновременно активни hard constraints.

Основни настройки:

| Настройка | Ефект |
|---|---|
| `time_limit_seconds` | Общ лимит за търсене. |
| `first_solution_strategy` | Как се изгражда първото допустимо решение. |
| `local_search_metaheuristic` | Основна стратегия за подобряване. |
| LNS time/nodes/arcs полета | Колко агресивно да се разрушават и възстановяват маршрути. |
| `use_full_propagation` | По-силно propagation; може да подобри надеждността, но струва време/памет. |
| `search_lambda_coefficient` | Силата на GLS penalties. |
| `log_search` | Native OR-Tools progress logging. |
| `num_workers` | Брой portfolio процеси за OR/PyVRP на ниво приложение. |

Декларираните полета са около [`config.py`, редове 828–852 и 960–988](../config.py#L828-L852).

`use_simple_solver=True` стартира capacity-only опростен solver, когато multiple trips е изключен ([`cvrp_solver.py`, редове 2879–2888](../cvrp_solver.py#L2879-L2888)). Той не е подходящ за production run с времеви прозорци, дневно време, фиксиран край, зони и останалите бизнес ограничения.

`enable_final_depot_reconfiguration` е post-processing TSP преподреждане и се изпълнява само когато customer time windows са изключени ([`cvrp_solver.py`, редове 1629–1636](../cvrp_solver.py#L1629-L1636)). Използвай го внимателно: то променя реда след основното CVRP решение и не е заместител на правилно моделиран край.

## 14. PyVRP 0.13 и 0.14 — подробности и fine настройки

### 14.1 Обща логика

И двете версии получават еднакви:

- клиенти, vehicles и matrix;
- objective преобразуване;
- time windows и service times;
- center/traffic zones;
- reload логика;
- post-solve audit.

Следователно 0.14 не е нов бизнес модел. Тя е по-нова search библиотека зад същия adapter. По-добър резултат не е гарантиран за всеки dataset и seed.

### 14.2 Operators

За 0.13 адаптерът добавя базово:

- `Exchange10`, `Exchange20`, `Exchange11`, `Exchange21`, `Exchange22`;
- `SwapTails`;
- `RelocateWithDepot`;
- при extended operators: `Exchange30`, `Exchange31`, `Exchange32`, `Exchange33`;
- когато са налични: `SwapStar`, `SwapRoutes`.

Логиката е в [`pyvrp_solver.py`, редове 603–732](../pyvrp_solver.py#L603-L732).

За 0.14 се използва unified default `OPERATORS`. Adapter-ът гарантира extended exchange operators, когато `pyvrp_use_extended_operators=True`, и запазва group replacement операторите, нужни за alternative time-window clients.

### 14.3 Надеждно декларирани настройки

Тези полета са част от `CVRPConfig` и се пренасят през worker процесите ([`config.py`, редове 907–934](../config.py#L907-L934)):

| Настройка | Значение |
|---|---|
| `pyvrp_seed_base` | База за различните portfolio workers. |
| `pyvrp_seed` | Точен seed, ако е зададен; полезен за възпроизводим тест. |
| `pyvrp_num_neighbours` | Размер на neighbourhood candidate списъка. По-висока стойност дава по-широко, но по-бавно търсене. |
| `pyvrp_weight_wait_time` / `pyvrp_symmetric_proximity` | Управляват proximity оценката при изграждане на neighbourhood-а. |
| `pyvrp_ils_no_improvement` | Колко итерации без подобрение преди restart/termination логика. |
| `pyvrp_ils_history_length` | Дължина на историята за diversity/ILS решенията. |
| `pyvrp_exhaustive_on_best` | По-пълен local search върху текущото най-добро решение. |
| `pyvrp_use_extended_operators` | Включва по-тежките exchange operators. |
| `pyvrp_min_perturbations` / `pyvrp_max_perturbations` | Сила на perturbation между локални оптимуми. |
| `pyvrp_display_progress` | Progress логове. |
| `pyvrp_display_interval_seconds` | Интервал между progress съобщенията. |
| `pyvrp_use_library_penalty_defaults` | Избира библиотечните defaults или ръчните penalty параметри. |
| `pyvrp_penalty_*` | Ръчни настройки за update честота, feasible target и минимални/максимални penalties. |
| `pyvrp_next_worker_path` | Ръчен път към 0.14 worker. |
| `pyvrp_next_worker_timeout_seconds` | Worker timeout; `0` изчислява solve време плюс резерв. |
| `pyvrp_next_fallback_to_stable` | При runtime проблем разрешава fallback към стабилния PyVRP. |

Практически ефекти:

- Повече neighbours и extended operators обикновено подобряват exploration, но увеличават времето на итерация.
- `exhaustive_on_best=True` е полезно за максимално качество при достатъчен time limit.
- По-голям `ils_no_improvement` намалява преждевременните рестарти, но може да задържи търсенето в една област.
- Силен perturbation помага да се излезе от local optimum, но прекалено силен може да разрушава добри структури.
- За честно сравнение 0.13/0.14 използвай еднакви вход, matrix, objective, time limit и контролирани seeds.

### 14.4 Пренасяне на фините настройки

Всички PyVRP полета, показани в актуалните Desktop и Web solver панели, са декларирани в `CVRPConfig`. Затова `asdict(cvrp_config)` ги пренася към паралелните процеси и към PyVRP 0.14 worker-а. При включено `pyvrp_use_library_penalty_defaults` ръчните `pyvrp_penalty_*` стойности се запазват, но умишлено не се прилагат до изключване на библиотечните defaults ([`pyvrp_solver.py`, редове 612–648](../pyvrp_solver.py#L612-L648)).

## 15. VROOM — подробности

VROOM е подходящ за бърз single-trip benchmark. Поддържа:

- capacity;
- start/end;
- max customers/tasks;
- max time/travel time;
- max distance;
- multiple client windows;
- client service time;
- fixed cost;
- custom distance/time matrix;
- center/traffic zone преобразуване.

Реализацията на vehicle limits е в [`vroom_solver.py`, редове 256–346](../vroom_solver.py#L256-L346), а matrix/objective логиката — в [`vroom_solver.py`, редове 364–426](../vroom_solver.py#L364-L426).

Настройки:

| Настройка | Значение |
|---|---|
| `vroom_worker_path` | Път до изолирания worker. |
| `vroom_worker_timeout_seconds` | Runtime timeout; `0` използва автоматичен solve+startup резерв. |
| `vroom_threads` | Вътрешни VROOM threads; `0` означава автоматично. |
| `vroom_exploration_level` | Exploration 0–5; по-високо обикновено означава повече време и шанс за подобрение. |

VROOM няма second-trip/reload режим в тази интеграция. Penalty-to-priority mapping е приближен и може да води до различно поведение при dropped clients спрямо PyVRP/OR-Tools.

## 16. VRP-Rust — подробности

VRP-Rust `vrp-cli` използва pragmatic problem format и поддържа:

- capacity;
- multiple windows;
- service time;
- fixed start/end;
- work time;
- reload/multiple trips;
- distance/time objective;
- пропускане чрез job value/objective order.

Настройки ([`config.py`, редове 946–958](../config.py#L946-L958)):

| Настройка | Значение |
|---|---|
| `vrp_worker_path` | Път до `vrp-cli`/worker. |
| `vrp_worker_timeout_seconds` | Runtime timeout; `0` използва автоматичен solve+startup резерв. |
| `vrp_threads` | Вътрешни threads; `0` означава автоматично. |
| `vrp_max_generations` | Максимален брой generations. Много голяма стойност не гарантира използването им, ако timeout настъпи първо. |
| `vrp_log_progress` | Progress логове от wrapper-а. |

Ограничения:

- center zones не влияят в pure `time`;
- `max_customers_per_day` не може да се комбинира с multiple trips;
- `distance` + активни zone costs + `max_distance_km` не се поддържа едновременно, защото adapter-ът използва distance cost channel за zone-adjusted objective ([`vrp_rust_prototype.py`, редове 280–301](../vrp_rust_prototype.py#L280-L301)).

## 17. Изграждане на distance/time матрицата

### 17.1 Ред на точките

Matrix node order е детерминиран ([`config.py`, редове 37–76](../config.py#L37-L76), [`main.py`, редове 309–319](../main.py#L309-L319)):

1. основното депо;
2. всички уникални start/end locations на активните бусове;
3. reload locations, когато multiple trips е включен;
4. клиентите.

Допълнителните депа се deduplicate-ват по координати, закръглени до шест знака, и се сортират. Това е важно за съвпадението на matrix index и solver node index.

### 17.2 Какво връща routing engine-ът

`DistanceMatrix` съдържа две независими матрици ([`osrm_client.py`, редове 23–30](../osrm_client.py#L23-L30)):

- `distances` — метри;
- `durations` — секунди.

Изборът на `objective_metric` става **след** построяването на матрицата. OSRM обикновено избира пътя според weight-а на активния profile — при стандартния car profile това обичайно е най-бързият път — и връща едновременно неговите distance и duration. Следователно `objective_metric="distance"` минимизира километража от получената матрица на избраните от routing engine-а пътища; не е гаранция, че за всяка двойка е изчислен абсолютният най-къс път по пътната мрежа. Solver-ът решава коя от двете матрици да използва като основна цена и коя само като constraint/reporting величина.

### 17.3 OSRM

Текущата стратегия е ([`osrm_client.py`, редове 278–335](../osrm_client.py#L278-L335)):

- до 30 точки — директен Table request;
- 31–500 — оптимизиран batched table;
- над 500 — Route API/batched fallback стратегия;
- заявката иска едновременно distance и duration ([`osrm_client.py`, редове 767–799](../osrm_client.py#L767-L799));
- при проблем с local OSRM може да се използва public fallback, ако е разрешен;
- при липсващи клетки/грешки се използва route или Haversine approximation.

Haversine fallback използва road factor приблизително 1.3 и оценява времето по средна скорост ([`osrm_client.py`, редове 728–750](../osrm_client.py#L728-L750)). Такъв arc е приблизителен и може да се различава съществено от реалния път.

OSRM matrix обикновено е асиметрична: A→B може да е различно от B→A. Приближените Haversine fallback клетки са симетрични.

### 17.4 Valhalla

Valhalla използва `sources_to_targets`:

- до 50 точки — една заявка;
- над 50 — batch-ове по 50 ([`valhalla_client.py`, редове 161–191 и 283–347](../valhalla_client.py#L161-L191));
- при time-dependent routing датата е текущият ден плюс configured departure time ([`valhalla_client.py`, редове 100–107](../valhalla_client.py#L100-L107));
- липсващи клетки и неуспешни batch-ове използват Haversine road-factor/speed fallback.

Ако Valhalla не е налична или върне грешка, главният workflow може да премине към OSRM ([`main.py`, редове 321–373](../main.py#L321-L373)).

### 17.5 Cache и известни ограничения

OSRM cache key в момента включва locations/sources/destinations, но не включва routing engine URL, profile или routing mode ([`osrm_client.py`, редове 84–94](../osrm_client.py#L84-L94)). До изтичане на cache запис промяна на server/profile може да върне стара матрица за същите координати.

Central cache може да намери по-голяма вече записана матрица и да извлече submatrix по координати ([`osrm_client.py`, редове 135–209 и 1307–1347](../osrm_client.py#L135-L209)). Това е бързо, но прави правилното versioning на cache key още по-важно.

Traffic zone multipliers не са част от суровия OSRM cache. Това е правилно за текущата архитектура, защото се прилагат след зареждането на matrix. Промяна на traffic zone не изисква задължително нов OSRM request.

Настройката `max_locations_for_osrm` не означава, че над нея всички стойности стават Haversine. Реалният код използва праговете 30/500 и настройката участва в batch sizing. Документация или GUI tooltip, които твърдят друго, са остарели.

## 18. Препоръчителен избор според проблема

| Сценарий | Препоръчан начален избор | Причина |
|---|---|---|
| Много hard constraints, multiple trips, дневни лимити | `or_tools` | Най-пряко и независимо моделира всички dimensions. |
| Най-добро общо качество върху типичен CVRP, без проблемни комбинации | Сравни `pyvrp` и `pyvrp_experimental` | Еднакъв бизнес adapter, различно search поведение. Няма универсален победител. |
| Търсене на най-кратко време с waiting | OR-Tools или PyVRP с `time_objective_include_waiting=True` | Пълна shift duration objective. |
| Бърз single-trip benchmark | `vroom` | Бърз native engine и вътрешни threads. |
| Независим Rust benchmark с reload | `vrp` | Различен search engine; проверявай ограниченията му. |
| Зоновите отстъпки са критични | `distance`, после OR-Tools/PyVRP/VROOM | Най-последователна текуща зоновa семантика. |
| `time` + max km + multiple trips | `or_tools` | PyVRP ще fallback-не; директният избор е по-ясен. |
| Дневен customer limit + multiple trips | `or_tools` | VROOM няма multiple trips; VRP-Rust отказва; PyVRP fallback-ва. |

За реално сравнение:

1. използвай еднакъв input snapshot;
2. изчисти или version-ни matrix cache при промяна на routing profile/server;
3. използвай еднакъв `objective_metric`;
4. използвай еднакъв time limit;
5. фиксирай seeds, когато сравняваш PyVRP;
6. сравнявай dropped clients, feasibility, total time и total distance отделно — не само един fitness number;
7. провери metadata за реалния solver, защото PyVRP може да е изпълнил OR-Tools fallback.

## 19. Известни разминавания между GUI/config и текущия solver код

Тези точки трябва да се приемат като технически дълг:

1. `customer_time_window_default_start_minutes` и `customer_time_window_default_end_minutes` са видими настройки, но не се използват; клиент без прозорец е open.
2. `enable_start_time_tracking` не включва/изключва отделна solver логика; tracking се активира автоматично според проблема.
3. Част от PyVRP fine полетата се четат от adapter-а и се показват в GUI, но липсват в `CVRPConfig`, затова не се пренасят надеждно през `asdict()` worker payload.
4. Single-trip OR-Tools маркира решение с dropped client като infeasible, дори при разрешено skipping.
5. VROOM priority е approximation на penalty, не същата числова objective цена като при OR-Tools/PyVRP.
6. Center zone правилата не са hard ограничения и имат различно влияние в pure `time` според backend-а.
7. OSRM cache key не version-ва server/profile/routing mode.
8. Excel input няма индивидуален client service-time mapping; JSON/HTTP има.
9. `use_simple_solver` заобикаля голяма част от бизнес ограниченията и не трябва да се представя като еквивалентен production solver.

## 20. Бърз диагностичен списък

Ако резултатът изглежда по-лош или нарушава очакване, провери в този ред:

1. Кой е **реалният** solver в metadata — избраният или fallback?
2. `objective_metric` е `distance` или `time`?
3. В `time` режим включено ли е waiting и очакваш ли center discount да влияе?
4. Активно ли е `enable_multiple_trips`, а избран ли е VROOM?
5. Има ли едновременно PyVRP `time` + `max_distance_km`, което задейства fallback?
6. Има ли дневен customer limit, който задейства fallback/unsupported комбинация?
7. Валидни ли са всички GPS координати и времеви прозорци?
8. Има ли OSRM/Valhalla fallback клетки в лога?
9. Съвпадат ли routing profile/server и cache-натата матрица?
10. Зоната `enabled=True` ли е? `show_on_map` не управлява solver-а.
11. Vehicle type съвпада ли точно с priority/restricted type в зоната?
12. Има ли достатъчно penalty, или очакваната „забрана“ всъщност е soft preference?
13. Има ли client-specific service time, или се използва fallback от буса?
14. При multiple trips общите max time, max distance и max customers/day остават ли достатъчни за следващ курс?
