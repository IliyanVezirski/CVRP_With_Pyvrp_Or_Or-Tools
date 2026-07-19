# PyVRP 0.14 experimental backend

Програмата пази две независими PyVRP версии:

| Избор в GUI/API | Runtime |
|---|---|
| `pyvrp` | стабилен PyVRP 0.13.x (минимум 0.13.4) в основната `.venv` |
| `pyvrp_experimental` | PyVRP 0.14.x в `.venv-pyvrp-next` или companion EXE |

0.14 не заменя 0.13. Главният процес никога не я импортва директно. Той подава problem/config snapshot към отделен worker и получава обратно сериализиран резултат с проверена protocol/version metadata.

Пълната solver семантика е в [Решители и ограничения](docs/SOLVERS_AND_CONSTRAINTS.md).

## 1. Защо е изолирана

PyVRP 0.13 и 0.14 не трябва да бъдат инсталирани върху една и съща production среда. Изолацията позволява:

- избор между двете версии от GUI/API;
- отделен build и self-check;
- ясен error при липсваща/грешна версия;
- optional explicit fallback към stable 0.13;
- еднакъв business adapter и output независимо от библиотечната версия.

Worker protocol версията е `1`, backend metadata е `pyvrp_next`, допустимата библиотека трябва да започва с `0.14`, а worker-ът трябва да декларира capability `hard_mandatory_customers`.

## 2. Подготовка на source средата

Нужен е съвместим Windows wheel за Python архитектурата/версията, използвана за `.venv-pyvrp-next`. Пример с локалния wheel:

```powershell
cd "D:\Iliyan\bizant_source"
python setup_pyvrp_next.py --wheel ".\wheels\pyvrp-0.14.0a0-cp314-cp314-win_amd64.whl"
```

Ако в `wheels` има точно един подходящ 0.14 wheel, script-ът може да го намери:

```powershell
python setup_pyvrp_next.py
```

`setup_pyvrp_next.py`:

1. създава `.venv-pyvrp-next` с посочения base Python;
2. инсталира общите runtime dependencies без stable PyVRP pin-а;
3. инсталира избрания 0.14 wheel;
4. инсталира PyInstaller;
5. проверява, че версията започва с `0.14`.

Той отказва да използва основната `.venv` като experimental среда.

## 3. Source run

При празен `cvrp.pyvrp_next_worker_path` runtime-ът първо проверява:

```text
.venv-pyvrp-next\Scripts\python.exe + pyvrp_next_worker.py
```

След това търси готов worker в standard `dist\pyvrp-next` paths. Изрично зададен `pyvrp_next_worker_path` има най-висок приоритет.

Изборът се прави в Desktop GUI:

```text
Солвър -> PyVRP 0.14
```

или през API:

```json
{
  "settings": {
    "solver_type": "pyvrp_experimental"
  }
}
```

## 4. Build на worker

Самостоятелно:

```powershell
.\.venv-pyvrp-next\Scripts\python.exe build_pyvrp_next_worker.py
```

Custom release root:

```powershell
$env:CVRP_DIST_DIR = "D:\Iliyan\dist_candidate"
.\.venv-pyvrp-next\Scripts\python.exe build_pyvrp_next_worker.py
```

Главният `build_exe.py` извиква worker build-а автоматично и отказва release, ако self-check/version проверката не мине.

Предпочитаният worker е PyInstaller `onedir`, защото parallel solve не разархивира голям onefile пакет за всеки процес. Runtime-ът пази съвместимост и със стария flat path.

## 5. Self-check

За готов build използвай точния път от [build ръководството](docs/INSTALLATION_BUILD_OUTPUTS.md), например:

```powershell
& "D:\Iliyan\dist\pyvrp-next\CVRP_PyVRP_Next_Worker\CVRP_PyVRP_Next_Worker.exe" --self-check
```

При стар flat layout:

```powershell
& "D:\Iliyan\dist\pyvrp-next\CVRP_PyVRP_Next_Worker.exe" --self-check
```

Успехът връща JSON metadata с `ok=true`, protocol/backend, PyVRP version и `capabilities.hard_mandatory_customers=true`. Нормалният solve не пуска отделен self-check subprocess преди всяка задача; response envelope-ът се валидира при самото изпълнение.

## 6. Настройки

### Runtime

