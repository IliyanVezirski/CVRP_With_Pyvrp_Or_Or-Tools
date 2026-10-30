"""Private, persistent JSON-pipe worker. No network listener or shared model."""
from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import subprocess
import threading
import uuid

from pyvrp_next_version import PYVRP_NEXT_VERSION

PROTOCOL = "tsp_persistent_v1"
logger = logging.getLogger(__name__)


class WorkerTransportError(RuntimeError):
    pass


class PersistentTspWorker:
    def __init__(self, expected_version=PYVRP_NEXT_VERSION):
        self.expected_version = expected_version
        self.lock = threading.Lock()
        self.process = None
        self.command = None
        self.messages = None
        self.ready = False
        self.requests = 0

    def status(self):
        process = self.process
        return {"mode": "persistent", "ready": bool(self.ready and process and process.poll() is None),
                "pid": process.pid if process and process.poll() is None else None,
                "completed_requests": self.requests}

    def _stop(self):
        process, self.process = self.process, None
        self.ready = False
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            if os.name == "nt":
                from pyvrp_next_runtime import _hidden_process_kwargs
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10, **_hidden_process_kwargs())
            else:
                process.kill()
            process.wait(timeout=5)
        finally:
            for stream in (process.stdout, process.stderr):
                if stream:
                    stream.close()

    def close(self):
        # Shutdown must also terminate a hung native solve.
        acquired = self.lock.acquire(timeout=2)
        try:
            self._stop()
        finally:
            if acquired:
                self.lock.release()

    @staticmethod
    def _read_messages(stream, messages):
        try:
            for line in stream:
                try:
                    messages.put(json.loads(line))
                except (ValueError, TypeError):
                    messages.put(WorkerTransportError("Невалиден отговор от постоянния PyVRP процес."))
        except (OSError, ValueError):
            pass
        finally:
            messages.put(WorkerTransportError("PyVRP процесът е прекъснат."))

    @staticmethod
    def _drain_errors(stream):
        try:
            for line in stream:
                logger.debug("PyVRP worker: %s", line.rstrip())
        except (OSError, ValueError):
            pass

    def _receive(self, timeout):
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise WorkerTransportError("Изтече времето за отговор от PyVRP процеса.") from exc
        if isinstance(message, Exception):
            raise message
        if not isinstance(message, dict) or message.get("protocol") != PROTOCOL or message.get("solver_version") != self.expected_version:
            raise WorkerTransportError(f"Несъвместим постоянен PyVRP модул. Нужна е версия {self.expected_version}.")
        return message

    def _ensure_started(self, command):
        if self.ready and self.command == tuple(command) and self.process and self.process.poll() is None:
            return
        self._stop()
        from pyvrp_next_runtime import _hidden_process_kwargs, _worker_environment
        self.command = tuple(command)
        self.messages = queue.Queue()
        self.process = subprocess.Popen([*command, "--tsp-serve"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            bufsize=1, env=_worker_environment(), **_hidden_process_kwargs())
        threading.Thread(target=self._read_messages, args=(self.process.stdout, self.messages), daemon=True).start()
        threading.Thread(target=self._drain_errors, args=(self.process.stderr,), daemon=True).start()
        try:
            message = self._receive(60)
            if message.get("kind") != "ready":
                raise WorkerTransportError("PyVRP не потвърди готовност.")
            self.ready = True
            logger.info("Persistent TSP PyVRP %s ready (PID %s)", self.expected_version, self.process.pid)
        except Exception:
            self._stop()
            raise

    def warmup(self, command):
        with self.lock:
            self._ensure_started(command)

    def solve(self, command, spec):
        # Serialise IPC so concurrent HTTP requests never consume each other's replies.
        if not self.lock.acquire(timeout=60):
            raise RuntimeError("PyVRP е зает с други заявки. Опитай отново.")
        try:
            for attempt in range(2):
                try:
                    self._ensure_started(command)
                    request_id = uuid.uuid4().hex
                    request = json.dumps({"protocol": PROTOCOL, "request_id": request_id,
                                          "payload": spec}, ensure_ascii=False, allow_nan=False)
                    self.process.stdin.write(request + "\n")
                    self.process.stdin.flush()
                    result = self._receive(float(spec["search_seconds"]) + 30)
                    if result.get("request_id") != request_id:
                        raise WorkerTransportError("PyVRP върна отговор за друга заявка.")
                    if not result.get("ok"):
                        raise ValueError(result.get("error") or "Грешка при TSP изчислението.")
                    self.requests += 1
                    return result
                except (WorkerTransportError, BrokenPipeError, OSError) as exc:
                    self._stop()
                    if attempt:
                        raise RuntimeError("Постоянният PyVRP процес не отговаря след рестартиране.") from exc
                    logger.warning("Restarting TSP PyVRP after transport failure: %s", exc)
        finally:
            self.lock.release()


worker = PersistentTspWorker()
worker_0141 = PersistentTspWorker("0.14.1")

def worker_for(version):
    return {PYVRP_NEXT_VERSION: worker, "0.14.1": worker_0141}[version]

def close_all():
    worker.close()
    worker_0141.close()

atexit.register(close_all)
