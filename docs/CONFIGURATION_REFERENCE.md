# Справочник на конфигурацията

Постоянната конфигурация е `config.py`, обединена в `MainConfig`. Desktop GUI и позволените операции на Web GUI редактират този файл, като първо създават backup в `config_backups`.

Точните активни стойности зависят от source/build директорията. За HTTP интеграция извикай `GET /health`: `settings_schema` е машинночетимият справочник на стартирания build.

## Начини за подаване на настройка

Пълна секция:

```json
{
  "settings": {
    "cvrp": {
      "solver_type": "pyvrp_experimental",
      "objective_metric": "distance"
    },
    "output": {
      "enable_excel_output": true
    }
  }
}
```

Точкова нотация:

```json
{
  "settings": {
    "cvrp.solver_type": "pyvrp_experimental",
    "output.enable_excel_output": true
  }
}
```

Кратки aliases:

```json
{
  "settings": {
    "solver_type": "pyvrp_experimental",
    "objective_metric": "distance",
    "time_limit_seconds": 360,
    "routing_engine": "osrm"
  }
}
```

Не всяко поле се допуска като кратък alias. Непознатите стойности се връщат като `ignored_settings`; използвай вложената секция или точковата нотация и провери `/health`.

## `MainConfig`

| Поле | Секция |
|---|---|
| `locations` | Депа, основна/допълнителни center зони и трафик зони. |
| `vehicles` | Списък от `VehicleConfig`. |
| `routing` | Избор и общо поведение на routing engine-а. |
| `osrm` | Връзка и batching към OSRM. |
| `valhalla` | Връзка и vehicle profile към Valhalla. |
| `input` | Excel/HTTP вход и field mapping. |
| `warehouse` | Предварително складово разпределение. |
| `cvrp` | Solver, objective, ограничения и качество. |
| `output` | Карти, Excel, CSV, графики, numbering и upload. |
| `logging` | Ниво и destinations на лога. |
| `cache` | Общи cache paths/limits. |
| `performance` | Общи performance hints. |
| `api` | HTTP API, Web GUI и TSP. |
| `set_data` | `setData` и `makeGroup`. |
| `debug_mode`, `verbose`, `dry_run` | Глобални режими. |

`dry_run` е декларирано поле, но текущият runtime не го прочита и то не изключва реалните интеграционни действия. За тест задължително изключи `set_data.enable_set_data_upload`, `set_data.enable_unserved_set_data_upload`, `set_data.enable_make_group` и route-map upload-а.

## `VehicleConfig`

| Поле | Значение |
|---|---|
| `vehicle_type` | `internal_bus`, `center_bus`, `external_bus`, `special_bus` или `vratza_bus`. |
| `config_id` | Стабилен ключ за конфигурационния ред и физическите бусове. |
| `name` | Човешко име в карти/отчети. |
| `enabled` | Дали редът участва. |
| `count` | Брой еднакво конфигурирани физически бусове. |
| `capacity` | Максимален обем за един курс. |
| `fixed_cost` | Цена за използване на бус в solver objective-а. |
| `max_distance_km` | Общ дневен пробег; `None` означава без лимит. |
| `max_time_hours` | Общ дневен работен span, включително път, обслужване, чакане и reload. |
| `service_time_minutes` | Fallback обслужване на клиент, ако клиентът няма свое. |
| `max_customers_per_day` | Общ брой клиенти през всички курсове. |
| `max_customers_per_route` | Legacy fallback; новите настройки използват дневния лимит. |
| `start_location` | Начално депо. GUI го избира от именуваните депа. |
| `end_location` | Край; празно означава стартовото депо. |
| `reload_location` | Депо между курсовете; празно означава стартовото. |
| `reload_time_minutes` | Време между два курса. |
| `start_time_minutes` | Начало на деня в минути от 00:00. |
| `tsp_depot_location` | Депо за свързан TSP сценарий; обикновено следва старта. |

## `InputConfig`

