"""
Модул за обработка на входни данни за CVRP
Чете Excel файлове с GPS данни, клиенти и обеми
"""

import pandas as pd
import re
import os
import json
import urllib.request
import urllib.error
import urllib.parse
import ssl
import math
from datetime import datetime, timedelta, time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
import logging
import importlib
import config

logger = logging.getLogger(__name__)


class MandatoryCustomerError(ValueError):
    """Base error for mandatory-client input or feasibility failures."""


class MandatoryFlagError(MandatoryCustomerError):
    """Raised when an explicit mandatory marker cannot be interpreted safely."""


@dataclass
class Customer:
    """Клас за представяне на клиент"""
    id: str
    name: str
    coordinates: Optional[Tuple[float, float]]  # (latitude, longitude)
    volume: float
    original_gps_data: str
    document: str = ""
    plas_doc: str = ""
    source_id_skld: str = ""
    time_window_start_minutes: Optional[int] = None
    time_window_end_minutes: Optional[int] = None
    time_windows: List[Tuple[int, int]] = field(default_factory=list)
    delivery_comment: str = ""
    grouped_documents: List[Dict[str, object]] = field(default_factory=list)
    service_time_minutes: Optional[float] = None
    mandatory: bool = False


TIME_WINDOW_FIELD_ALIASES = (
    "WorkTime",
    "work_time",
    "working_time",
    "WorkTimeText",
    "WorkHours",
    "WorkingHours",
    "BusinessHours",
    "TimeWindow",
    "DeliveryTime",
    "DeliveryWindow",
    "RabVreme",
    "RabTime",
    "RabotnoVreme",
    "rabotno_vreme",
    "RabotnoVreme",
    "РаботноВреме",
    "Работно време",
)
DELIVERY_COMMENT_FIELD_ALIASES = (
    "DeliveryComment",
    "DeliveryComments",
    "DeliveryNote",
    "DeliveryNotes",
    "DeliveryRemark",
    "DeliveryInfo",
    "DeliveryInstruction",
    "DeliveryInstructions",
    "CommentDelivery",
    "Comment",
    "Comments",
    "Note",
    "Remark",
    "Memo",
    "Opisanie",
    "Description",
    "Komentar",
    "Zabelezhka",
    "Коментар",
    "Коментар доставка",
    "Забележка",
    "Забележка доставка",
)
SERVICE_TIME_FIELD_ALIASES = (
    "ServiceTimeMinutes",
    "service_time_minutes",
    "serviceTimeMinutes",
    "ServiceMinutes",
    "service_minutes",
    "serviceMinutes",
    "CustomerServiceTimeMinutes",
    "customer_service_time_minutes",
    "VisitTimeMinutes",
    "visit_time_minutes",
    "ServiceTime",
    "service_time",
    "Време за обслужване",
)
MANDATORY_FIELD_ALIASES = (
    "Mandatory",
    "mandatory",
    "IsMandatory",
    "is_mandatory",
    "Required",
    "required",
    "IsRequired",
    "is_required",
    "MustServe",
    "must_serve",
    "Задължителен",
    "Задължителна доставка",
)


_INVALID_TEXT_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _is_empty_value(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().lower() in ("nan", "none", "null", "-")
    try:
        missing = pd.isna(value)
        if isinstance(missing, bool):
            return missing
    except (TypeError, ValueError):
        pass
    return False


def _field_candidates(primary: str, aliases: Tuple[str, ...]) -> List[str]:
    candidates: List[str] = []
    for item in [primary, *aliases]:
        for part in str(item or "").replace(";", ",").split(","):
            field_name = part.strip()
            if field_name and field_name not in candidates:
                candidates.append(field_name)
    return candidates


def _record_value(record: Dict, primary_field: str, aliases: Tuple[str, ...] = ()):
    if not isinstance(record, dict):
        return None
    candidates = _field_candidates(primary_field, aliases)
    for field_name in candidates:
        if field_name in record and not _is_empty_value(record.get(field_name)):
            return record.get(field_name)

    lowered = {str(key).strip().lower(): key for key in record.keys()}
    for field_name in candidates:
        original_key = lowered.get(field_name.strip().lower())
        if original_key is not None and not _is_empty_value(record.get(original_key)):
            return record.get(original_key)
    return None


def _clean_text_value(value) -> str:
    if _is_empty_value(value):
        return ""
    return str(value).strip()


def _parse_volume_value(value, context: str = "") -> float:
    """Parse stack volume while preserving decimal halves from Excel/HTTP input."""
    if _is_empty_value(value):
        return 0.0

    if isinstance(value, bool):
        raise ValueError(f"Невалиден обем{context}: {value!r}")

    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number):
            return 0.0
        return number

    text = str(value).strip().replace("\xa0", " ")
    if not text:
        return 0.0

    compact = re.sub(r"\s+", "", text)
    matches = re.findall(r"[-+]?\d+(?:[.,]\d+)*", compact)
    if not matches:
        raise ValueError(f"Невалиден обем{context}: {value!r}")
    compact = matches[0]

    if "," in compact and "." in compact:
        if compact.rfind(",") > compact.rfind("."):
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", "")
    elif "," in compact:
        compact = compact.replace(",", ".")

    try:
        return float(compact)
    except ValueError as exc:
        raise ValueError(f"Невалиден обем{context}: {value!r}") from exc


