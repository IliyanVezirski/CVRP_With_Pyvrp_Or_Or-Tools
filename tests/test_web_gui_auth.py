import json
import os
from pathlib import Path
import tempfile
import unittest

from web_gui_auth import (
    CredentialStore,
    CredentialStoreError,
    CredentialValidationError,
    GENERIC_AUTH_FAILURE,
    HASH_SCHEME,
    LoginRateLimiter,
    SessionManager,
    STORE_VERSION,
    WebGUIAuthService,
    hash_password,
    is_password_hash,
    parse_legacy_credentials,
    verify_password,
)


TEST_ITERATIONS = 1_000


class FakeClock:
    def __init__(self, initial=1_000.0):
        self.value = float(initial)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class PasswordHashTests(unittest.TestCase):
    def test_unicode_password_is_hashed_and_verified_as_utf8_bytes(self):
        password = "  п@рола;: 密碼 🔐  "
        encoded = hash_password(password, iterations=TEST_ITERATIONS)

        self.assertTrue(encoded.startswith(f"{HASH_SCHEME}${TEST_ITERATIONS}$"))
        self.assertTrue(is_password_hash(encoded))
        self.assertTrue(verify_password(password, encoded))
        self.assertFalse(verify_password(password.strip(), encoded))
        self.assertFalse(verify_password("друга", encoded))

    def test_random_salts_produce_distinct_hashes(self):
        first = hash_password("same", iterations=TEST_ITERATIONS)
        second = hash_password("same", iterations=TEST_ITERATIONS)

        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("same", first))
        self.assertTrue(verify_password("same", second))

    def test_invalid_password_and_hash_are_rejected_safely(self):
        with self.assertRaises(CredentialValidationError):
            hash_password("", iterations=TEST_ITERATIONS)
        with self.assertRaises(CredentialValidationError):
            hash_password("line\nbreak", iterations=TEST_ITERATIONS)
        with self.assertRaises(CredentialValidationError):
            hash_password("nul\x00byte", iterations=TEST_ITERATIONS)
        self.assertFalse(is_password_hash("pbkdf2_sha256$bad"))
        self.assertFalse(verify_password("password", "not-a-hash"))