| Група | Полета |
|---|---|
| Източник | `input_source`, `excel_file_path`, `sheet_name`, `encoding` |
| HTTP заявка | `json_url`, `json_http_method`, `json_command`, `json_sklad`, `json_done_flag`, `json_extra_query`, `json_date_field`, `json_override_date`, `json_timeout_seconds` |
| HTTP mapping | `json_gps_field`, `json_client_id_field`, `json_client_name_field`, `json_volume_field`, `json_document_field`, `json_plas_doc_field`, `json_id_skld_field`, `json_time_window_field`, `json_delivery_comment_field`, `json_service_time_field`, `json_mandatory_field` |
| Excel mapping | `gps_column`, `client_id_column`, `client_name_column`, `volume_column`, `document_column`, `time_window_column`, `delivery_comment_column`, `mandatory_column` |
| Обработка | `enable_customer_document_grouping` |

`json_override_date` приема нормална дата `DD/MM/YYYY`. В request-local API override се приема и `current_week_saturday`; специалният endpoint `/run_saturday` задава токена автоматично.

`json_service_time_field` е optional. Валидна клиентска стойност заменя bus service time само за това посещение.

`json_mandatory_field` и `mandatory_column` избират полето за абсолютно задължително посещение. Приемат `true/false`, `1/0`, `yes/no` и `да/не`; невалидна изрична стойност прекратява входа. Marker-ът се прочита преди останалите полета, така че и друга грешка в mandatory реда (например невалиден обем) прекратява ръна вместо тихо да премахне клиента.

## `WarehouseConfig`

| Поле | Значение |
|---|---|
| `enable_warehouse` | Включва стъпката преди solver-а. |
| `sort_by_volume` | Сортира по обем. |
| `sort_by_distance` | Допълнително сортира по разстояние. |
| `check_max_bus_capacity` | Отделя заявки, които не могат да влязат в максималния bus capacity. |
| `max_bus_customer_volume` | Праг за клиентски обем към склада. |
| `capacity_toleranse` | Толеранс при capacity проверките; името е запазено с текущия правопис. |

## `LocationConfig`

### Депа

`depot_location`, `center_location`, `vratza_depot_location` и речникът `depot_locations` формират именувания списък. Матрицата включва използваните start/end точки и при втори курсове — reload точките.

### Основна center зона

`center_zone_mode`, `center_zone_polygon`, `center_zone_radius_km`, `show_center_zone_on_map`, `enable_center_zone_priority`, `enable_center_zone_restrictions`, `discount_center_bus`, `center_bus_outside_center_penalty`, `internal_bus_center_penalty`, `external_bus_center_penalty`, `special_bus_center_penalty` и `vratza_bus_center_penalty`.

Основната legacy зона няма отделно `enabled`. За пълно изключване задай едновременно `enable_center_zone_priority=false` и `enable_center_zone_restrictions=false`; `show_center_zone_on_map` променя само картата.

### Допълнителни center зони

`center_zones` е списък от:

| Поле | Значение |
|---|---|
| `name` | Уникално видимо име. |
| `mode` | `circle` или `polygon`. |
| `center_coords`, `radius_km` | Геометрия за кръг. |
| `polygon` | GPS точки за полигон. |
| `enabled` | Включва/изключва solver правилото. |
| `show_on_map` | Само визуализация; не изключва правилото. |
| `enable_priority`, `enable_restrictions` | Отделни switches за двете части на правилото. |
| `priority_vehicle_types` | Типове с отстъпка в зоната. |
| `restricted_vehicle_types` | Типове, за които се начислява penalty. |
| `discount_priority_vehicle` | Множител за цената на приоритетния тип; `0.9` означава 10% по-ниска цена, а `1.0` — без отстъпка. |
| `priority_vehicle_outside_penalty` | Наказание за приоритетен бус извън таргетираните зони. |
| `vehicle_penalties` | Penalty по vehicle type. |

### Трафик

Старата градска зона използва `city_center_coords`, `city_traffic_radius_km`, `city_traffic_duration_multiplier`, `enable_city_traffic_adjustment` и `show_city_traffic_zone_on_map`.