def _safe_service_time_minutes(value, context: str = "") -> Optional[float]:
    """Parse an optional per-stop service duration expressed in minutes.

    Invalid values are treated as missing so the solver can safely fall back to
    the service time configured for the selected vehicle.
    """
    if _is_empty_value(value):
        return None

    number: Optional[float] = None
    if isinstance(value, bool):
        logger.warning("Игнорирам невалидно време за обслужване%s: %r", context, value)
        return None

    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace("\xa0", " ")
        duration_match = re.fullmatch(r"(\d{1,3})\s*:\s*(\d{1,2})", text)
        if duration_match:
            hours = int(duration_match.group(1))
            minutes = int(duration_match.group(2))
            if minutes <= 59:
                number = float(hours * 60 + minutes)
        else:
            minute_match = re.fullmatch(
                r"([-+]?\d+(?:[.,]\d+)?)\s*(?:min(?:ute)?s?|мин(?:ута|ути)?\.?)?",
                text,
                flags=re.IGNORECASE,
            )
            if minute_match:
                number = float(minute_match.group(1).replace(",", "."))

    if number is None or not math.isfinite(number) or number < 0:
        logger.warning("Игнорирам невалидно време за обслужване%s: %r", context, value)
        return None
    return number


def _parse_mandatory_flag(value, context: str = "") -> bool:
    """Parse an optional hard-service marker from JSON or Excel input."""
    if _is_empty_value(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isfinite(number) and number in (0.0, 1.0):
            return bool(int(number))

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "да", "mandatory", "required", "задължителен"}:
        return True
    if text in {"0", "false", "no", "n", "не", "optional", "незадължителен"}:
        return False
    raise MandatoryFlagError(
        f"Невалидна стойност за задължителен клиент{context}: {value!r}. "
        "Използвай true/false, 1/0 или да/не."
    )


def is_mandatory_customer(customer: Customer) -> bool:
    """Return whether this customer must be served in an accepted solution."""
    return bool(getattr(customer, "mandatory", False))


def _safe_delivery_comment(value, context: str = "") -> str:
    """Return a safe one-line delivery comment, or empty string for invalid values."""
    if _is_empty_value(value):
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        logger.warning("Игнорирам невалиден формат на коментар за доставка%s: %r", context, value)
        return ""

    try:
        text = str(value)
    except Exception as exc:
        logger.warning("Игнорирам коментар за доставка%s: не може да се прочете (%s)", context, exc)
        return ""

    if _INVALID_TEXT_CONTROL_CHARS_RE.search(text):
        logger.warning("Игнорирам коментар за доставка%s: съдържа невалидни контролни символи", context)
        return ""

    text = re.sub(r"[\r\n\t]+", " ", text).strip()
    if not text:
        return ""

    max_len = 500
    if len(text) > max_len:
        logger.warning("Съкращавам твърде дълъг коментар за доставка%s до %s символа", context, max_len)
        text = text[:max_len].rstrip()
    return text


