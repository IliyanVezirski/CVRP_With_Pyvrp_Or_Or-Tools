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
    delivery_comment: str = ""
    grouped_documents: List[Dict[str, object]] = field(default_factory=list)


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
            "JSON отговорът съдържа суров newline/tab в текстово поле; "
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


def parse_time_window_value(value) -> Tuple[Optional[int], Optional[int]]:
    """Парсира работен прозорец от различни GET/Excel формати."""
    if _is_empty_value(value):
        return None, None

    if isinstance(value, dict):
        start = _record_value(value, "start", ("begin",))
        end = _record_value(value, "end", ("finish",))
        if start is not None or end is not None:
            return parse_time_value_to_minutes(start), parse_time_value_to_minutes(end)
        value = _record_value(value, "value", TIME_WINDOW_FIELD_ALIASES)
        if _is_empty_value(value):
            return None, None

    if isinstance(value, (list, tuple)) and len(value) >= 2:
        first_start, first_end = parse_time_window_value(value[0])
        if first_start is not None or first_end is not None:
            return first_start, first_end
        return parse_time_value_to_minutes(value[0]), parse_time_value_to_minutes(value[1])

    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "null", "-"):
        return None, None

    normalized = (
        text.replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )
    normalized = re.sub(r"\b(?:от|from|between)\b", "", normalized, flags=re.IGNORECASE).strip()

    if re.fullmatch(r"\d{6,8}", normalized):
        half = len(normalized) // 2
        return parse_time_value_to_minutes(normalized[:half]), parse_time_value_to_minutes(normalized[half:])

    time_tokens = _time_tokens_from_text(normalized)
    if len(time_tokens) >= 2:
        return parse_time_value_to_minutes(time_tokens[0]), parse_time_value_to_minutes(time_tokens[1])

    range_match = re.match(r"^\s*(.+?)\s*(?:-|до|to|/|;|,)\s*(.+?)\s*$", normalized, flags=re.IGNORECASE)
    if range_match:
        start = parse_time_value_to_minutes(range_match.group(1))
        end = parse_time_value_to_minutes(range_match.group(2))
        return start, end

    return None, None


def safe_parse_time_window_value(value, context: str = "") -> Tuple[Optional[int], Optional[int]]:
    """Parse and validate one customer time window. Invalid values are ignored."""
    if _is_empty_value(value):
        return None, None

    try:
        start, end = parse_time_window_value(value)
    except Exception as exc:
        logger.warning("Игнорирам невалидно работно време%s: %r (%s)", context, value, exc)
        return None, None

    if start is None and end is None:
        logger.warning("Игнорирам невалидно работно време%s: %r", context, value)
        return None, None

    if start is None or end is None:
        logger.warning("Игнорирам непълно работно време%s: %r", context, value)
        return None, None

    try:
        start = int(start)
        end = int(end)
    except (TypeError, ValueError):
        logger.warning("Игнорирам невалидно работно време%s: %r", context, value)
        return None, None

    if not (0 <= start <= 1439 and 0 <= end <= 1439):
        logger.warning("Игнорирам работно време извън 00:00-23:59%s: %r", context, value)
        return None, None

    if start == end:
        logger.warning("Игнорирам работно време с еднакви начало и край%s: %r", context, value)
        return None, None

    return start, end


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
        
        for idx, record in enumerate(records):
            try:
                gps_data = str(record.get(gps_field, "")).strip()
                client_id = str(record.get(id_field, "")).strip()
                client_name = str(record.get(name_field, "")).strip()
                volume = float(record.get(vol_field, 0))
                document = str(record.get(doc_field, "")).strip()
                plas_doc = str(record.get(plas_doc_field, "")).strip()
                source_id_skld = str(record.get(skld_field, "")).strip()
                tw_value = _record_value(record, tw_field, TIME_WINDOW_FIELD_ALIASES)
                record_context = f" (JSON запис {idx}, клиент {client_id})"
                tw_start, tw_end = (
                    safe_parse_time_window_value(tw_value, record_context)
                    if tw_value is not None
                    else (None, None)
                )
                delivery_comment = _safe_delivery_comment(
                    _record_value(record, comment_field, DELIVERY_COMMENT_FIELD_ALIASES),
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
                    delivery_comment=delivery_comment,
                    grouped_documents=[
                        {
                            "customer_id": client_id,
                            "document": document,
                            "plas_doc": plas_doc,
                            "source_id_skld": source_id_skld,
                            "volume": volume,
                        }
                    ],
                )
                customers.append(customer)
                
            except Exception as e:
                logger.error(f"Грешка при обработка на JSON запис {idx}: {e}")
                continue
        
        return self._group_customer_documents(customers, "JSON")

    def _process_dataframe(self, df: pd.DataFrame) -> List[Customer]:
        """Обработва DataFrame и създава списък от клиенти"""
        customers = []
        parser = GPSParser()
        
        for index, row in df.iterrows():
            try:
                client_id = str(row[self.config.client_id_column]).strip()
                client_name = str(row[self.config.client_name_column]).strip()
                gps_data = str(row[self.config.gps_column]).strip()
                volume = float(row[self.config.volume_column])
                
                # Четем номер на документ/поръчка ако колоната съществува
                document = ""
                if self.config.document_column and self.config.document_column in row.index:
                    doc_val = row[self.config.document_column]
                    if pd.notna(doc_val):
                        document = str(doc_val).strip()

                tw_start = None
                tw_end = None
                tw_column = getattr(self.config, "time_window_column", "")
                if tw_column and tw_column in row.index:
                    tw_start, tw_end = safe_parse_time_window_value(
                        row[tw_column],
                        f" (Excel ред {index}, клиент {client_id})",
                    )
                delivery_comment = ""
                comment_column = getattr(self.config, "delivery_comment_column", "")
                if comment_column and comment_column in row.index:
                    delivery_comment = _safe_delivery_comment(
                        row[comment_column],
                        f" (Excel ред {index}, клиент {client_id})",
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
                    delivery_comment=delivery_comment,
                    grouped_documents=[
                        {
                            "customer_id": client_id,
                            "document": document,
                            "plas_doc": "",
                            "source_id_skld": "",
                            "volume": volume,
                        }
                    ],
                )
                
                customers.append(customer)
                
            except Exception as e:
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

            if target.time_window_start_minutes is None and target.time_window_end_minutes is None:
                target.time_window_start_minutes = customer.time_window_start_minutes
                target.time_window_end_minutes = customer.time_window_end_minutes
            elif (
                (customer.time_window_start_minutes is not None or customer.time_window_end_minutes is not None)
                and (
                    target.time_window_start_minutes != customer.time_window_start_minutes
                    or target.time_window_end_minutes != customer.time_window_end_minutes
                )
            ):
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
                continue

            copied = dict(item)
            copied["volume"] = float(copied.get("volume", 0) or 0)
            positions[key] = copied
            merged.append(copied)

        return merged


# Функция за лесно използване
def load_customer_data(file_path: Optional[str] = None) -> InputData:
    """Удобна функция за зареждане на клиентски данни"""
    handler = InputHandler()
    return handler.load_data(file_path)