`traffic_zones` съдържа `name`, `center_coords`, `radius_km`, `duration_multiplier`, `enabled` и `show_on_map`. Видимостта не променя duration матрицата.

## `RoutingConfig`, `OSRMConfig`, `ValhallaConfig`

### Общ routing

- `engine`: `osrm` или `valhalla`;
- `enable_time_dependent`, `departure_time`: time-dependent Valhalla матрица;
- `enable_curbside_approach`: предпочитана страна/curb approach;
- `valhalla_preferred_side`: `same` или `either`.

### OSRM

`base_url`, `profile`, `chunk_size`, `timeout_seconds`, `retry_attempts`, `retry_delay_seconds`, `average_speed_kmh`, `use_cache`, `cache_expiry_hours`, `fallback_to_public`, `public_osrm_url`, `max_locations_for_osrm`, `enable_smart_chunking`.

OSRM Table API връща едновременно duration и distance. Solver objective избира коя матрица е основна цена; построяването на маршрута от OSRM не означава автоматично оптимизация само по време.

### Valhalla

`base_url`, `costing`, `timeout_seconds`, retry/cache полетата, `date_time_type` и truck размерите/теглата: `truck_height`, `truck_width`, `truck_length`, `truck_weight`, `truck_axle_load`, `truck_axle_count`, `truck_hazmat`, `truck_hgv_no_access_penalty`.

## `CVRPConfig`

### Основни

| Поле | Значение |
|---|---|
| `solver_type` | `pyvrp`, `pyvrp_experimental`, `or_tools`, `vroom`, `vrp`. |
| `enable_multiple_trips` | Разрешава свързани допълнителни курсове, когато backend-ът ги поддържа. |
| `time_limit_seconds` | Solver budget. |
| `objective_metric` | `distance` или `time`. |
| `time_objective_include_waiting` | Управлява варианта на time fitness; точната backend семантика е описана в solver документа. |
| `allow_customer_skipping` | Разрешава необслужени клиенти. |
| `distance_penalty_disjunction` | Базово наказание/legacy penalty. |
| `enable_priority_dropping`, `drop_volume_weight`, `drop_closeness_weight`, `min_customer_drop_penalty`, `max_customer_drop_penalty` | Индивидуална защита срещу пропускане. |
| `enable_customer_time_windows` | Включва клиентските прозорци. |
| `customer_time_window_default_start_minutes`, `customer_time_window_default_end_minutes` | Декларирани legacy defaults, които текущите solver-и не прилагат; клиент без валиден прозорец остава open. |
| `global_start_time_minutes` | Глобално планирано начало, когато бусът няма собствено. |
| `enable_start_time_tracking` | Декларирана GUI настройка без отделен runtime branch; absolute tracking се включва автоматично при прозорци или multiple trips. |

### OR-Tools/legacy search

`first_solution_strategy`, `local_search_metaheuristic`, `lns_time_limit_seconds`, `lns_num_nodes`, `lns_num_arcs`, `use_full_propagation`, `log_search`, `search_lambda_coefficient`, `use_simple_solver`, `enable_final_depot_reconfiguration`, `algorithm`.

`parallel_first_solution_strategies` и `parallel_local_search_metaheuristics` задават състезаващите се OR-Tools конфигурации.

### Паралелност

`enable_parallel_solving`, `num_workers`, `pyvrp_seed`, `pyvrp_seed_base`.

`num_workers=-1` означава CPU ядра минус едно в текущия runtime. Полетата `performance.max_workers` и `performance.memory_limit_mb` в настоящата версия не налагат строг cap върху този избор; за контрол на тежък PyVRP 0.14 рън задавай конкретен `num_workers`.

### PyVRP 0.13 и 0.14

Общи quality полета: `pyvrp_num_neighbours`, `pyvrp_ils_no_improvement`, `pyvrp_ils_history_length`, `pyvrp_exhaustive_on_best`, `pyvrp_use_extended_operators`, `pyvrp_min_perturbations`, `pyvrp_max_perturbations`, `pyvrp_display_progress`.

