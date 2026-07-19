"""Authentication primitives for the CVRP web interface.

This module is deliberately independent from the HTTP handler and the desktop
GUI.  Both integrations can use the same small public API:

* :class:`CredentialStore` owns the versioned, atomic JSON credential file at
  ``<base_dir>/data/web_gui_auth.json``.  Passwords are always stored as
  PBKDF2-SHA256 hashes.  Passing ``legacy_users`` to the constructor performs a
  one-time migration only when the JSON store does not exist.
* :class:`SessionManager` issues opaque in-memory session and CSRF tokens.  A
  password reset, account disable, or account deletion invalidates existing
  sessions through the per-user credential revision.
* :class:`LoginRateLimiter` limits failed attempts per client IP and username.
* :class:`WebGUIAuthService` combines the three pieces and always returns the
  same public error for invalid, missing, disabled, or rate-limited logins.

The module never logs passwords, password hashes, session tokens, or CSRF
tokens.  Callers must preserve that property when wiring it into HTTP logging.
"""

from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from typing import Any, Callable, Deque, Dict, Optional


STORE_VERSION = 1
HASH_SCHEME = "pbkdf2_sha256"
DEFAULT_PBKDF2_ITERATIONS = 600_000
DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60
DEFAULT_MAX_LOGIN_FAILURES = 5
DEFAULT_MAX_LOGIN_FAILURES_PER_IP = 25
DEFAULT_LOGIN_WINDOW_SECONDS = 5 * 60
DEFAULT_MAX_RATE_LIMIT_KEYS = 4096
GENERIC_AUTH_FAILURE = "Invalid username or password."

_SALT_BYTES = 16
_DIGEST_BYTES = 32
_MAX_HASH_ITERATIONS = 10_000_000


class WebGUIAuthError(Exception):
    """Base class for web GUI authentication errors."""


class CredentialValidationError(WebGUIAuthError, ValueError):
    """Raised for invalid local credential-management input."""


class CredentialStoreError(WebGUIAuthError):
    """Raised when the credential store is missing, corrupt, or unsupported."""


class AuthenticationFailed(WebGUIAuthError):
    """Generic authentication failure that does not reveal account state."""

    def __init__(self) -> None:
        super().__init__(GENERIC_AUTH_FAILURE)


@dataclass(frozen=True)
class AuthenticatedUser:
    """A verified identity without secret credential material."""

    username: str
    credential_revision: int
    store_revision: int


@dataclass(frozen=True)
class UserChange:
    """Revision information returned after a credential-store mutation."""

    username: str
    store_revision: int
    credential_revision: Optional[int]


@dataclass(frozen=True)
class SessionCredentials:
    """Raw tokens returned once to the HTTP integration after login."""

    username: str
    token: str
    csrf_token: str
    expires_at: float


@dataclass(frozen=True)
class SessionIdentity:
    """A validated session identity returned to request handlers."""

    username: str
    expires_at: float


@dataclass(frozen=True)
class LoginResult:
    """Public login result; all failures use :data:`GENERIC_AUTH_FAILURE`."""

    success: bool
    session: Optional[SessionCredentials] = None
    error: Optional[str] = None
    retry_after_seconds: int = 0


@dataclass
class _SessionRecord:
    username: str
    credential_revision: int
    store_revision: int
    csrf_digest: bytes
    expires_at: float
    public_expires_at: float


def _normalise_username(username: Any) -> str:
    if not isinstance(username, str):
        raise CredentialValidationError("Username must be a string.")
    normalised = username.strip()
    if not normalised:
        raise CredentialValidationError("Username cannot be empty.")
    if "\x00" in normalised or _contains_line_break(normalised):
        raise CredentialValidationError("Username cannot contain NUL or a newline.")
    if ":" in normalised:
        raise CredentialValidationError("Username cannot contain ':'.")
    return normalised


def _validate_password(password: Any) -> str:
    if not isinstance(password, str):
        raise CredentialValidationError("Password must be a string.")
    if password == "":
        raise CredentialValidationError("Password cannot be empty.")
    if "\x00" in password or _contains_line_break(password):
        raise CredentialValidationError("Password cannot contain NUL or a newline.")
    # Deliberately do not strip or otherwise normalise the password.  Spaces,
    # colons, semicolons, and all valid Unicode characters are significant.
    return password


