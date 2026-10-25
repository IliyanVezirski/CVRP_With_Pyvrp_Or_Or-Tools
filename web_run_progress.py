"""Read-only, bounded progress observations from an isolated run's own log."""

import os
import math
import re
import threading
from datetime import datetime


PHASE_MESSAGES = {
    "СТЪПКА 1: ПОДГОТОВКА НА ДАННИ": "input",
    "СТЪПКА 1.5: ИЗЧИСЛЯВАНЕ НА МАТРИЦА С РАЗСТОЯНИЯ": "matrix",
    "СТЪПКА 2: РЕШАВАНЕ НА CVRP ПРОБЛЕМА": "solve",
    "СТЪПКА 3: ОБРАБОТКА НА РЕЗУЛТАТИТЕ": "output",
}
PARENT_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}.*? - (?:main|__main__) - INFO - (.+)$")
SEND_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}.*? - setdata_client - INFO - Изпращам \d+ setData заявки към Bizant$")
SEARCH_LINE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - "
    r"(?:(?P<worker>worker-\d+) - )?(?:pyvrp_solver|pyvrp_next_runtime) - INFO - "
    r"(?:\[PyVRP 0\.14\] )?Solving with PyVRP \(time limit: "
    r"(?P<limit>\d+(?:\.\d+)?)s, seed: \d+\)\.\.\.$"
)
# These are explicitly approximate UI milestones, not measured percentages of
# CPU work or solution quality. Only authoritative completion yields 100%.
PHASE_PERCENT = {"input": 0, "matrix": 5, "solve": 10, "output": 95, "sending": 98}


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace(',', '.'))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError, OverflowError):
        return None


def _positive_seconds(value):
    try:
        seconds = float(value)
        return seconds if math.isfinite(seconds) and 0 < seconds <= 86400 else None
    except (TypeError, ValueError, OverflowError):
        return None


def elapsed_seconds(status, now=None):
    """Use the server clock, not the browser's potentially different timezone."""
    start = status.get("started_at")
    if not start:
        return None
    try:
        started = datetime.fromisoformat(start)
        end = status.get("finished_at")
        finished = datetime.fromisoformat(end) if end and not status.get("running") else (
            now or datetime.now(tz=started.tzinfo)
        )
        return max(0, int((finished - started).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


class RunProgressReader:
    """Keep a cursor for the current run only; never scan shared historical logs."""

    def __init__(self):
        self.lock = threading.Lock()
        self.key = None
        self.offset = 0
        self.pending = b""
        self.phase = None
        self.phase_started_at = None
        self.searches = {}
        self.last_percent = 0

    def _observation(self, run_status, now):
        status = run_status or {}
        completed = not status.get('running') and status.get('status') in {'completed', 'success'}
        if completed:
            return {"phase": self.phase, "percent": 100, "estimated": False}
        if self.phase is None:
            return {"phase": None, "percent": None, "estimated": False}
        clock = _timestamp(status.get('finished_at')) if not status.get('running', True) else None
        clock = clock or _timestamp(now) or datetime.now()
        percent = PHASE_PERCENT[self.phase]
        if self.phase == 'solve':
            searches = list(self.searches.values())
            # Use this run's snapshot, never editable/current GUI settings.
            budget = _positive_seconds(status.get('search_time_limit_seconds')) if status.get('source') == 'web_gui' else None
            if not searches and budget and self.phase_started_at:
                searches = [(self.phase_started_at, budget)]
            if searches:
                # Parallel searches overlap; their times must not be added.
                fraction = min(max(0, (clock - start).total_seconds()) / limit for start, limit in searches)
                percent = min(94, 10 + int(85 * min(1, fraction)))
        self.last_percent = max(self.last_percent, percent)
        return {"phase": self.phase, "percent": self.last_percent, "estimated": True}

    def read(self, run_id, path, run_status=None, now=None):
        if not run_id or not path:
            return {"phase": None, "percent": None, "estimated": False}
        with self.lock:
            try:
                stat = os.stat(path)
                key = (str(run_id), os.path.abspath(path), stat.st_ino)
                if key != self.key or stat.st_size < self.offset:
                    self.key, self.offset, self.pending, self.phase = key, 0, b"", None
                    self.phase_started_at, self.searches, self.last_percent = None, {}, 0
                with open(path, "rb") as stream:
                    stream.seek(self.offset)
                    chunk = stream.read(1024 * 1024)
                    self.offset = stream.tell()
                lines = (self.pending + chunk).split(b"\n")
                self.pending = lines.pop()
                # Log messages/tracebacks can contain huge values. Retain only
                # a small incomplete line; never treat a tail as a phase marker.
                if len(self.pending) > 8192:
                    self.pending = b"\x00" + self.pending[-8192:]
                for raw in lines:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r")
                    parent = PARENT_LINE.fullmatch(line)
                    if parent and parent[1] in PHASE_MESSAGES:
                        self.phase = PHASE_MESSAGES[parent[1]]
                        self.phase_started_at = _timestamp(line[:23])
                    elif SEND_LINE.fullmatch(line):
                        self.phase = "sending"
                    search = SEARCH_LINE.fullmatch(line)
                    if search and self.phase == 'solve':
                        start, limit = _timestamp(search['stamp']), _positive_seconds(search['limit'])
                        worker = search['worker'] or 'main'
                        if start and limit and (worker in self.searches or len(self.searches) < 512):
                            self.searches[worker] = (start, limit)
                return self._observation(run_status, now)
            except (OSError, ValueError):
                return {"phase": None, "percent": None, "estimated": False}


progress_reader = RunProgressReader()
