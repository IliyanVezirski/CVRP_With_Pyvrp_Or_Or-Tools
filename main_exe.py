"""
EXE входна точка за CVRP програма
Този файл се използва за създаване на EXE файл с PyInstaller
"""

import sys
import os
import io
import logging
import shutil
import argparse
from pathlib import Path
import importlib.util


def _configure_stdio():
    """Make EXE console/log redirection tolerant to Unicode output."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue

        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
            continue
        except Exception:
            pass

        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            continue

        try:
            setattr(sys, stream_name, io.TextIOWrapper(buffer, encoding="utf-8", errors="replace"))
        except Exception:
            pass


_configure_stdio()


def _resolve_runtime_path(base_dir: Path, configured_path: str, default_relative_path: str) -> str:
    """Resolve config paths for EXE mode.

    Absolute paths are preserved as-is so Settings can point anywhere.
    Relative paths are resolved relative to the EXE directory.
    Empty values fall back to a default path under the EXE directory.
    """
    raw_path = (configured_path or "").strip()
    if not raw_path:
        return str(base_dir / default_relative_path)

    candidate = Path(raw_path)
    if candidate.is_absolute():
        normalised = str(candidate).replace("/", "\\").lower()
        stale_markers = (
            "\\users\\shaman\\documents\\new project 2\\cvrp_with_pyvrp_or_or-tools\\",
            "\\programming\\bizant 2.0\\cvrp-ortools-optimizer\\",
        )
        if any(marker in normalised for marker in stale_markers):
            logging.warning(
                "Relocating old bundled path %s to %s",
                configured_path,
                base_dir / default_relative_path,
            )
            return str(base_dir / default_relative_path)
        return str(candidate)

    return str((base_dir / candidate).resolve())


def _ensure_runtime_output_directories(config) -> None:
    """Create directories required by the current runtime config."""
    directories = [
        os.path.dirname(config.input.excel_file_path),
        config.output.excel_output_dir,
        config.output.charts_output_dir,
        config.output.routes_output_dir,
        os.path.dirname(config.output.map_output_file),
        os.path.dirname(config.output.csv_output_file),
        os.path.dirname(config.logging.log_file),
        config.cache.cache_dir,
    ]

    for directory in directories:
        if not directory:
            continue

        normalized = os.path.normpath(directory)
        drive, tail = os.path.splitdrive(normalized)

        # Skip creating plain drive roots or invalid drive-only paths.
        if normalized in (drive + os.sep, drive + "\\", drive + "/"):
            continue

        if drive and not os.path.exists(drive + os.sep):
            logging.warning(f"Пропускам несъществуващ drive за директория: {directory}")
            continue

        if not os.path.exists(normalized):
            os.makedirs(normalized, exist_ok=True)

def setup_exe_environment():
    """Настройва средата за EXE изпълнение"""
    # Променяме работната директория към директорията на EXE файла
    if getattr(sys, 'frozen', False):
        # Ако е EXE файл, използваме директорията на EXE файла
        exe_dir = Path(sys.executable).parent
        os.chdir(exe_dir)
        print(f"📁 Работна директория: {exe_dir}")
    else:
        # Ако е Python скрипт, използваме директорията на скрипта
        script_dir = Path(__file__).parent
        os.chdir(script_dir)
        print(f"📁 Работна директория: {script_dir}")
    
    # Създаваме необходимите директории ако не съществуват
    directories = ['logs', 'output', 'cache', 'data', 'output/excel', 'output/charts', 'output/routes']
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
    
    # Настройваме logging за EXE
    log_handlers = [logging.FileHandler('logs/cvrp_exe.log', encoding='utf-8')]
    if sys.stdout is not None:
        log_handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=log_handlers,
    )

# Забележка: Функцията copy_output_files е премахната, тъй като сега файловете
# се създават директно в правилните директории, без нужда от копиране

# Динамично зареждане на config.py от директорията на EXE файла
def load_config():
    if getattr(sys, 'frozen', False):
        # За EXE файл, използваме директорията на EXE файла
        exe_dir = Path(sys.executable).parent
        config_path = exe_dir / 'config.py'
    else:
        # За Python скрипт, използваме директорията на скрипта
        script_dir = Path(__file__).parent
        config_path = script_dir / 'config.py'
    
    if config_path.exists():
        spec = importlib.util.spec_from_file_location('config', str(config_path))
        if spec and spec.loader:
            config = importlib.util.module_from_spec(spec)
            sys.modules['config'] = config
            spec.loader.exec_module(config)
            
            # В EXE режим относителните пътища се връзват към папката на exe-то,
            # а абсолютните стойности от Settings се запазват без промяна.
            if getattr(sys, 'frozen', False):
                exe_dir = Path(sys.executable).parent
                runtime_config = config.get_config()
                input_config = runtime_config.input
                output_config = runtime_config.output

                input_config.excel_file_path = _resolve_runtime_path(
                    exe_dir,
                    input_config.excel_file_path,
                    'data/input.xlsx',
                )

                output_config.map_output_file = _resolve_runtime_path(
                    exe_dir,
                    output_config.map_output_file,
                    'output/interactive_map.html',
                )
                output_config.routes_output_dir = _resolve_runtime_path(
                    exe_dir,
                    output_config.routes_output_dir,
                    'output/routes',
                )
                output_config.excel_output_dir = _resolve_runtime_path(
                    exe_dir,
                    output_config.excel_output_dir,
                    'output/excel',
                )
                output_config.csv_output_file = _resolve_runtime_path(
                    exe_dir,
                    output_config.csv_output_file,
                    'output/routes.csv',
                )
                output_config.charts_output_dir = _resolve_runtime_path(
                    exe_dir,
                    output_config.charts_output_dir,
                    'output/charts',
                )

                _ensure_runtime_output_directories(runtime_config)

                print(f"📝 Входен файл: {input_config.excel_file_path}")
                print(f"📝 HTML карта: {output_config.map_output_file}")
                print(f"📝 HTML маршрути: {output_config.routes_output_dir}")
                print(f"📝 Excel директория: {output_config.excel_output_dir}")
                print(f"📝 CSV файл: {output_config.csv_output_file}")
                print(f"📝 Графики: {output_config.charts_output_dir}")
            
            print(f"✅ Конфигурация заредена от: {config_path}")
        else:
            print(f"⚠️ Не мога да заредя config.py от: {config_path}")
    else:
        print(f"⚠️ config.py не е намерен в: {config_path}")

# Добавяме директорията на EXE файла към Python path
if getattr(sys, 'frozen', False):
    exe_dir = Path(sys.executable).parent
    sys.path.insert(0, str(exe_dir))
else:
    current_dir = Path(__file__).parent
    sys.path.insert(0, str(current_dir))

def main_exe():
    """Главна функция за EXE"""
    try:
        setup_exe_environment()
        load_config()
        
        print("🚀 Стартиране на CVRP оптимизация...")
        print("=" * 50)
        
        current_dir = os.getcwd()
        
        # Проверяваме input_source от конфигурацията
        try:
            import config
            cfg = config.get_config()
            input_source = getattr(cfg.input, 'input_source', 'excel')
        except Exception:
            input_source = 'excel'
        
        input_file = None
        if input_source == 'http_json':
            print("🌐 Режим: HTTP JSON - данните ще се заредят от сървъра.")
        else:
            # Excel режим - търсим входен файл
            if len(sys.argv) > 1:
                input_file = sys.argv[1]
                print(f"📁 Използвам входен файл от аргумент: {input_file}")
            else:
                default_files = [
                    os.path.join(current_dir, 'data', 'input.xlsx'), 
                    os.path.join(current_dir, 'input.xlsx')
                ]
                
                for file_path in default_files:
                    if os.path.exists(file_path):
                        input_file = file_path
                        print(f"📁 Намерен входен файл: {input_file}")
                        break
                
                if not input_file:
                    print("⚠️ Не е намерен входен файл. Създайте data/input.xlsx или посочете файл като аргумент.")
                    print("💡 Пример: CVRP_Optimizer.exe data/my_data.xlsx")
                    print("💡 Или променете input_source на 'http_json' в config.py")
                    input("\nНатиснете Enter за да затворите програмата...")
                    sys.exit(1)
        
        # Заменяме sys.argv с правилните аргументи
        original_argv = sys.argv.copy()
        sys.argv = [sys.argv[0]]
        if input_file:
            sys.argv.append(input_file)
        
        print(f"📁 Текуща директория: {current_dir}")
        
        # Изпълняваме главната функция
        from main import main
        main()
        
        # Възстановяваме оригиналните аргументи
        sys.argv = original_argv
        
        print("\n✅ Програмата завърши успешно!")
        print(f"📁 Резултатите са в директорията: {os.path.join(current_dir, 'output')}")
        
    except KeyboardInterrupt:
        print("\n⚠️ Програмата е прекъсната от потребителя.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Грешка при изпълнение: {e}")
        logging.error(f"EXE грешка: {e}", exc_info=True)
        sys.exit(1)


def server_exe():
    """Стартира API сървъра в EXE режим."""
    try:
        parser = argparse.ArgumentParser(description="CVRP Optimizer API server")
        parser.add_argument("--server", "--api", action="store_true")
        parser.add_argument("--host", default=None)
        parser.add_argument("--port", type=int, default=None)
        args, _ = parser.parse_known_args()

        setup_exe_environment()
        load_config()

        import config
        api_config = config.get_config().api
        host = args.host or getattr(api_config, "api_host", "0.0.0.0")
        port = args.port or int(getattr(api_config, "api_port", 8088))
        endpoint = getattr(api_config, "api_endpoint", "/solve")
        trigger_endpoint = getattr(api_config, "trigger_endpoint", "/run")
        tsp_endpoint = getattr(api_config, "tsp_endpoint", "/tsp")
        tsp_report_endpoint = getattr(api_config, "tsp_report_endpoint", "/tsp-report")
        shutdown_endpoint = getattr(api_config, "shutdown_endpoint", "/shutdown")
        from cvrp_api_server import _build_public_base_url, run_server

        public_url = _build_public_base_url(api_config, host, port)

        print(f"🌐 CVRP API сървър: http://{host}:{port}")
        print(f"🔗 URL за клиенти: {public_url}{endpoint}")
        print(f"▶️ URL за run: {public_url}{trigger_endpoint}")
        print(f"🧭 URL за TSP: {public_url}{tsp_endpoint}")
        print(f"📊 URL за TSP отчет: {public_url}{tsp_report_endpoint}")
        print(f"⏹ URL за спиране: {public_url}{shutdown_endpoint}")
        print("Натиснете Ctrl+C за спиране.")
        run_server(host, port)
    except KeyboardInterrupt:
        print("\n⚠️ API сървърът е спрян от потребителя.")
    except Exception as e:
        print(f"\n❌ Грешка при стартиране на API сървър: {e}")
        logging.error(f"EXE API server грешка: {e}", exc_info=True)
        sys.exit(1)


def tsp_worker_exe():
    """Runs one current-driver TSP request in a separate EXE process."""
    try:
        parser = argparse.ArgumentParser(description="CVRP TSP worker")
        parser.add_argument("--tsp-worker", action="store_true")
        parser.add_argument("input_json")
        parser.add_argument("output_json")
        args, _ = parser.parse_known_args()

        setup_exe_environment()
        load_config()

        from tsp_worker import run_worker_from_files

        sys.exit(run_worker_from_files(args.input_json, args.output_json))
    except Exception as e:
        print(f"\n❌ Грешка при TSP worker: {e}")
        logging.error(f"EXE TSP worker грешка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    
    if "--tsp-worker" in sys.argv:
        tsp_worker_exe()
    elif "--settings" in sys.argv or "--config" in sys.argv:
        # Стартираме GUI за настройки
        from config_gui import main as gui_main
        gui_main()
    elif "--server" in sys.argv or "--api" in sys.argv:
        server_exe()
    else:
        main_exe()
