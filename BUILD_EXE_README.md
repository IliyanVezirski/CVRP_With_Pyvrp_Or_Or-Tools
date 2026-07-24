# Build инструкции за Bizant

Пълното актуално ръководство е в:

**[Инсталация, build, изходни файлове и диагностика](docs/INSTALLATION_BUILD_OUTPUTS.md)**

Този файл е запазен като кратка входна точка за стари линкове.

## Нормален build

```powershell
cd "D:\Iliyan\bizant_source"
.\.venv\Scripts\python.exe build_exe.py
```

По подразбиране release-ът се създава в `D:\Iliyan\dist`. `build_exe.py` изгражда и проверява:

- `Bizant.exe`;
- `pyvrp-next\CVRP_PyVRP_Next_Worker.exe`;
- `vroom\CVRP_VROOM_Worker.exe`;
- `vrp-rust\CVRP_VRP_Rust_Worker.exe`.

Ако някоя задължителна изолирана среда/worker липсва, build-ът трябва да спре с ясна грешка. Не разпространявай само главния EXE — нужна е цялата `dist` директория.

## Build на друго място

```powershell
$env:CVRP_DIST_DIR = "D:\Iliyan\dist_candidate"
.\.venv\Scripts\python.exe build_exe.py
```

За отделна build cache директория може да се зададе и `CVRP_BUILD_DIR`.

## Запазване на config от съществуващ build

По подразбиране build-ът копира source `config.py` в release-а. Ако целта е да се запази вече настроеният `dist\config.py`:

```powershell
$env:CVRP_PRESERVE_DIST_CONFIG = "1"
.\.venv\Scripts\python.exe build_exe.py
```

Преди production build провери изрично кой config трябва да остане. Пълните правила и backup поведението са описани в [главното build ръководство](docs/INSTALLATION_BUILD_OUTPUTS.md#6-конфигурация-записване-и-backup-и).

## Минимална проверка

След build:

1. Провери, че всички worker EXE файлове съществуват.
2. Изпълни worker self-check командите от пълното ръководство.
3. Стартирай `Settings.bat` и виж правилния `config.py`.
4. Стартирай `start_api_server.bat` и извикай `GET /health`.
5. Направи тест с изключен `setData` и route upload.
6. Провери normal и `/run_saturday` output имената.

## Важно

- В repository-то няма `setup_vrp_rust.py`; VRP-Rust средата се подготвя по командите в пълното ръководство.
- Основният output е един общ `cvrp_report_ДАТА.xlsx` с sheets, не три задължителни отделни Excel файла.
- При multi-trip Excel добавя само `Курс`; техническият CSV съдържа допълнителни идентификатори.
- Полетата `max_log_size_mb` и `backup_count` още не включват автоматична log rotation в runtime-а.