def _contains_line_break(value: str) -> bool:
    # str.splitlines() recognises these in addition to CR and LF.  Keeping the
    # explicit set avoids treating an ordinary empty string as a line break.
    return any(character in value for character in "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")


def _validate_iterations(iterations: Any) -> int:
    if isinstance(iterations, bool):
        raise CredentialValidationError("PBKDF2 iterations must be an integer.")
    try:
        value = int(iterations)
    except (TypeError, ValueError) as exc:
        raise CredentialValidationError("PBKDF2 iterations must be an integer.") from exc
    if value < 1 or value > _MAX_HASH_ITERATIONS:
        raise CredentialValidationError(
            f"PBKDF2 iterations must be between 1 and {_MAX_HASH_ITERATIONS}."
        )
    return value


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64decode(value: str, field_name: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise CredentialValidationError(f"Invalid {field_name} in password hash.") from exc


def _decode_password_hash(encoded_hash: Any) -> tuple[int, bytes, bytes]:
    if not isinstance(encoded_hash, str):
        raise CredentialValidationError("Password hash must be a string.")
    parts = encoded_hash.split("$")
    if len(parts) != 4 or parts[0] != HASH_SCHEME:
        raise CredentialValidationError("Unsupported password hash format.")
    iterations = _validate_iterations(parts[1])
    salt = _b64decode(parts[2], "salt")
    expected_digest = _b64decode(parts[3], "digest")
    if len(salt) < _SALT_BYTES:
        raise CredentialValidationError("Password hash salt is too short.")
    if len(expected_digest) != _DIGEST_BYTES:
        raise CredentialValidationError("Password hash digest has an invalid length.")
    return iterations, salt, expected_digest


def hash_password(
    password: str,
    *,
    iterations: int = DEFAULT_PBKDF2_ITERATIONS,
    salt: Optional[bytes] = None,
) -> str:
    """Return a versioned PBKDF2-SHA256 hash for an exact Unicode password.

    ``salt`` is exposed only for deterministic tests and migration tooling.
    Production callers should leave it unset so a cryptographically random
    16-byte salt is generated.
    """

    password = _validate_password(password)
    iterations = _validate_iterations(iterations)
    if salt is None:
        salt = secrets.token_bytes(_SALT_BYTES)
    if not isinstance(salt, bytes) or len(salt) < _SALT_BYTES:
        raise CredentialValidationError(f"Password salt must be at least {_SALT_BYTES} bytes.")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=_DIGEST_BYTES,
    )
    return f"{HASH_SCHEME}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def is_password_hash(value: Any) -> bool:
    """Return ``True`` only for a structurally valid supported password hash."""

    try:
        _decode_password_hash(value)
        return True
    except CredentialValidationError:
        return False


def verify_password(password: Any, encoded_hash: Any) -> bool:
    """Verify an exact Unicode password using a byte-level constant-time compare."""

    try:
        password = _validate_password(password)
        iterations, salt, expected_digest = _decode_password_hash(encoded_hash)
    except CredentialValidationError:
        return False
    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=len(expected_digest),
    )
    return hmac.compare_digest(expected_digest, actual_digest)