0.14 sidecar: `pyvrp_next_worker_path`, `pyvrp_next_worker_timeout_seconds`, `pyvrp_next_fallback_to_stable`.

### VROOM

`vroom_worker_path`, `vroom_worker_timeout_seconds`, `vroom_threads`, `vroom_exploration_level` (`0..5`).

### VRP-Rust

`vrp_worker_path`, `vrp_worker_timeout_seconds`, `vrp_threads`, `vrp_max_generations`, `vrp_log_progress`.

## `OutputConfig`

| Група | Полета |
|---|---|
| Карти | `enable_interactive_map`, `map_output_file`, `routes_output_dir`, `map_provider`, `folium_tiles`, `google_maps_api_key`, `map_zoom_level`, `show_route_colors`, `show_vehicle_info` |
| Upload | `route_maps_upload_mode`, `route_maps_upload_url`, `route_maps_upload_token_field`, `route_maps_upload_token`, `route_maps_upload_file_field`, `route_maps_upload_bus_id_field`, `route_maps_upload_timeout_seconds` |
| Активен Excel | `enable_excel_output`, `excel_output_dir` |
| Legacy Excel имена | `warehouse_excel_file`, `routes_excel_file`, `efficiency_excel_file`; текущият `generate_all_outputs()` не ги използва |
| Номериране | `excel_bus_number_prefix`, `excel_bus_number_digits`, `saturday_excel_bus_number_prefix`, `saturday_excel_bus_number_digits`, `center_bus_numbering_enabled`, `center_bus_numbering_start_id` |
| CSV | `enable_csv_output`, `csv_output_file` |
| Графики | `enable_charts`, `charts_output_dir`, `efficiency_chart_file`, `route_comparison_file`, `volume_distribution_file` |
| Детайли | `include_detailed_info`, `show_km_info`, `show_time_info`, `show_volume_info` |

Текущият output workflow записва един `cvrp_report_<date>.xlsx`; трите отделни Excel filename полета са запазени за съвместимост. При `/run_saturday` Saturday prefix/digits заменят normal серията само за ръна. Ако `center_bus_numbering_enabled=true`, отделната CENTER_BUS серия от `center_bus_numbering_start_id` има предимство.

Google ключът може да идва от `GOOGLE_MAPS_API_KEY`. Upload token-ите не трябва да се публикуват в документация или source control.

## `APIConfig`

### HTTP и Web

`api_host`, `api_port`, `api_public_url`, `api_key`, `api_endpoint`, `trigger_endpoint`, `saturday_trigger_endpoint`, `tsp_endpoint`, `tsp_report_endpoint`, `shutdown_endpoint`, `health_endpoint`, `web_gui_enabled`, `web_gui_endpoint`, `web_gui_title`, `web_gui_public_host`, `web_gui_public_url`, `web_gui_trusted_proxy_ips`, `web_gui_run_defaults_json`.

`web_gui_run_defaults_json` е наследено поле. Вътрешният save endpoint съществува, но текущата Web страница няма бутон за него и Web стартът не смесва автоматично запазения preset. Не разчитай на това поле като активен слой настройки.

`web_gui_users` е legacy bootstrap поле. Реалните PBKDF2 credentials са в `data/web_gui_auth.json` и се управляват от Desktop GUI.

### TSP оптимизация

`tsp_default_service_time_minutes`, `tsp_objective_metric`, `tsp_use_time_windows`, `tsp_time_window_wait_weight`, `tsp_time_window_late_weight`, `tsp_enable_two_opt`, `tsp_two_opt_max_passes`, `tsp_response_format`, `tsp_generate_html_map`, `tsp_upload_html_map`, `tsp_worker_timeout_seconds`.

### TSP truck профили

`tsp_valhalla_truck_profiles` е списъкът с профили. Legacy single-profile полетата са `tsp_valhalla_truck_driver_ids`, `tsp_valhalla_truck_height`, `tsp_valhalla_truck_width`, `tsp_valhalla_truck_length`, `tsp_valhalla_truck_weight`, `tsp_valhalla_truck_axle_load`, `tsp_valhalla_truck_axle_count`, `tsp_valhalla_truck_hazmat` и `tsp_valhalla_truck_hgv_no_access_penalty`.

