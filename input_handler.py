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
from dataclasses import dataclass
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


def parse_time_value_to_minutes(value) -> Optional[int]:
    """Парсира час към минути от 00:00.

    Поддържа HH:MM, datetime/time, Excel fraction-of-day, часове и минути.
    """
    if value is None or pd.isna(value):
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

    # Ако е подаден прозорец като "08:00-17:00", взимаме първата стойност.
    if "-" in text and ":" in text:
        text = text.split("-", 1)[0].strip()

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

    match = re.search(r"(\d{1,2})\s*[:.]\s*(\d{1,2})", text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        if 0 <= hours <= 47 and 0 <= minutes <= 59:
            return hours * 60 + minutes

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


def parse_time_window_value(value) -> Tuple[Optional[int], Optional[int]]:
    """Парсира работен прозорец във формат "08:00 - 16:00"."""
    if value is None or pd.isna(value):
        return None, None

    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "null", "-"):
        return None, None

    normalized = (
        text.replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )

    range_match = re.match(r"^\s*(.+?)\s*(?:-|до|to)\s*(.+?)\s*$", normalized, flags=re.IGNORECASE)
    if range_match:
        start = parse_time_value_to_minutes(range_match.group(1))
        end = parse_time_value_to_minutes(range_match.group(2))
        return start, end

    time_tokens = re.findall(r"\d{1,2}\s*[:.]\s*\d{1,2}", normalized)
    if len(time_tokens) >= 2:
        return parse_time_value_to_minutes(time_tokens[0]), parse_time_value_to_minutes(time_tokens[1])

    return None, None


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
            
            data = json.loads(raw)
            
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
        tw_start_field = getattr(self.config, "json_time_window_start_field", "WorkFrom")
        tw_end_field = getattr(self.config, "json_time_window_end_field", "WorkTo")
        
        for idx, record in enumerate(records):
            try:
                gps_data = str(record.get(gps_field, "")).strip()
                client_id = str(record.get(id_field, "")).strip()
                client_name = str(record.get(name_field, "")).strip()
                volume = float(record.get(vol_field, 0))
                document = str(record.get(doc_field, "")).strip()
                plas_doc = str(record.get(plas_doc_field, "")).strip()
                source_id_skld = str(record.get(skld_field, "")).strip()
                tw_start, tw_end = parse_time_window_value(record.get(tw_field)) if tw_field else (None, None)
                if tw_start is None and tw_end is None:
                    tw_start = parse_time_value_to_minutes(record.get(tw_start_field)) if tw_start_field else None
                    tw_end = parse_time_value_to_minutes(record.get(tw_end_field)) if tw_end_field else None
                
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
                )
                customers.append(customer)
                
            except Exception as e:
                logger.error(f"Грешка при обработка на JSON запис {idx}: {e}")
                continue
        
        return customers

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
                tw_start_column = getattr(self.config, "time_window_start_column", "")
                tw_end_column = getattr(self.config, "time_window_end_column", "")
                if tw_column and tw_column in row.index:
                    tw_start, tw_end = parse_time_window_value(row[tw_column])
                if tw_start is None and tw_end is None:
                    if tw_start_column and tw_start_column in row.index:
                        tw_start = parse_time_value_to_minutes(row[tw_start_column])
                    if tw_end_column and tw_end_column in row.index:
                        tw_end = parse_time_value_to_minutes(row[tw_end_column])
                
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
                )
                
                customers.append(customer)
                
            except Exception as e:
                # Променяме съобщението, за да работи с всякакъв тип индекс (не само числа)
                logger.error(f"Грешка при обработка на ред с индекс '{index}': {e}")
                continue
        
        return customers


# Функция за лесно използване
def load_customer_data(file_path: Optional[str] = None) -> InputData:
    """Удобна функция за зареждане на клиентски данни"""
    handler = InputHandler()
    return handler.load_data(file_path)