class LegacyMigrationTests(unittest.TestCase):
    def test_password_is_not_split_on_semicolon_or_stripped(self):
        exact_password = "  value;with:semicolon:and:colon  "
        users = parse_legacy_credentials(
            f" alice :{exact_password}\n",
            iterations=TEST_ITERATIONS,
        )

        self.assertEqual(list(users), ["alice"])
        self.assertTrue(verify_password(exact_password, users["alice"]["password_hash"]))
        self.assertFalse(verify_password(exact_password.strip(), users["alice"]["password_hash"]))

    def test_valid_existing_hash_is_preserved(self):
        existing = hash_password("secret", iterations=TEST_ITERATIONS)
        users = parse_legacy_credentials(
            f"alice:{existing}",
            iterations=TEST_ITERATIONS,
        )

        self.assertEqual(users["alice"]["password_hash"], existing)

    def test_duplicate_and_invalid_legacy_entries_are_rejected(self):
        invalid_values = (
            "alice:first\n alice :second",
            ":password",
            "alice:",
            "missing-colon",
            "alice:bad\x00password",
            "alice:pbkdf2_sha256$malformed",
        )
        for raw_value in invalid_values:
            with self.subTest(raw_value=repr(raw_value)):
                with self.assertRaises(CredentialValidationError):
                    parse_legacy_credentials(raw_value, iterations=TEST_ITERATIONS)

    def test_comments_and_empty_lines_are_ignored(self):
        users = parse_legacy_credentials(
            "\n   \n# old comment\n  # indented comment\nalice:secret\n",
            iterations=TEST_ITERATIONS,
        )
        self.assertEqual(list(users), ["alice"])


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_store(self, **kwargs):
        return CredentialStore(
            self.base_dir,
            iterations=TEST_ITERATIONS,
            **kwargs,
        )

    def test_store_path_schema_and_migration_contain_no_plaintext(self):
        plaintext = "unique migration; password  "
        store = self.make_store(legacy_users=f"alice:{plaintext}")

        self.assertEqual(store.path, self.base_dir / "data" / "web_gui_auth.json")
        raw_text = store.path.read_text(encoding="utf-8")
        document = json.loads(raw_text)
        self.assertEqual(document["version"], STORE_VERSION)
        self.assertEqual(document["revision"], 1)
        self.assertNotIn(plaintext, raw_text)
        self.assertTrue(document["users"]["alice"]["enabled"])
        self.assertEqual(store.list_usernames(), ["alice"])
        self.assertEqual(store.list_usernames(enabled_only=True), ["alice"])
        self.assertIsNotNone(store.authenticate("alice", plaintext))

    def test_existing_store_wins_over_later_legacy_value(self):
        first = self.make_store(legacy_users="alice:first")
        second = self.make_store(legacy_users="mallory:second")

        self.assertEqual(first.list_usernames(), ["alice"])
        self.assertEqual(second.list_usernames(), ["alice"])
        self.assertIsNotNone(second.authenticate("alice", "first"))
        self.assertIsNone(second.authenticate("mallory", "second"))

    def test_upsert_reset_enable_and_delete_increment_revisions(self):
        store = self.make_store()
        self.assertEqual(store.revision, 0)

        added = store.upsert_user("alice", "first")
        self.assertEqual((added.store_revision, added.credential_revision), (1, 1))
        self.assertIsNotNone(store.authenticate("alice", "first"))

        reset = store.reset_password("alice", " second; ")
        self.assertEqual((reset.store_revision, reset.credential_revision), (2, 2))
        self.assertIsNone(store.authenticate("alice", "first"))
        self.assertIsNotNone(store.authenticate("alice", " second; "))

        disabled = store.set_user_enabled("alice", False)
        self.assertEqual((disabled.store_revision, disabled.credential_revision), (3, 3))
        self.assertEqual(store.list_usernames(enabled_only=True), [])
        self.assertIsNone(store.authenticate("alice", " second; "))

        enabled = store.set_user_enabled("alice", True)
        self.assertEqual((enabled.store_revision, enabled.credential_revision), (4, 4))
        deleted = store.delete_user("alice")
        self.assertIsNotNone(deleted)
        self.assertEqual(deleted.store_revision, 5)
        self.assertIsNone(deleted.credential_revision)
        self.assertIsNone(store.delete_user("alice"))
        self.assertEqual(store.revision, 5)

    def test_store_reloads_external_changes_by_mtime(self):
        observing_store = self.make_store()
        original_mtime = observing_store.path.stat().st_mtime_ns
        writing_store = self.make_store()
        writing_store.upsert_user("bob", "secret")

        current_mtime = observing_store.path.stat().st_mtime_ns
        if current_mtime <= original_mtime:
            bumped = original_mtime + 10_000_000
            os.utime(observing_store.path, ns=(bumped, bumped))

        self.assertEqual(observing_store.list_usernames(), ["bob"])
        self.assertIsNotNone(observing_store.authenticate("bob", "secret"))

    def test_missing_or_malformed_store_fails_closed(self):
        store = self.make_store(legacy_users="alice:secret")
        store.path.unlink()
        with self.assertRaises(CredentialStoreError):
            store.list_usernames()

        store = self.make_store(legacy_users="alice:secret")
        document = json.loads(store.path.read_text(encoding="utf-8"))
        document["version"] = 999
        store.path.write_text(json.dumps(document), encoding="utf-8")
        stat = store.path.stat()
        os.utime(store.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
        with self.assertRaises(CredentialStoreError):
            store.list_usernames()

    def test_management_input_rejects_empty_newline_and_nul(self):
        store = self.make_store()
        invalid_pairs = (
            ("", "password"),
            ("alice\nadmin", "password"),
            ("alice", ""),
            ("alice", "line\nbreak"),
            ("alice", "unicode\u2028break"),
            ("alice", "nul\x00password"),
        )
        for username, password in invalid_pairs:
            with self.subTest(username=repr(username), password=repr(password)):
                with self.assertRaises(CredentialValidationError):
                    store.upsert_user(username, password)


class SessionAndRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.store = CredentialStore(
            self.temp_dir.name,
            iterations=TEST_ITERATIONS,
        )
        self.store.upsert_user("alice", " точна;парола ")
        self.limiter = LoginRateLimiter(clock=self.clock)
        self.sessions = SessionManager(
            self.store,
            ttl_seconds=120,
            clock=self.clock,
            wall_clock=lambda: 1_800_000_000,
        )
        self.service = WebGUIAuthService(
            self.store,
            session_manager=self.sessions,
            rate_limiter=self.limiter,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def login(self, password=" точна;парола ", ip="10.0.0.1"):
        return self.service.login("alice", password, client_ip=ip)

    def test_login_session_and_csrf_round_trip(self):
        result = self.login()

        self.assertTrue(result.success)
        self.assertIsNone(result.error)
        self.assertIsNotNone(result.session)
        self.assertEqual(result.session.expires_at, 1_800_000_120)
        self.assertNotIn("alice", result.session.token)
        identity = self.service.authenticate_session(result.session.token)
        self.assertEqual(identity.username, "alice")
        self.assertIsNone(
            self.service.authenticate_session(result.session.token, require_csrf=True)
        )
        self.assertIsNone(
            self.service.authenticate_session(
                result.session.token,
                csrf_token="wrong",
                require_csrf=True,
            )
        )
        csrf_identity = self.service.authenticate_session(
            result.session.token,
            csrf_token=result.session.csrf_token,
            require_csrf=True,
        )
        self.assertEqual(csrf_identity.username, "alice")

    def test_expiry_and_logout_revoke_sessions(self):
        expired = self.login().session
        self.clock.advance(120)
        self.assertIsNone(self.service.authenticate_session(expired.token))

        active = self.login().session
        self.assertTrue(self.service.logout(active.token))
        self.assertFalse(self.service.logout(active.token))
        self.assertIsNone(self.service.authenticate_session(active.token))

    def test_password_reset_disable_and_delete_invalidate_sessions(self):
        first = self.login().session
        self.store.reset_password("alice", "new password")
        self.assertIsNone(self.service.authenticate_session(first.token))

        second = self.service.login("alice", "new password", client_ip="10.0.0.1").session
        self.store.set_user_enabled("alice", False)
        self.assertIsNone(self.service.authenticate_session(second.token))

        self.store.set_user_enabled("alice", True)
        third = self.service.login("alice", "new password", client_ip="10.0.0.1").session
        self.store.delete_user("alice")
        self.assertIsNone(self.service.authenticate_session(third.token))

    def test_any_credential_store_revision_change_invalidates_sessions(self):
        session = self.login().session
        self.store.upsert_user("bob", "another secret")

        self.assertIsNone(self.service.authenticate_session(session.token))

    def test_all_login_failures_have_one_generic_message(self):
        disabled_store = CredentialStore(
            Path(self.temp_dir.name) / "disabled",
            iterations=TEST_ITERATIONS,
        )
        disabled_store.upsert_user("disabled", "secret", enabled=False)
        disabled_service = WebGUIAuthService(
            disabled_store,
            session_manager=SessionManager(disabled_store, clock=self.clock),
            rate_limiter=LoginRateLimiter(clock=self.clock),
        )

        results = (
            self.service.login("missing", "secret", client_ip="1"),
            self.service.login("alice", "wrong", client_ip="2"),
            self.service.login("", "secret", client_ip="3"),
            disabled_service.login("disabled", "secret", client_ip="4"),
        )
        self.assertTrue(all(not result.success for result in results))
        self.assertEqual({result.error for result in results}, {GENERIC_AUTH_FAILURE})

    def test_rate_limiter_blocks_five_failures_for_five_minutes(self):
        for attempt in range(5):
            result = self.login(password="wrong")
            self.assertFalse(result.success)
            if attempt < 4:
                self.assertEqual(result.retry_after_seconds, 0)

        self.assertEqual(result.retry_after_seconds, 300)
        self.assertFalse(self.limiter.is_allowed("10.0.0.1", "alice"))
        blocked = self.login(password=" точна;парола ")
        self.assertFalse(blocked.success)
        self.assertEqual(blocked.error, GENERIC_AUTH_FAILURE)
        self.assertEqual(blocked.retry_after_seconds, 300)

        # The limit is scoped to both IP and username.
        self.assertTrue(self.login(ip="10.0.0.2").success)
        self.clock.advance(300)
        self.assertTrue(self.login().success)

    def test_rate_limiter_blocks_username_rotation_from_one_ip(self):
        limiter = LoginRateLimiter(
            max_failures=5,
            max_failures_per_ip=25,
            clock=self.clock,
        )
        for index in range(25):
            limiter.record_failure("10.0.0.9", f"rotated-{index}")

        self.assertFalse(limiter.is_allowed("10.0.0.9", "brand-new-name"))
        self.assertGreater(limiter.retry_after("10.0.0.9", "brand-new-name"), 0)
        self.assertTrue(limiter.is_allowed("10.0.0.10", "brand-new-name"))

    def test_rate_limiter_prunes_and_bounds_unique_keys(self):
        limiter = LoginRateLimiter(
            max_failures=2,
            max_failures_per_ip=20,
            max_keys=3,
            window_seconds=60,
            clock=self.clock,
        )
        for index in range(10):
            limiter.record_failure(f"10.0.0.{index}", f"user-{index}")

        self.assertLessEqual(len(limiter._failures), 3)
        self.assertLessEqual(len(limiter._ip_failures), 3)
        self.clock.advance(60)
        self.assertTrue(limiter.is_allowed("10.0.0.99", "fresh"))
        self.assertEqual(limiter._failures, {})
        self.assertEqual(limiter._ip_failures, {})


if __name__ == "__main__":
    unittest.main()