### TSP дневен отчет

`tsp_daily_report_enabled`, `tsp_daily_report_time`, `tsp_daily_report_output_dir`, `tsp_daily_report_history_file`, `tsp_daily_report_include_details`.

### TSP field mapping

`tsp_driver_id_field`, `tsp_driver_name_field`, `tsp_driver_location_field`, `tsp_end_location_field`, `tsp_customers_field`, `tsp_customer_id_field`, `tsp_customer_name_field`, `tsp_customer_order_field`, `tsp_customer_gps_field`, `tsp_customer_quantity_field`, `tsp_customer_turnover_field`, `tsp_customer_work_time_field`, `tsp_customer_comment_field`.

Едно mapping поле може да съдържа няколко имена, разделени със запетая; използва се първото намерено.

## `SetDataConfig`

| Поле | Значение |
|---|---|
| `enable_set_data_upload` | Изпраща обслужените заявки. |
| `set_data_url`, `set_data_http_method`, `set_data_command`, `set_data_timeout_seconds` | Endpoint и transport. |
| `set_data_done_flag` | DoneFlag за обслужени. |
| `set_data_id_skld`, `set_data_vratza_id_skld`, `set_data_depot_id_skld_map` | Склад според депото. |
| `set_data_id_grafik`, `set_data_id_grafik_template` | График/бус номер. |
| `set_data_bukva_template` | Буква/позиция за обслужени. |
| `enable_unserved_set_data_upload` | Изпраща и необслужените. |
| `set_data_unserved_done_flag`, `set_data_unserved_id_grafik`, `set_data_unserved_id_grafik_template`, `set_data_unserved_bukva_template` | Отделни стойности за необслужени. |
| `enable_make_group`, `set_data_make_group_command` | След успешните заявки изпраща групиране по склад. |

Шаблоните могат да използват контекста, предоставен от `setdata_client.py`, включително route/stop/bus/trip стойности. Основният Excel отчет добавя само колоната **Курс**, когато има повече от един курс. Техническият CSV добавя `ID бус`, `Курс`, `ID курс` и `Ключ бус` за машинна корелация.

## Logging, cache и performance

- `LoggingConfig`: `log_level`, `log_file`, `log_format`, `enable_console_logging`, `enable_file_logging`, `max_log_size_mb`, `backup_count`.
- `CacheConfig`: `enable_cache`, `cache_dir`, `osrm_cache_file`, `routes_cache_file`, `cache_expiry_hours`, `max_cache_size_mb`.
- `PerformanceConfig`: `max_concurrent_requests`, `chunk_processing_delay`, `memory_limit_mb`, `enable_multiprocessing`, `max_workers`.

Важно за текущата версия:

- logging използва обикновен file handler; `max_log_size_mb` и `backup_count` не включват автоматична ротация;
- от `CacheConfig` реално се използват global switch-ът, `cache_dir` и `osrm_cache_file`; `routes_cache_file`, централният `cache_expiry_hours` и `max_cache_size_mb` не управляват текущия runtime. Нормалният OSRM client има отделен `OSRMConfig.cache_expiry_hours`, а друг вътрешен cache път използва фиксирани 24 часа;
- всички полета в `PerformanceConfig` в момента са декларативни hints и не налагат concurrency, delay, memory, multiprocessing или worker limits.

Не разчитай на тези стойности като операционна защита без допълнителен monitoring.

## Сигурност

- Ако `api_host=0.0.0.0`, попълни `api_key`.
- За Web GUI извън доверена мрежа използвай HTTPS reverse proxy.
- `X-Forwarded-For` се доверява само за адреси в `web_gui_trusted_proxy_ips`.
- Пази `data/web_gui_auth.json`, upload token-и и API keys извън публичен source control.
- Проверявай `ignored_settings` в API отговора; неприета настройка не трябва да се счита за приложена.