def parse_legacy_credentials(
    raw_value: Optional[str],
    *,
    iterations: int = DEFAULT_PBKDF2_ITERATIONS,
) -> Dict[str, Dict[str, Any]]:
    """Parse and hash the legacy newline-separated ``username:value`` format.

    The first colon separates the username from the value.  The value may be
    either a supported hash or a plaintext password.  Passwords are never
    stripped and semicolons are ordinary password characters.  Empty fields,
    NUL/newline-containing fields, malformed hash-looking values, and duplicate
    normalised usernames are rejected.  Empty lines and comment lines beginning
    with ``#`` are ignored.
    """

    if raw_value is None:
        raw_value = ""
    if not isinstance(raw_value, str):
        raise CredentialValidationError("Legacy users must be a string.")
    iterations = _validate_iterations(iterations)
    users: Dict[str, Dict[str, Any]] = {}
    for line_number, line in enumerate(raw_value.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise CredentialValidationError(
                f"Legacy user line {line_number} must contain username:value."
            )
        raw_username, value = line.split(":", 1)
        username = _normalise_username(raw_username)
        _validate_password(value)
        if username in users:
            raise CredentialValidationError(f"Duplicate legacy username: {username}")
        if value.startswith(f"{HASH_SCHEME}$"):
            _decode_password_hash(value)
            password_hash = value
        else:
            password_hash = hash_password(value, iterations=iterations)
        users[username] = {
            "password_hash": password_hash,
            "enabled": True,
            "revision": 1,
        }
    return users


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CredentialStoreError(f"Duplicate JSON key in credential store: {key}")
        result[key] = value
    return result


class CredentialStore:
    """Versioned PBKDF2 credential store with atomic writes and mtime reloads.

    Args:
        base_dir: Application base directory.  The fixed store path is
            ``<base_dir>/data/web_gui_auth.json``.
        legacy_users: Optional legacy newline-separated credentials.  They are
            migrated only if the JSON store does not already exist.
        iterations: PBKDF2 work factor used for new/reset plaintext passwords.

    Secret-bearing records are intentionally private.  Public listing returns
    usernames only through :meth:`list_usernames`.
    """

    def __init__(
        self,
        base_dir: os.PathLike[str] | str,
        *,
        legacy_users: Optional[str] = None,
        iterations: int = DEFAULT_PBKDF2_ITERATIONS,
    ) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.path = self.base_dir / "data" / "web_gui_auth.json"
        self.iterations = _validate_iterations(iterations)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._mtime_ns: Optional[int] = None
        self._dummy_hash = hash_password(
            secrets.token_urlsafe(24),
            iterations=self.iterations,
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self._load_locked()
            else:
                users = parse_legacy_credentials(legacy_users, iterations=self.iterations)
                self._data = {
                    "version": STORE_VERSION,
                    "revision": 1 if users else 0,
                    "users": users,
                }
                self._write_locked()

    @property
    def revision(self) -> int:
        """Return the latest store-wide revision, reloading external changes."""

        with self._lock:
            self._reload_if_changed_locked()
            return int(self._data["revision"])

    def list_usernames(self, *, enabled_only: bool = False) -> list[str]:
        """Return sorted usernames only; hashes and revisions never leave the store."""

        with self._lock:
            self._reload_if_changed_locked()
            users = self._data["users"]
            return sorted(
                username
                for username, record in users.items()
                if not enabled_only or bool(record["enabled"])
            )

    def authenticate(self, username: Any, password: Any) -> Optional[AuthenticatedUser]:
        """Verify credentials and return a non-secret identity or ``None``.

        Unknown users execute the same PBKDF2 verification path using a dummy
        hash.  Disabled accounts verify their stored hash but still return
        ``None``.  Input validation details are intentionally not exposed here.
        """

        try:
            normalised_username = _normalise_username(username)
            _validate_password(password)
        except CredentialValidationError:
            normalised_username = ""
            password = password if isinstance(password, str) and password else "invalid"

        with self._lock:
            self._reload_if_changed_locked()
            record = self._data["users"].get(normalised_username)
            store_revision = int(self._data["revision"])
            encoded_hash = record["password_hash"] if record else self._dummy_hash

        matches = verify_password(password, encoded_hash)
        if not record or not bool(record["enabled"]) or not matches:
            return None
        return AuthenticatedUser(
            username=normalised_username,
            credential_revision=int(record["revision"]),
            store_revision=store_revision,
        )

    def upsert_user(self, username: str, password: str, *, enabled: bool = True) -> UserChange:
        """Create or replace a user password and increment all relevant revisions."""

        username = _normalise_username(username)
        password = _validate_password(password)
        if not isinstance(enabled, bool):
            raise CredentialValidationError("enabled must be a boolean.")
        encoded_hash = hash_password(password, iterations=self.iterations)
        with self._lock:
            self._reload_if_changed_locked()
            old_record = self._data["users"].get(username)
            credential_revision = int(old_record["revision"]) + 1 if old_record else 1
            self._data["users"][username] = {
                "password_hash": encoded_hash,
                "enabled": enabled,
                "revision": credential_revision,
            }
            store_revision = self._increment_store_revision_locked()
            self._write_locked()
            return UserChange(username, store_revision, credential_revision)

    def reset_password(self, username: str, password: str) -> UserChange:
        """Reset an existing user's password and invalidate that user's sessions."""

        username = _normalise_username(username)
        password = _validate_password(password)
        encoded_hash = hash_password(password, iterations=self.iterations)
        with self._lock:
            self._reload_if_changed_locked()
            old_record = self._data["users"].get(username)
            if old_record is None:
                raise CredentialValidationError(f"Unknown username: {username}")
            credential_revision = int(old_record["revision"]) + 1
            self._data["users"][username] = {
                "password_hash": encoded_hash,
                "enabled": bool(old_record["enabled"]),
                "revision": credential_revision,
            }
            store_revision = self._increment_store_revision_locked()
            self._write_locked()
            return UserChange(username, store_revision, credential_revision)

    def set_user_enabled(self, username: str, enabled: bool) -> UserChange:
        """Enable or disable an existing user and invalidate existing sessions."""

        username = _normalise_username(username)
        if not isinstance(enabled, bool):
            raise CredentialValidationError("enabled must be a boolean.")
        with self._lock:
            self._reload_if_changed_locked()
            old_record = self._data["users"].get(username)
            if old_record is None:
                raise CredentialValidationError(f"Unknown username: {username}")
            credential_revision = int(old_record["revision"]) + 1
            self._data["users"][username] = {
                "password_hash": old_record["password_hash"],
                "enabled": enabled,
                "revision": credential_revision,
            }
            store_revision = self._increment_store_revision_locked()
            self._write_locked()
            return UserChange(username, store_revision, credential_revision)

    def delete_user(self, username: str) -> Optional[UserChange]:
        """Delete a user, returning ``None`` without a revision bump if absent."""

        username = _normalise_username(username)
        with self._lock:
            self._reload_if_changed_locked()
            if username not in self._data["users"]:
                return None
            del self._data["users"][username]
            store_revision = self._increment_store_revision_locked()
            self._write_locked()
            return UserChange(username, store_revision, None)

    def enabled_user_revision(self, username: Any) -> Optional[int]:
        """Return an enabled user's credential revision for session validation."""

        try:
            username = _normalise_username(username)
        except CredentialValidationError:
            return None
        with self._lock:
            self._reload_if_changed_locked()
            record = self._data["users"].get(username)
            if not record or not bool(record["enabled"]):
                return None
            return int(record["revision"])

    def _increment_store_revision_locked(self) -> int:
        revision = int(self._data["revision"]) + 1
        self._data["revision"] = revision
        return revision

    def _reload_if_changed_locked(self) -> None:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except FileNotFoundError as exc:
            raise CredentialStoreError(f"Credential store is missing: {self.path}") from exc
        if self._mtime_ns != mtime_ns:
            self._load_locked()

    def _load_locked(self) -> None:
        try:
            with self.path.open("r", encoding="utf-8") as file_handle:
                data = json.load(file_handle, object_pairs_hook=_reject_duplicate_json_keys)
        except CredentialStoreError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CredentialStoreError(f"Cannot read credential store: {self.path}") from exc
        self._validate_store_data(data)
        self._data = data
        try:
            self._mtime_ns = self.path.stat().st_mtime_ns
        except FileNotFoundError as exc:
            raise CredentialStoreError(f"Credential store disappeared while loading: {self.path}") from exc

    def _write_locked(self) -> None:
        self._validate_store_data(self._data)
        temporary_path: Optional[str] = None
        try:
            file_descriptor, temporary_path = tempfile.mkstemp(
                prefix=".web_gui_auth.",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            try:
                os.chmod(temporary_path, 0o600)
            except OSError:
                # Windows only partially implements POSIX permission bits.
                pass
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as file_handle:
                json.dump(self._data, file_handle, ensure_ascii=False, indent=2, sort_keys=True)
                file_handle.write("\n")
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self._mtime_ns = self.path.stat().st_mtime_ns
        except OSError as exc:
            raise CredentialStoreError(f"Cannot write credential store: {self.path}") from exc
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _validate_store_data(data: Any) -> None:
        if not isinstance(data, dict):
            raise CredentialStoreError("Credential store root must be a JSON object.")
        if data.get("version") != STORE_VERSION:
            raise CredentialStoreError(
                f"Unsupported credential store version: {data.get('version')!r}"
            )
        revision = data.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise CredentialStoreError("Credential store revision must be a non-negative integer.")
        users = data.get("users")
        if not isinstance(users, dict):
            raise CredentialStoreError("Credential store users must be a JSON object.")
        for raw_username, record in users.items():
            try:
                username = _normalise_username(raw_username)
            except CredentialValidationError as exc:
                raise CredentialStoreError("Credential store contains an invalid username.") from exc
            if username != raw_username:
                raise CredentialStoreError("Stored usernames must already be normalised.")
            if not isinstance(record, dict):
                raise CredentialStoreError(f"Credential record for {username} must be an object.")
            if set(record) != {"password_hash", "enabled", "revision"}:
                raise CredentialStoreError(f"Credential record for {username} has invalid fields.")
            try:
                _decode_password_hash(record["password_hash"])
            except CredentialValidationError as exc:
                raise CredentialStoreError(f"Credential record for {username} has an invalid hash.") from exc
            if not isinstance(record["enabled"], bool):
                raise CredentialStoreError(f"Credential record for {username} has invalid enabled state.")
            user_revision = record["revision"]
            if isinstance(user_revision, bool) or not isinstance(user_revision, int) or user_revision < 1:
                raise CredentialStoreError(f"Credential record for {username} has invalid revision.")


class LoginRateLimiter:
    """Bounded sliding-window limiter for both IP/username pairs and IPs."""

    def __init__(
        self,
        *,
        max_failures: int = DEFAULT_MAX_LOGIN_FAILURES,
        max_failures_per_ip: int = DEFAULT_MAX_LOGIN_FAILURES_PER_IP,
        window_seconds: float = DEFAULT_LOGIN_WINDOW_SECONDS,
        max_keys: int = DEFAULT_MAX_RATE_LIMIT_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(max_failures, bool) or int(max_failures) < 1:
            raise ValueError("max_failures must be a positive integer.")
        if isinstance(max_failures_per_ip, bool) or int(max_failures_per_ip) < int(max_failures):
            raise ValueError("max_failures_per_ip must be at least max_failures.")
        if float(window_seconds) <= 0:
            raise ValueError("window_seconds must be positive.")
        if isinstance(max_keys, bool) or int(max_keys) < 1:
            raise ValueError("max_keys must be a positive integer.")
        self.max_failures = int(max_failures)
        self.max_failures_per_ip = int(max_failures_per_ip)
        self.window_seconds = float(window_seconds)
        self.max_keys = int(max_keys)
        self._clock = clock
        self._failures: Dict[tuple[str, str], Deque[float]] = {}
        self._ip_failures: Dict[str, Deque[float]] = {}
        self._lock = threading.RLock()

    def is_allowed(self, client_ip: Any, username: Any) -> bool:
        """Return whether another credential verification may be attempted."""

        with self._lock:
            self._prune_all_locked()
            pair_failures = self._active_failures_locked(self._failures, self._key(client_ip, username))
            ip_failures = self._active_failures_locked(self._ip_failures, self._ip_key(client_ip))
            return (
                len(pair_failures) < self.max_failures
                and len(ip_failures) < self.max_failures_per_ip
            )

    def record_failure(self, client_ip: Any, username: Any) -> int:
        """Record a failed attempt and return whole retry-after seconds, or zero."""

        key = self._key(client_ip, username)
        ip_key = self._ip_key(client_ip)
        with self._lock:
            self._prune_all_locked()
            now = float(self._clock())
            failures = self._active_failures_locked(self._failures, key)
            failures.append(now)
            self._failures[key] = failures
            ip_failures = self._active_failures_locked(self._ip_failures, ip_key)
            ip_failures.append(now)
            self._ip_failures[ip_key] = ip_failures
            self._enforce_bounds_locked()
            return max(
                self._retry_after_locked(failures, self.max_failures),
                self._retry_after_locked(ip_failures, self.max_failures_per_ip),
            )

    def record_success(self, client_ip: Any, username: Any) -> None:
        """Clear failures for a successfully authenticated IP/username pair."""

        with self._lock:
            self._prune_all_locked()
            self._failures.pop(self._key(client_ip, username), None)

    def retry_after(self, client_ip: Any, username: Any) -> int:
        """Return whole seconds until the pair may try again, or zero."""

        with self._lock:
            self._prune_all_locked()
            failures = self._active_failures_locked(self._failures, self._key(client_ip, username))
            ip_failures = self._active_failures_locked(self._ip_failures, self._ip_key(client_ip))
            return max(
                self._retry_after_locked(failures, self.max_failures),
                self._retry_after_locked(ip_failures, self.max_failures_per_ip),
            )

    def _key(self, client_ip: Any, username: Any) -> tuple[str, str]:
        ip_text = self._ip_key(client_ip)
        try:
            username_text = _normalise_username(username)
        except CredentialValidationError:
            username_text = str(username or "")[:256]
        return ip_text, username_text

    @staticmethod
    def _ip_key(client_ip: Any) -> str:
        return str(client_ip or "").strip()[:256]

    def _active_failures_locked(self, store: Dict[Any, Deque[float]], key: Any) -> Deque[float]:
        now = float(self._clock())
        cutoff = now - self.window_seconds
        failures = store.get(key, deque())
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if failures:
            store[key] = failures
        else:
            store.pop(key, None)
        return failures

    def _retry_after_locked(self, failures: Deque[float], limit: int) -> int:
        if len(failures) < limit:
            return 0
        remaining = failures[0] + self.window_seconds - float(self._clock())
        return max(1, int(math.ceil(remaining)))

    def _prune_all_locked(self) -> None:
        for key in list(self._failures):
            self._active_failures_locked(self._failures, key)
        for key in list(self._ip_failures):
            self._active_failures_locked(self._ip_failures, key)
        self._enforce_bounds_locked()

    def _enforce_bounds_locked(self) -> None:
        for store in (self._failures, self._ip_failures):
            while len(store) > self.max_keys:
                oldest_key = min(
                    store,
                    key=lambda key: store[key][-1] if store[key] else float("-inf"),
                )
                store.pop(oldest_key, None)


class SessionManager:
    """Opaque in-memory sessions with CSRF and credential-revision checks."""

    def __init__(
        self,
        credential_store: CredentialStore,
        *,
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if float(ttl_seconds) <= 0:
            raise ValueError("ttl_seconds must be positive.")
        self.credential_store = credential_store
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._wall_clock = wall_clock
        self._sessions: Dict[bytes, _SessionRecord] = {}
        self._lock = threading.RLock()

    def create_session(self, identity: AuthenticatedUser) -> SessionCredentials:
        """Issue an opaque session and CSRF token for a freshly verified identity."""

        current_revision = self.credential_store.enabled_user_revision(identity.username)
        if (
            current_revision != identity.credential_revision
            or self.credential_store.revision != identity.store_revision
        ):
            raise AuthenticationFailed()
        with self._lock:
            self._prune_expired_locked()
            while True:
                token = secrets.token_urlsafe(32)
                token_digest = self._token_digest(token)
                if token_digest not in self._sessions:
                    break
            csrf_token = secrets.token_urlsafe(32)
            expires_at = float(self._clock()) + self.ttl_seconds
            public_expires_at = float(self._wall_clock()) + self.ttl_seconds
            self._sessions[token_digest] = _SessionRecord(
                username=identity.username,
                credential_revision=identity.credential_revision,
                store_revision=identity.store_revision,
                csrf_digest=self._token_digest(csrf_token),
                expires_at=expires_at,
                public_expires_at=public_expires_at,
            )
        return SessionCredentials(identity.username, token, csrf_token, public_expires_at)

    def authenticate_session(
        self,
        token: Any,
        *,
        csrf_token: Any = None,
        require_csrf: bool = False,
    ) -> Optional[SessionIdentity]:
        """Validate a session, optionally requiring its matching CSRF token."""

        if not isinstance(token, str) or not token:
            return None
        token_digest = self._token_digest(token)
        with self._lock:
            self._prune_expired_locked()
            record = self._sessions.get(token_digest)
            if record is None:
                return None
            if require_csrf:
                if not isinstance(csrf_token, str) or not csrf_token:
                    return None
                provided_csrf_digest = self._token_digest(csrf_token)
                if not hmac.compare_digest(record.csrf_digest, provided_csrf_digest):
                    return None
            current_revision = self.credential_store.enabled_user_revision(record.username)
            if (
                current_revision != record.credential_revision
                or self.credential_store.revision != record.store_revision
            ):
                self._sessions.pop(token_digest, None)
                return None
            return SessionIdentity(record.username, record.public_expires_at)

    def revoke_session(self, token: Any) -> bool:
        """Revoke one session token, returning whether it existed."""

        if not isinstance(token, str) or not token:
            return False
        with self._lock:
            return self._sessions.pop(self._token_digest(token), None) is not None

    def revoke_user(self, username: Any) -> int:
        """Revoke every in-memory session for a username and return the count."""

        try:
            username = _normalise_username(username)
        except CredentialValidationError:
            return 0
        with self._lock:
            matching = [
                token_digest
                for token_digest, record in self._sessions.items()
                if record.username == username
            ]
            for token_digest in matching:
                self._sessions.pop(token_digest, None)
            return len(matching)

    def clear(self) -> None:
        """Revoke all sessions (for example during server shutdown)."""

        with self._lock:
            self._sessions.clear()

    @staticmethod
    def _token_digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    def _prune_expired_locked(self) -> None:
        now = float(self._clock())
        expired = [
            token_digest
            for token_digest, record in self._sessions.items()
            if now >= record.expires_at
        ]
        for token_digest in expired:
            self._sessions.pop(token_digest, None)


class WebGUIAuthService:
    """Facade used by HTTP handlers for login, session checks, and logout."""

    def __init__(
        self,
        credential_store: CredentialStore,
        *,
        session_manager: Optional[SessionManager] = None,
        rate_limiter: Optional[LoginRateLimiter] = None,
    ) -> None:
        self.credential_store = credential_store
        self.sessions = session_manager or SessionManager(credential_store)
        self.rate_limiter = rate_limiter or LoginRateLimiter()

    def login(self, username: Any, password: Any, *, client_ip: Any = "") -> LoginResult:
        """Authenticate and issue a session, with a generic result for all failures."""

        retry_after = self.rate_limiter.retry_after(client_ip, username)
        if retry_after:
            return LoginResult(False, error=GENERIC_AUTH_FAILURE, retry_after_seconds=retry_after)

        identity = self.credential_store.authenticate(username, password)
        if identity is None:
            retry_after = self.rate_limiter.record_failure(client_ip, username)
            return LoginResult(False, error=GENERIC_AUTH_FAILURE, retry_after_seconds=retry_after)

        self.rate_limiter.record_success(client_ip, username)
        try:
            session = self.sessions.create_session(identity)
        except AuthenticationFailed:
            return LoginResult(False, error=GENERIC_AUTH_FAILURE)
        return LoginResult(True, session=session)

    def authenticate_session(
        self,
        token: Any,
        *,
        csrf_token: Any = None,
        require_csrf: bool = False,
    ) -> Optional[SessionIdentity]:
        """Delegate a session and optional CSRF validation to the session manager."""

        return self.sessions.authenticate_session(
            token,
            csrf_token=csrf_token,
            require_csrf=require_csrf,
        )

    def logout(self, token: Any) -> bool:
        """Revoke an opaque session token."""

        return self.sessions.revoke_session(token)


__all__ = [
    "AuthenticatedUser",
    "AuthenticationFailed",
    "CredentialStore",
    "CredentialStoreError",
    "CredentialValidationError",
    "DEFAULT_LOGIN_WINDOW_SECONDS",
    "DEFAULT_MAX_LOGIN_FAILURES",
    "DEFAULT_MAX_LOGIN_FAILURES_PER_IP",
    "DEFAULT_MAX_RATE_LIMIT_KEYS",
    "DEFAULT_PBKDF2_ITERATIONS",
    "DEFAULT_SESSION_TTL_SECONDS",
    "GENERIC_AUTH_FAILURE",
    "HASH_SCHEME",
    "LoginRateLimiter",
    "LoginResult",
    "SessionCredentials",
    "SessionIdentity",
    "SessionManager",
    "STORE_VERSION",
    "UserChange",
    "WebGUIAuthError",
    "WebGUIAuthService",
    "hash_password",
    "is_password_hash",
    "parse_legacy_credentials",
    "verify_password",
]