def _loads_json_tolerant(raw: str):
    """Decode JSON, allowing raw control chars inside strings from legacy HTTP APIs."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        message = str(exc).lower()
        if "invalid control character" not in message:
            raise
        logger.warning(
            "JSON съдържанието съдържа суров newline/tab в текстово поле; "
            "опитвам tolerant JSON decode."
        )
        return json.loads(raw, strict=False)


def parse_time_value_to_minutes(value) -> Optional[int]:
    """Парсира час към минути от 00:00.

    Поддържа HH:MM, datetime/time, Excel fraction-of-day, часове и минути.
    """
    if _is_empty_value(value):
        return None

    if isinstance(value, datetime):
        return value.hour * 60 + value.minute

    if isinstance(value, time):
        return value.hour * 60 + value.minute

    if hasattr(value, "hour") and hasattr(value, "minute"):
        try:
            return int(value.hour) * 60 + int(value.minute)
        except (TypeError, ValueError):
            pass

    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number):
            return None
        if 0 <= number < 1:
            return int(round(number * 24 * 60))
        if 0 <= number <= 24:
            return int(round(number * 60))
        return int(round(number))

    text = str(value).strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered in ("nan", "none", "null", "-"):
        return None

    match = re.search(r"(\d{1,2})\s*[:.]\s*(\d{1,2})(?:\s*[:.]\s*\d{1,2})?", text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        if 0 <= hours <= 47 and 0 <= minutes <= 59:
            return hours * 60 + minutes

    compact_match = re.fullmatch(r"\D*(\d{3,4})\D*", text)
    if compact_match:
        digits = compact_match.group(1)
        hours = int(digits[:-2])
        minutes = int(digits[-2:])
        if 0 <= hours <= 47 and 0 <= minutes <= 59:
            return hours * 60 + minutes

    # Ако е подаден прозорец като "8-17", взимаме първата стойност.
    range_prefix = re.match(r"^\s*(\d{1,2}(?:[,.]\d+)?)\s*(?:-|до|to|/)\s*", text, flags=re.IGNORECASE)
    if range_prefix:
        text = range_prefix.group(1).strip()

    if re.fullmatch(r"\d+(?:[,.]\d+)?", text):
        try:
            number = float(text.replace(",", "."))
            if 0 <= number < 1:
                return int(round(number * 24 * 60))
            if 0 <= number <= 24:
                return int(round(number * 60))
            return int(round(number))
        except ValueError:
            return None

    try:
        number = float(text.replace(",", "."))
        if 0 <= number < 1:
            return int(round(number * 24 * 60))
        if 0 <= number <= 24:
            return int(round(number * 60))
        return int(round(number))
    except ValueError:
        logger.warning(f"Не мога да парсирам час: {value}")
        return None


def _time_tokens_from_text(text: str) -> List[str]:
    tokens = re.findall(r"\b\d{1,2}\s*[:.]\s*\d{1,2}(?:\s*[:.]\s*\d{1,2})?\b", text)
    if len(tokens) >= 2:
        return tokens

    compact_tokens = re.findall(r"\b\d{3,4}\b", text)
    valid_compact = []
    for token in compact_tokens:
        hours = int(token[:-2])
        minutes = int(token[-2:])
        if 0 <= hours <= 47 and 0 <= minutes <= 59:
            valid_compact.append(token)
    return valid_compact


def _normalise_time_window_pair(start, end) -> Optional[Tuple[int, int]]:
    if start is None or end is None:
        return None

    try:
        start = int(start)
        end = int(end)
    except (TypeError, ValueError):
        return None

    if not (0 <= start <= 1439 and 0 <= end <= 1439):
        return None
    if start == end:
        return None
    return start, end


def _dedupe_time_windows(windows: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    unique: List[Tuple[int, int]] = []
    seen = set()
    for start, end in windows:
        key = (int(start), int(end))
        if key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return sorted(unique, key=lambda item: (item[0], item[1] if item[1] >= item[0] else item[1] + 24 * 60))


def parse_time_window_values(value) -> List[Tuple[Optional[int], Optional[int]]]:
    """Parse one or more customer working windows from GET/Excel formats."""
    if _is_empty_value(value):
        return []

    if isinstance(value, dict):
        start = _record_value(value, "start", ("begin",))
        end = _record_value(value, "end", ("finish",))
        if start is not None or end is not None:
            return [(parse_time_value_to_minutes(start), parse_time_value_to_minutes(end))]
        value = _record_value(value, "value", TIME_WINDOW_FIELD_ALIASES)
        if _is_empty_value(value):
            return []

    if isinstance(value, (list, tuple)):
        windows: List[Tuple[Optional[int], Optional[int]]] = []
        for item in value:
            windows.extend(parse_time_window_values(item))

        if windows:
            return windows

        if len(value) >= 2:
            return [(parse_time_value_to_minutes(value[0]), parse_time_value_to_minutes(value[1]))]
        return []

    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "null", "-"):
        return []

    normalized = (
        text.replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )
    normalized = re.sub(r"\b(?:от|from|between)\b", "", normalized, flags=re.IGNORECASE).strip()

    if re.fullmatch(r"\d{6,8}", normalized):
        half = len(normalized) // 2
        return [(parse_time_value_to_minutes(normalized[:half]), parse_time_value_to_minutes(normalized[half:]))]

    time_tokens = _time_tokens_from_text(normalized)
    if len(time_tokens) >= 2:
        return [
            (parse_time_value_to_minutes(time_tokens[i]), parse_time_value_to_minutes(time_tokens[i + 1]))
            for i in range(0, len(time_tokens) - 1, 2)
        ]

    range_match = re.match(r"^\s*(.+?)\s*(?:-|до|to|/|;|,)\s*(.+?)\s*$", normalized, flags=re.IGNORECASE)
    if range_match:
        start = parse_time_value_to_minutes(range_match.group(1))
        end = parse_time_value_to_minutes(range_match.group(2))
        return [(start, end)]

    return []


def parse_time_window_value(value) -> Tuple[Optional[int], Optional[int]]:
    """Парсира работен прозорец от различни GET/Excel формати."""
    windows = parse_time_window_values(value)
    if not windows:
        return None, None
    return windows[0]


def safe_parse_time_window_values(value, context: str = "") -> List[Tuple[int, int]]:
    """Parse and validate all customer time windows. Invalid values are ignored."""
    if _is_empty_value(value):
        return []

    try:
        raw_windows = parse_time_window_values(value)
    except Exception as exc:
        logger.warning("Игнорирам невалидно работно време%s: %r (%s)", context, value, exc)
        return []

    if not raw_windows:
        logger.warning("Игнорирам невалидно работно време%s: %r", context, value)
        return []

    windows: List[Tuple[int, int]] = []
    invalid_count = 0
    for start, end in raw_windows:
        normalised = _normalise_time_window_pair(start, end)
        if normalised is None:
            invalid_count += 1
            continue
        windows.append(normalised)

    windows = _dedupe_time_windows(windows)
    if not windows:
        logger.warning("Игнорирам невалидно работно време%s: %r", context, value)
        return []
    if invalid_count:
        logger.warning(
            "Игнорирам %s невалидни части от работно време%s: %r",
            invalid_count,
            context,
            value,
        )
    return windows


def safe_parse_time_window_value(value, context: str = "") -> Tuple[Optional[int], Optional[int]]:
    """Parse and validate the first customer time window. Invalid values are ignored."""
    windows = safe_parse_time_window_values(value, context)
    if not windows:
        return None, None
    return windows[0]


def customer_time_windows_minutes(customer: Customer) -> List[Tuple[int, int]]:
    windows = getattr(customer, "time_windows", None) or []
    normalised = [
        window
        for window in (_normalise_time_window_pair(start, end) for start, end in windows)
        if window is not None
    ]
    if normalised:
        return _dedupe_time_windows(normalised)

    start = getattr(customer, "time_window_start_minutes", None)
    end = getattr(customer, "time_window_end_minutes", None)
    fallback = _normalise_time_window_pair(start, end)
    return [fallback] if fallback else []


def format_time_windows_minutes(windows: List[Tuple[int, int]]) -> str:
    def fmt(minutes: int) -> str:
        minutes = int(minutes)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    return "; ".join(f"{fmt(start)}-{fmt(end)}" for start, end in windows)


def time_windows_to_seconds(windows: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    result = []
    for start, end in windows:
        start_s = max(0, int(start)) * 60
        end_s = max(0, int(end)) * 60
        if end_s < start_s:
            end_s += 24 * 3600
        result.append((start_s, end_s))
    return sorted(result, key=lambda item: (item[0], item[1]))


def choose_time_window_for_arrival(
    arrival_seconds: float,
    windows_seconds: List[Tuple[int, int]],
) -> Tuple[Optional[Tuple[int, int]], float, int, str]:
    """Return selected window, wait seconds, window index, and status for an arrival."""
    if not windows_seconds:
        return None, 0.0, -1, ""

    for index, (start_s, end_s) in enumerate(windows_seconds):
        if arrival_seconds <= end_s:
            wait_seconds = max(0.0, float(start_s) - float(arrival_seconds))
            if wait_seconds > 0:
                return (start_s, end_s), wait_seconds, index, "Изчакване"
            return (start_s, end_s), 0.0, index, "OK"

    return windows_seconds[-1], 0.0, len(windows_seconds) - 1, "След работно време"


def _unique_join(values: List[object], separator: str = "; ") -> str:
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return separator.join(result)


@dataclass
class InputData:
    """Клас за входните данни"""
    customers: List[Customer]
    total_volume: float
    depot_location: Tuple[float, float]
    
    def __post_init__(self):
        """Изчислява общия обем"""
        self.total_volume = sum(customer.volume for customer in self.customers)


class GPSParser:
    """Парсър за GPS координати от различни формати"""
    
    @staticmethod
    def parse_gps_string(gps_string: str) -> Optional[Tuple[float, float]]:
        """Парсира GPS координати от текстов низ"""
        if not gps_string or pd.isna(gps_string):
            return None
        
        gps_string = str(gps_string).strip()
        
        # Обикновени десетични координати
        decimal_pattern = r'(-?\d+\.?\d*),?\s*(-?\d+\.?\d*)'
        match = re.search(decimal_pattern, gps_string)
        if match:
            try:
                lat = float(match.group(1))
                lon = float(match.group(2))
                
                # Валидация на координатите
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return (lat, lon)
            except ValueError:
                pass
        
        logger.warning(f"Не мога да парсирам GPS координати: {gps_string}")
        return None


class InputHandler:
    """Главен клас за обработка на входни данни"""
    
    def __init__(self, main_config=None):
        if main_config is None:
            importlib.reload(config)
            main_config = config.get_config()
        self.main_config = main_config
        self.config = main_config.input
    
    def load_data(self, file_path: Optional[str] = None) -> InputData:
        """Зарежда данни от файл или HTTP JSON"""
        # Ако input_source е http_json, зареждаме от URL
        if self.config.input_source == "http_json":
            return self._load_from_json_url()
        
        # Използваме подадения път или взимаме от конфигурацията
        file_path = file_path or self.config.excel_file_path
        
        # Проверяваме дали файлът съществува, ако не опитваме да намерим файла на различни места
        if not os.path.exists(file_path):
            logger.warning(f"Файлът не съществува на път: {file_path}")
            
            # Ако пътят не е абсолютен, опитваме да го намерим в текущата директория
            if not os.path.isabs(file_path):
                current_dir = os.getcwd()
                possible_paths = [
                    os.path.join(current_dir, file_path),
                    os.path.join(current_dir, 'data', os.path.basename(file_path)),
                    os.path.join(current_dir, os.path.basename(file_path))
                ]
                
                for possible_path in possible_paths:
                    if os.path.exists(possible_path):
                        logger.info(f"Намерен файл на път: {possible_path}")
                        file_path = possible_path
                        break
        
        logger.info(f"Опитвам да заредя файл от: {file_path}")
        
        try:
            # Определяме колони които да се четат като текст (за запазване на водещи нули)
            dtype_overrides = {}
            if self.config.document_column:
                dtype_overrides[self.config.document_column] = str
            
            # Четене на Excel файла
            if self.config.sheet_name:
                df = pd.read_excel(file_path, sheet_name=self.config.sheet_name, dtype=dtype_overrides)
            else:
                df = pd.read_excel(file_path, dtype=dtype_overrides)
            
            logger.info(f"Успешно заредени {len(df)} реда от {file_path}")
            
            # Обработка на данните
            customers = self._process_dataframe(df)
            self._validate_mandatory_coordinates(customers)
            
            # Филтриране на валидни клиенти
            valid_customers = [c for c in customers if c.coordinates is not None]
            
            # Получаване на депо координати
            depot_location = self.main_config.locations.depot_location
            
            input_data = InputData(
                customers=valid_customers,
                total_volume=0,  # ще се изчисли в __post_init__
                depot_location=depot_location
            )
            
            return input_data
            
        except Exception as e:
            logger.error(f"Грешка при четене на файла {file_path}: {e}")
            raise

    def load_data_from_json_records(self, payload) -> InputData:
        """Зарежда клиентски данни от JSON payload, подаден директно към API сървъра."""
        records = self._extract_json_records(payload)
        logger.info(f"Получени {len(records)} JSON записа от POST payload")
        customers = self._process_json_records(records)
        self._validate_mandatory_coordinates(customers)
        valid_customers = [c for c in customers if c.coordinates is not None]
        depot_location = self.main_config.locations.depot_location

        return InputData(
            customers=valid_customers,
            total_volume=0,
            depot_location=depot_location
        )
    
    @staticmethod
    def _next_business_date() -> str:
        """Връща следващия работен ден като YYYY-MM-DD.
        Пон-Чет → утре, Пет → понеделник, Съб → понеделник, Нед → понеделник."""
        today = datetime.now()
        weekday = today.weekday()  # 0=Mon ... 4=Fri, 5=Sat, 6=Sun
        if weekday == 4:    # петък → +3 дни (понеделник)
            delta = 3
        elif weekday == 5:  # събота → +2 дни (понеделник)
            delta = 2
        elif weekday == 6:  # неделя → +1 ден (понеделник)
            delta = 1
        else:               # пон-чет → +1 ден
            delta = 1
        return (today + timedelta(days=delta)).strftime("%Y-%m-%d")

    def _load_from_json_url(self) -> InputData:
        """Зарежда данни от HTTP JSON endpoint"""
        base_url = self.config.json_url
        if not base_url:
            raise ValueError("json_url не е зададен в InputConfig")
        
        # Ако е зададена конкретна дата (DD/MM/YYYY), конвертираме към YYYY-MM-DD за URL
        override = self.config.json_override_date.strip() if self.config.json_override_date else ""
        if override:
            parsed = datetime.strptime(override, "%d/%m/%Y")
            target_date = parsed.strftime("%Y-%m-%d")
            logger.info(f"Ръчно зададена дата: {override} → {target_date}")
        else:
            target_date = self._next_business_date()
            logger.info(f"Автоматична дата (следващ работен ден): {target_date}")
        method = getattr(self.config, "json_http_method", "GET").upper()
        date_field = getattr(self.config, "json_date_field", "date") or "date"
        request_params = self._build_json_request_params(target_date, date_field)
        if method == "POST":
            url = base_url
            post_payload = json.dumps(request_params).encode("utf-8")
            logger.info(f"Зареждам данни от HTTP JSON с POST: {url}  ({request_params})")
        else:
            url = self._build_get_url(base_url, request_params)
            post_payload = None
            logger.info(f"Зареждам данни от HTTP JSON с GET: {url}")
        
        try:
            # Създаваме SSL контекст (позволява self-signed сертификати при нужда)
            ctx = ssl.create_default_context()
            headers = {"Accept": "application/json"}
            if post_payload is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                url,
                data=post_payload,
                headers=headers,
                method=method,
            )
            
            with urllib.request.urlopen(req, timeout=self.config.json_timeout_seconds, context=ctx) as response:
                raw_bytes = response.read()
                # Опитваме UTF-8, после Windows-1251 (кирилица)
                for enc in ("utf-8", "windows-1251", "latin-1"):
                    try:
                        raw = raw_bytes.decode(enc)
                        logger.info(f"JSON декодиран с {enc}")
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    raw = raw_bytes.decode("utf-8", errors="replace")
            
            # Поправяме чести проблеми в JSON от сървъра:
            # 1. \r без \n
            raw = raw.replace("\r", "\n")
            # 2. GPS стойности без отваряща кавичка: "GPS": 42.676...,23.360..." → "GPS": "42.676...,23.360..."
            raw = re.sub(r'"GPS":\s*([\d.])', r'"GPS": "\1', raw)
            
            data = _loads_json_tolerant(raw)
            
            data = self._extract_json_records(data)
            
            logger.info(f"Получени {len(data)} записа от JSON")
            
            # Конвертиране в Customer обекти
            customers = self._process_json_records(data)
            self._validate_mandatory_coordinates(customers)
            valid_customers = [c for c in customers if c.coordinates is not None]
            
            depot_location = self.main_config.locations.depot_location
            
            return InputData(
                customers=valid_customers,
                total_volume=0,
                depot_location=depot_location
            )
            
        except urllib.error.URLError as e:
            logger.error(f"Грешка при връзка с {url}: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Невалиден JSON отговор: {e}")
            raise

    def _build_json_request_params(self, target_date: str, date_field: str) -> Dict[str, str]:
        """Сглобява параметрите за HTTP JSON заявката."""
        params: Dict[str, str] = {}

        command = str(getattr(self.config, "json_command", "") or "").strip()
        if command:
            params["cmd"] = command

        params[date_field] = target_date

        sklad = str(getattr(self.config, "json_sklad", "") or "").strip()
        if sklad:
            params["Sklad"] = sklad

        done_flag = str(getattr(self.config, "json_done_flag", "") or "").strip()
        if done_flag:
            params["DoneFlag"] = done_flag

        extra_query = str(getattr(self.config, "json_extra_query", "") or "").strip().lstrip("?")
        if extra_query:
            for key, value in urllib.parse.parse_qsl(extra_query, keep_blank_values=True):
                if key:
                    params[key] = value

        return params

    def _build_get_url(self, base_url: str, params: Dict[str, str]) -> str:
        """Добавя/обновява GET параметри към URL без да губи вече зададени параметри."""
        parts = urllib.parse.urlsplit(base_url)
        query_pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        merged = {key: value for key, value in query_pairs}
        merged.update(params)
        query = urllib.parse.urlencode(merged)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    
    def _extract_json_records(self, payload) -> list:
        """Връща списък със записи от JSON payload."""
        if isinstance(payload, dict):
            for preferred_key in ("customers", "clients", "orders", "data", "items", "records"):
                val = payload.get(preferred_key)
                if isinstance(val, list):
                    logger.info(f"Използвам JSON ключ '{preferred_key}' с {len(val)} записа")
                    return val

            for key, val in payload.items():
                if isinstance(val, list):
                    logger.info(f"Използвам JSON ключ '{key}' с {len(val)} записа")
                    return val

            raise ValueError("JSON обектът не съдържа списък с клиентски записи")

        if isinstance(payload, list):
            return payload

        raise ValueError(f"Очакван е JSON списък или обект със списък, получен е {type(payload).__name__}")

    def _process_json_records(self, records: list) -> List[Customer]:
        """Обработва JSON записи и създава списък от клиенти"""
        customers = []
        parser = GPSParser()
        
        gps_field = self.config.json_gps_field
        id_field = self.config.json_client_id_field
        name_field = self.config.json_client_name_field
        vol_field = self.config.json_volume_field
        doc_field = self.config.json_document_field
        plas_doc_field = getattr(self.config, "json_plas_doc_field", "IdPlasDoc")
        skld_field = getattr(self.config, "json_id_skld_field", "IdSkld")
        tw_field = getattr(self.config, "json_time_window_field", "WorkTime")
        comment_field = getattr(self.config, "json_delivery_comment_field", "DeliveryComment")
        service_time_field = getattr(self.config, "json_service_time_field", "ServiceTimeMinutes")
        mandatory_field = getattr(self.config, "json_mandatory_field", "Mandatory")
        
        for idx, record in enumerate(records):
            mandatory = False
            record_context = f" (JSON запис {idx})"
            try:
                if not isinstance(record, dict):
                    raise TypeError("JSON записът трябва да е обект")

                # Read the hard-service marker before parsing any fallible
                # customer fields.  Otherwise an invalid volume/column value
                # could send a mandatory record through the legacy skip path.
                client_id = str(record.get(id_field, "")).strip()
                record_context = f" (JSON запис {idx}, клиент {client_id})"
                mandatory = _parse_mandatory_flag(
                    _record_value(record, mandatory_field, MANDATORY_FIELD_ALIASES),
                    record_context,
                )

                gps_data = str(record.get(gps_field, "")).strip()
                client_name = str(record.get(name_field, "")).strip()
                volume = _parse_volume_value(
                    record.get(vol_field, 0),
                    record_context,
                )
                document = str(record.get(doc_field, "")).strip()
                plas_doc = str(record.get(plas_doc_field, "")).strip()
                source_id_skld = str(record.get(skld_field, "")).strip()
                tw_value = _record_value(record, tw_field, TIME_WINDOW_FIELD_ALIASES)
                time_windows = safe_parse_time_window_values(tw_value, record_context) if tw_value is not None else []
                tw_start, tw_end = time_windows[0] if time_windows else (None, None)
                delivery_comment = _safe_delivery_comment(
                    _record_value(record, comment_field, DELIVERY_COMMENT_FIELD_ALIASES),
                    record_context,
                )
                service_time_minutes = _safe_service_time_minutes(
                    _record_value(record, service_time_field, SERVICE_TIME_FIELD_ALIASES),
                    record_context,
                )
                coordinates = parser.parse_gps_string(gps_data)
                
                customer = Customer(
                    id=client_id,
                    name=client_name,
                    coordinates=coordinates,
                    volume=volume,
                    original_gps_data=gps_data,
                    document=document,
                    plas_doc=plas_doc,
                    source_id_skld=source_id_skld,
                    time_window_start_minutes=tw_start,
                    time_window_end_minutes=tw_end,
                    time_windows=time_windows,
                    delivery_comment=delivery_comment,
                    service_time_minutes=service_time_minutes,
                    mandatory=mandatory,
                    grouped_documents=[
                        {
                            "customer_id": client_id,
                            "document": document,
                            "plas_doc": plas_doc,
                            "source_id_skld": source_id_skld,
                            "volume": volume,
                            "service_time_minutes": service_time_minutes,
                            "mandatory": mandatory,
                        }
                    ],
                )
                customers.append(customer)
                
            except MandatoryCustomerError:
                raise
            except Exception as e:
                if mandatory:
                    raise MandatoryCustomerError(
                        f"Грешка при обработка на задължителен клиент{record_context}: {e}"
                    ) from e
                logger.error(f"Грешка при обработка на JSON запис {idx}: {e}")
                continue
        
        return self._group_customer_documents(customers, "JSON")

    def _process_dataframe(self, df: pd.DataFrame) -> List[Customer]:
        """Обработва DataFrame и създава списък от клиенти"""
        customers = []
        parser = GPSParser()
        
        for index, row in df.iterrows():
            mandatory = False
            record_context = f" (Excel ред {index})"
            try:
                # Determine mandatory status first, before accessing required
                # columns or parsing volume.  Mandatory rows must never be
                # silently discarded by the backwards-compatible skip path.
                mandatory = _parse_mandatory_flag(
                    _record_value(
                        row.to_dict(),
                        getattr(self.config, "mandatory_column", "Задължителен"),
                        MANDATORY_FIELD_ALIASES,
                    ),
                    record_context,
                )

                client_id = str(row[self.config.client_id_column]).strip()
                record_context = f" (Excel ред {index}, клиент {client_id})"
                client_name = str(row[self.config.client_name_column]).strip()
                gps_data = str(row[self.config.gps_column]).strip()
                volume = _parse_volume_value(
                    row[self.config.volume_column],
                    record_context,
                )
                
                # Четем номер на документ/поръчка ако колоната съществува
                document = ""
                if self.config.document_column and self.config.document_column in row.index:
                    doc_val = row[self.config.document_column]
                    if pd.notna(doc_val):
                        document = str(doc_val).strip()

                tw_start = None
                tw_end = None
                time_windows = []
                tw_column = getattr(self.config, "time_window_column", "")
                if tw_column and tw_column in row.index:
                    time_windows = safe_parse_time_window_values(
                        row[tw_column],
                        record_context,
                    )
                    tw_start, tw_end = time_windows[0] if time_windows else (None, None)
                delivery_comment = ""
                comment_column = getattr(self.config, "delivery_comment_column", "")
                if comment_column and comment_column in row.index:
                    delivery_comment = _safe_delivery_comment(
                        row[comment_column],
                        record_context,
                    )
                
                coordinates = parser.parse_gps_string(gps_data)
                
                customer = Customer(
                    id=client_id,
                    name=client_name,
                    coordinates=coordinates,
                    volume=volume,
                    original_gps_data=gps_data,
                    document=document,
                    time_window_start_minutes=tw_start,
                    time_window_end_minutes=tw_end,
                    time_windows=time_windows,
                    delivery_comment=delivery_comment,
                    mandatory=mandatory,
                    grouped_documents=[
                        {
                            "customer_id": client_id,
                            "document": document,
                            "plas_doc": "",
                            "source_id_skld": "",
                            "volume": volume,
                            "mandatory": mandatory,
                        }
                    ],
                )
                
                customers.append(customer)
                
            except MandatoryCustomerError:
                raise
            except Exception as e:
                if mandatory:
                    raise MandatoryCustomerError(
                        f"Грешка при обработка на задължителен клиент{record_context}: {e}"
                    ) from e
                # Променяме съобщението, за да работи с всякакъв тип индекс (не само числа)
                logger.error(f"Грешка при обработка на ред с индекс '{index}': {e}")
                continue
        
        return self._group_customer_documents(customers, "Excel")

    def _group_customer_documents(self, customers: List[Customer], source_label: str) -> List[Customer]:
        """Group multiple documents for the same customer/location into one delivery stop."""
        if not bool(getattr(self.config, "enable_customer_document_grouping", True)):
            return customers

        grouped: Dict[Tuple[str, float, float], Customer] = {}
        ordered: List[Customer] = []
        grouped_rows = 0

        for customer in customers:
            customer_id = str(getattr(customer, "id", "") or "").strip()
            if not customer_id or not customer.coordinates:
                ordered.append(customer)
                continue

            key = (
                customer_id.lower(),
                round(float(customer.coordinates[0]), 6),
                round(float(customer.coordinates[1]), 6),
            )
            if key not in grouped:
                customer.grouped_documents = self._normalise_grouped_documents(customer)
                grouped[key] = customer
                ordered.append(customer)
                continue

            target = grouped[key]
            grouped_rows += 1
            target.volume = float(target.volume or 0) + float(customer.volume or 0)
            target.grouped_documents = self._merge_grouped_document_lists(
                self._normalise_grouped_documents(target),
                self._normalise_grouped_documents(customer),
            )
            target.document = _unique_join([doc.get("document", "") for doc in target.grouped_documents])
            target.plas_doc = _unique_join([doc.get("plas_doc", "") for doc in target.grouped_documents])
            target.source_id_skld = _unique_join(
                [doc.get("source_id_skld", "") for doc in target.grouped_documents]
            )
            target.delivery_comment = _unique_join(
                [target.delivery_comment, customer.delivery_comment],
                separator=" | ",
            )
            # A grouped visit is mandatory when any source document marks it so.
            target.mandatory = is_mandatory_customer(target) or is_mandatory_customer(customer)

            target_service = getattr(target, "service_time_minutes", None)
            customer_service = getattr(customer, "service_time_minutes", None)
            if target_service is None and customer_service is not None:
                target.service_time_minutes = customer_service
            elif (
                target_service is not None
                and customer_service is not None
                and not math.isclose(float(target_service), float(customer_service))
            ):
                selected_service = max(float(target_service), float(customer_service))
                logger.warning(
                    "Клиент %s има различно време за обслужване в няколко документа; "
                    "използвам по-голямото %.2f минути за общото посещение.",
                    customer.id,
                    selected_service,
                )
                target.service_time_minutes = selected_service

            target_windows = customer_time_windows_minutes(target)
            customer_windows = customer_time_windows_minutes(customer)
            if not target_windows and customer_windows:
                target.time_window_start_minutes = customer.time_window_start_minutes
                target.time_window_end_minutes = customer.time_window_end_minutes
                target.time_windows = list(customer_windows)
            elif customer_windows and target_windows != customer_windows:
                logger.warning(
                    "Клиент %s има различно работно време в няколко документа; "
                    "запазвам първото за общото посещение.",
                    customer.id,
                )

        if grouped_rows:
            logger.info(
                "Групирани %s %s документа/реда към вече съществуващи клиентски посещения.",
                grouped_rows,
                source_label,
            )
        return ordered

    def _normalise_grouped_documents(self, customer: Customer) -> List[Dict[str, object]]:
        documents = list(getattr(customer, "grouped_documents", None) or [])
        if not documents:
            documents = [
                {
                    "customer_id": getattr(customer, "id", ""),
                    "document": getattr(customer, "document", ""),
                    "plas_doc": getattr(customer, "plas_doc", ""),
                    "source_id_skld": getattr(customer, "source_id_skld", ""),
                    "volume": getattr(customer, "volume", 0),
                    "service_time_minutes": getattr(customer, "service_time_minutes", None),
                    "mandatory": is_mandatory_customer(customer),
                }
            ]
        normalised = []
        for item in documents:
            if not isinstance(item, dict):
                continue
            normalised.append(
                {
                    "customer_id": str(item.get("customer_id", getattr(customer, "id", "")) or ""),
                    "document": str(item.get("document", "") or ""),
                    "plas_doc": str(item.get("plas_doc", "") or ""),
                    "source_id_skld": str(item.get("source_id_skld", "") or ""),
                    "volume": float(item.get("volume", 0) or 0),
                    "service_time_minutes": _safe_service_time_minutes(
                        item.get("service_time_minutes", None)
                    ),
                    "mandatory": _parse_mandatory_flag(item.get("mandatory", False)),
                }
            )
        return normalised

    def _merge_grouped_document_lists(
        self,
        left: List[Dict[str, object]],
        right: List[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        merged: List[Dict[str, object]] = []
        positions: Dict[str, Dict[str, object]] = {}

        for item in [*left, *right]:
            document = str(item.get("document", "") or "")
            plas_doc = str(item.get("plas_doc", "") or "")
            key = plas_doc or document
            if not key:
                key = f"__row_{len(merged)}"

            if key in positions:
                existing = positions[key]
                existing["volume"] = float(existing.get("volume", 0) or 0) + float(item.get("volume", 0) or 0)
                if not existing.get("document") and document:
                    existing["document"] = document
                if not existing.get("plas_doc") and plas_doc:
                    existing["plas_doc"] = plas_doc
                if not existing.get("source_id_skld") and item.get("source_id_skld"):
                    existing["source_id_skld"] = str(item.get("source_id_skld", ""))
                existing_service = existing.get("service_time_minutes")
                item_service = item.get("service_time_minutes")
                if item_service is not None and (
                    existing_service is None or float(item_service) > float(existing_service)
                ):
                    existing["service_time_minutes"] = float(item_service)
                existing["mandatory"] = bool(existing.get("mandatory")) or bool(item.get("mandatory"))
                continue

            copied = dict(item)
            copied["volume"] = float(copied.get("volume", 0) or 0)
            positions[key] = copied
            merged.append(copied)

        return merged

    @staticmethod
    def _validate_mandatory_coordinates(customers: List[Customer]) -> None:
        missing = [
            customer
            for customer in customers
            if is_mandatory_customer(customer) and not customer.coordinates
        ]
        if not missing:
            return

        sample = ", ".join(
            str(getattr(customer, "id", "") or getattr(customer, "name", ""))
            for customer in missing[:10]
        )
        suffix = f" и още {len(missing) - 10}" if len(missing) > 10 else ""
        raise MandatoryCustomerError(
            "Задължителен клиент няма валидни GPS координати и не може да бъде "
            f"подаден към solver-а: {sample}{suffix}"
        )


# Функция за лесно използване
def load_customer_data(file_path: Optional[str] = None) -> InputData:
    """Удобна функция за зареждане на клиентски данни"""
    handler = InputHandler()
    return handler.load_data(file_path)