| Поле | Значение |
|---|---|
| `pyvrp_next_worker_path` | Optional ръчен Python/EXE worker path. |
| `pyvrp_next_worker_timeout_seconds` | `0` = solve time limit + startup/IPC резерв; положително число = explicit timeout. |
| `pyvrp_next_fallback_to_stable` | Ако е `true`, runtime/protocol/timeout грешка може изрично да fallback-не към 0.13. |

Fallback-ът по подразбиране е изключен, за да не изглежда, че е работила 0.14, когато реално е решавала 0.13.

### Quality настройки, които се пренасят надеждно

- `pyvrp_seed` / `pyvrp_seed_base`;
- `pyvrp_num_neighbours`;
- `pyvrp_weight_wait_time` / `pyvrp_symmetric_proximity`;
- `pyvrp_ils_no_improvement`;
- `pyvrp_ils_history_length`;
- `pyvrp_exhaustive_on_best`;
- `pyvrp_use_extended_operators`;
- `pyvrp_min_perturbations` / `pyvrp_max_perturbations`;
- `pyvrp_display_progress` / `pyvrp_display_interval_seconds`;
- `pyvrp_use_library_penalty_defaults` и ръчните `pyvrp_penalty_*` полета.

И двете версии използват тези общи adapter настройки, когато библиотечният API ги поддържа. 0.14 използва своя unified operator API; 0.13 се конфигурира с наличните отделни operators.

Всички фини настройки от актуалните solver панели са dataclass полета на `CVRPConfig` и се пренасят надеждно през worker payload-а. Подробностите са в [solver документа](docs/SOLVERS_AND_CONSTRAINTS.md#144-пренасяне-на-фините-настройки).

## 7. Progress логове

Worker-ът насочва progress и solver логовете към parent pipe. Runtime-ът:

- чете output-а потоково;
- добавя prefix `[PyVRP 0.14]`;
- съкращава последователни еднакви редове;
- пази tail за error diagnostics;
- работи с unbuffered child output.

`pyvrp_display_progress=false` изключва подробния PyVRP progress, но не и важните runtime/status съобщения.

## 8. Business логика и fallback към OR-Tools

0.13 и 0.14 получават еднакви клиенти, матрица, vehicles, windows, zones, service times, reload правила и post-solve audit. 0.14 е нов search engine зад същия adapter, не различен бизнес модел.

Adapter-ът може да използва OR-Tools за комбинация, която PyVRP channel model-ът не представя надеждно:

- активен реален `max_customers_per_day`;
- `objective_metric=time` заедно с активен `max_distance_km`.

Това е business-model fallback вътре в adapter-а и е различно от `pyvrp_next_fallback_to_stable`, което е fallback при runtime проблем на 0.14 worker-а. Винаги проверявай `solver_used`/metadata в резултата.

## 9. Качество и сравнение с 0.13

0.14 не гарантира по-добър резултат за всеки вход. За честен тест използвай:

- еднакви клиенти и matrix/cache;
- еднакви vehicles, zones и ограничения;
- еднакъв objective и time limit;
- контролирани seeds или достатъчно повторения;
- сравнение по обслужени клиенти, hard validity, километри/минути — не само по raw fitness между различни run режими.

## 10. Чести грешки

### Worker is not installed

Провери `.venv-pyvrp-next`, стандартните `dist\pyvrp-next` paths и explicit worker path. При EXE разпространявай цялата release директория.

### Version/protocol mismatch

Worker-ът е от друг build, библиотеката не е 0.14.x или липсва capability `hard_mandatory_customers`. Rebuild-ни worker-а със същия source като главния EXE; стар worker се отказва умишлено, за да не пропусне задължителен клиент.

### Timeout

При `0` runtime-ът изчислява time limit + резерв. Ако explicit стойността е твърде малка, worker-ът може да бъде прекратен преди serialization. Не задавай 60 секунди за solver с 360-секунден budget.

### Бавно начало

Onedir worker-ът избягва onefile unpack overhead. Antivirus scan, студен disk cache и много parallel worker-и могат да забавят първото зареждане.

### RAM/pagefile/OpenBLAS failure

Намали `cvrp.num_workers` до безопасен конкретен брой. Автоматичният `-1` използва приблизително всички CPU ядра без едно, а `performance.memory_limit_mb/max_workers` в текущата версия не налагат строг cap.

### Резултатът е отхвърлен

Това не означава непременно worker crash. Independent audit може да отхвърли кандидат за capacity, прозорец, фиксиран край, дневно време/километри/клиенти или повторен клиент. Виж точния route/client в run log-а.
