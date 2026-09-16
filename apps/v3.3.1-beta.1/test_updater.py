#!/usr/bin/env python3
"""稳定版更新引擎的离线网络、ZIP 安全、激活与备份测试。"""

import hashlib
import io
import json
from pathlib import Path
import sqlite3
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

import updater
from updater import UpdateError, UpdateManager


class FakeResponse:
    def __init__(self, payload, url):
        self.stream = io.BytesIO(payload)
        self.url = url

    def read(self, size=-1):
        return self.stream.read(size)

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        return False


class SSLFailingResponse(FakeResponse):
    def read(self, size=-1):
        raise ssl.SSLEOFError(ssl.SSL_ERROR_EOF, "unexpected EOF")


def make_bundle(version="3.3.0", extra_entries=None, corrupt_internal=False):
    files = {
        "launcher.py": b"print('launcher')\n",
        "apps/v%s/VERSION" % version: (version + "\n").encode(),
        "apps/v%s/ielts.py" % version: b"print('ielts')\n",
    }
    manifest = {
        "bundle_version": version + "-github.7",
        "default_app": version,
        "personal_learning_data_included": False,
        "files": {name: hashlib.sha256(value).hexdigest() for name, value in sorted(files.items())},
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    files["BUNDLE_MANIFEST.json"] = manifest_bytes
    sums = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    if corrupt_internal:
        sums["launcher.py"] = "0" * 64
    files["SHA256SUMS"] = "".join("%s  %s\n" % (digest, name) for name, digest in sorted(sums.items())).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in sorted(files.items()):
            archive.writestr(name, value)
        for name, value in extra_entries or []:
            archive.writestr(name, value)
    return output.getvalue()


def release_payload(bundle, version="3.3.0", **overrides):
    asset_name = "IELTS-CLI-Portable-%s.zip" % version
    release = {
        "draft": False,
        "prerelease": False,
        "tag_name": "v" + version,
        "assets": [{
            "name": asset_name,
            "browser_download_url": "https://github.com/RoreProMAX/ielts-cli/releases/download/v%s/%s" % (version, asset_name),
            "digest": "sha256:" + hashlib.sha256(bundle).hexdigest(),
            "size": len(bundle),
        }],
    }
    release.update(overrides)
    return json.dumps(release).encode()


class FakeTransport:
    def __init__(self, metadata, bundle=None, download_url=None):
        self.metadata = metadata
        self.bundle = bundle
        self.download_url = download_url
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request.full_url, timeout, dict(request.header_items())))
        if request.full_url in (updater.LATEST_RELEASE_URL, updater.ALL_RELEASES_URL):
            return FakeResponse(self.metadata, request.full_url)
        if self.bundle is not None:
            return FakeResponse(self.bundle, self.download_url or request.full_url)
        raise urllib.error.URLError("offline")


def wait_for(manager, terminal_states=("current", "available", "ready", "error")):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = manager.poll()
        if status["state"] in terminal_states:
            return status
        time.sleep(0.01)
    raise AssertionError("updater did not finish: %r" % manager.poll())


class UpdateManagerTests(unittest.TestCase):
    def test_channel_switch_during_activation_cannot_write_old_pointer(self):
        version = "3.3.1-beta.1"
        bundle = make_bundle(version)
        metadata = json.dumps([json.loads(release_payload(bundle, version, prerelease=True))]).encode()
        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.3.0", transport=FakeTransport(metadata, bundle))
            manager.set_channel("beta")
            manager.check_async(); self.assertEqual(wait_for(manager)["state"], "available")
            manager.install_async(); self.assertEqual(wait_for(manager)["state"], "ready")
            old_pointer = {"schema": 1, "version": "3.3.0", "digest": "0" * 64,
                           "activated_at": "old"}
            manager._atomic_json(manager.active_path, old_pointer)
            entered = threading.Event()
            release = threading.Event()
            def blocked_backup(_version):
                entered.set()
                release.wait(2)
            manager._backup_profile = blocked_backup
            result = []
            worker = threading.Thread(target=lambda: result.append(manager.activate_ready()))
            worker.start(); self.assertTrue(entered.wait(2))
            manager.set_channel("stable")
            release.set(); worker.join(2)
            self.assertEqual(result, [None])
            self.assertEqual(json.loads(manager.active_path.read_text(encoding="utf-8")), old_pointer)

    def test_beta_bundle_stages_activates_and_is_recovered(self):
        version = "3.3.1-beta.1"
        bundle = make_bundle(version)
        metadata = json.dumps([json.loads(release_payload(bundle, version, prerelease=True))]).encode()
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(metadata, bundle)
            manager = UpdateManager(directory, "3.3.0", transport=transport)
            manager.set_channel("beta")
            manager.check_async()
            status = wait_for(manager)
            self.assertEqual(status["state"], "available")
            self.assertIn("Beta 版", status["message"])
            self.assertTrue(manager.install_async())
            self.assertEqual(wait_for(manager)["state"], "ready")
            launcher = manager.activate_ready()
            self.assertEqual(launcher, Path(directory).resolve() / ("updates/releases/%s/launcher.py" % version))
            self.assertEqual(manager.active_launcher(), launcher)
            restored = UpdateManager(directory, "3.3.0")
            self.assertEqual(restored.channel, "beta")
            self.assertEqual(restored.active_launcher(), launcher)

    def test_beta_channel_selects_highest_consistent_beta_or_stable(self):
        bundles = {version: make_bundle(version) for version in ("3.3.1-beta.2", "3.3.1-beta.10", "3.3.1")}
        releases = [json.loads(release_payload(bundles[version], version, prerelease="-beta." in version)) for version in bundles]
        releases = [None, "invalid", 42, []] + releases
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(json.dumps(releases).encode())
            manager = UpdateManager(directory, "3.3.0", transport=transport)
            manager.set_channel("beta")
            manager.check_async()
            status = wait_for(manager)
        self.assertEqual(status["version"], "3.3.1")
        self.assertEqual(status["channel"], "beta")
        self.assertEqual(transport.requests[0][0], updater.ALL_RELEASES_URL)

    def test_stable_channel_rejects_beta_even_if_release_flag_is_wrong(self):
        bundle = make_bundle("3.3.1-beta.1")
        metadata = release_payload(bundle, "3.3.1-beta.1", prerelease=False)
        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.3.0", transport=FakeTransport(metadata))
            manager.check_async()
            self.assertEqual(wait_for(manager)["state"], "current")

    def test_channel_setting_preserves_auto_check_and_clears_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.3.0")
            manager.set_auto_check(False)
            manager.set_channel("beta")
            saved = json.loads((Path(directory) / "update_settings.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["channel"], "beta")
            self.assertIs(saved["auto_check"], False)
            self.assertEqual(UpdateManager(directory, "3.3.0").channel, "beta")

    def test_loaded_channel_is_reported_in_initial_status(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "update_settings.json").write_text(
                json.dumps({"schema": 1, "auto_check": False, "channel": "beta"}), encoding="utf-8")
            manager = UpdateManager(directory, "3.3.0")
            self.assertEqual(manager.poll()["channel"], "beta")

    def test_switching_channel_clears_beta_ready_and_busy_rejects_change(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.3.0")
            manager.set_channel("beta")
            manager._candidate = {"version": "3.3.1-beta.1"}
            manager._ready = {"version": "3.3.1-beta.1"}
            manager.set_channel("stable")
            self.assertIsNone(manager._candidate)
            self.assertIsNone(manager._ready)
            manager._thread = __import__("threading").Thread(target=lambda: time.sleep(0.2))
            manager._thread.start()
            with self.assertRaises(ValueError):
                manager.set_channel("beta")
            manager._thread.join()
            self.assertEqual(manager.channel, "stable")

    def test_default_https_open_retries_ssl_failure_with_verified_tls12(self):
        request = __import__("urllib.request").request.Request(updater.LATEST_RELEASE_URL)
        expected = FakeResponse(b"{}", updater.LATEST_RELEASE_URL)
        failure = ssl.SSLError(ssl.SSL_ERROR_SSL, "wrong version number")
        with patch("updater._open_with_context", side_effect=[failure, expected]) as mocked:
            self.assertIs(updater._default_open(request, 12), expected)
        self.assertEqual(mocked.call_count, 2)
        context = mocked.call_args_list[1].args[2]
        self.assertEqual(context.maximum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_certificate_failure_is_not_retried(self):
        request = __import__("urllib.request").request.Request(updater.LATEST_RELEASE_URL)
        failure = ssl.SSLCertVerificationError(ssl.SSL_ERROR_SSL, "certificate verify failed")
        with patch("updater._open_with_context", side_effect=failure) as mocked:
            with self.assertRaises(ssl.SSLCertVerificationError):
                updater._default_open(request, 12)
        self.assertEqual(mocked.call_count, 1)

    def test_new_stable_release_becomes_available(self):
        bundle = make_bundle()
        transport = FakeTransport(release_payload(bundle))
        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.2.0", transport=transport)
            self.assertTrue(manager.check_async())
            status = wait_for(manager)
        self.assertEqual(status["state"], "available")
        self.assertEqual(status["version"], "3.3.0")
        self.assertEqual(transport.requests[0][0], updater.LATEST_RELEASE_URL)
        self.assertNotIn("Authorization", transport.requests[0][2])

    def test_older_or_equal_release_is_current(self):
        for version in ("3.2.0", "3.1.9"):
            bundle = make_bundle(version)
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                manager = UpdateManager(directory, "3.2.0", transport=FakeTransport(release_payload(bundle, version)))
                manager.check_async()
                self.assertEqual(wait_for(manager)["state"], "current")

    def test_prerelease_and_draft_are_filtered(self):
        bundle = make_bundle()
        for field in ("prerelease", "draft"):
            metadata = release_payload(bundle, **{field: True})
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                manager = UpdateManager(directory, "3.2.0", transport=FakeTransport(metadata))
                manager.check_async()
                self.assertEqual(wait_for(manager)["state"], "current")

    def test_auto_check_setting_is_atomic_and_constructor_stays_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manager = UpdateManager(root / "missing", "3.2.0")
            self.assertTrue(manager.auto_check)
            self.assertFalse((root / "missing").exists())
            manager.set_auto_check(False)
            self.assertFalse(manager.auto_check)
            saved = json.loads((root / "missing/update_settings.json").read_text(encoding="utf-8"))
            self.assertIs(saved["auto_check"], False)
            self.assertFalse(UpdateManager(root / "missing", "3.2.0").auto_check)

    def test_normal_stage_activation_and_profile_backup(self):
        bundle = make_bundle()
        metadata = release_payload(bundle)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "progress.json").write_text('{"attempts": 8}', encoding="utf-8")
            (root / "learn_round-ielts.json").write_text('{"word": "safe"}', encoding="utf-8")
            database = sqlite3.connect(root / "learning.sqlite3")
            database.execute("CREATE TABLE cards(word TEXT)")
            database.execute("INSERT INTO cards VALUES ('retain')")
            database.commit()
            database.close()
            original = (root / "progress.json").read_bytes()
            manager = UpdateManager(root, "3.2.0", transport=FakeTransport(metadata, bundle))
            manager.check_async()
            self.assertEqual(wait_for(manager)["state"], "available")
            self.assertTrue(manager.install_async())
            self.assertEqual(wait_for(manager)["state"], "ready")
            launcher = manager.activate_ready()
            self.assertEqual(launcher, root / "updates/releases/3.3.0/launcher.py")
            self.assertEqual(manager.active_launcher(), launcher)
            self.assertEqual((root / "progress.json").read_bytes(), original)
            backups = list((root / "updates/backups").glob("before-3.3.0-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "progress.json").read_bytes(), original)
            self.assertTrue((backups[0] / "learn_round-ielts.json").is_file())
            copied = sqlite3.connect(backups[0] / "learning.sqlite3")
            self.assertEqual(copied.execute("SELECT * FROM cards").fetchall(), [("retain",)])
            copied.close()

    def test_checked_stage_is_recovered_after_restart_without_redownload(self):
        bundle = make_bundle()
        metadata = release_payload(bundle)
        with tempfile.TemporaryDirectory() as directory:
            first_transport = FakeTransport(metadata, bundle)
            first = UpdateManager(directory, "3.2.0", transport=first_transport)
            first.check_async()
            wait_for(first)
            first.install_async()
            self.assertEqual(wait_for(first)["state"], "ready")

            second_transport = FakeTransport(metadata)
            second = UpdateManager(directory, "3.2.0", transport=second_transport)
            second.check_async()
            status = wait_for(second)
            self.assertEqual(status["state"], "ready")
            self.assertEqual([request[0] for request in second_transport.requests], [updater.LATEST_RELEASE_URL])
            self.assertIsNotNone(second.activate_ready())

    def test_active_tree_must_match_authenticated_zip_even_if_self_consistent(self):
        bundle = make_bundle()
        metadata = release_payload(bundle)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manager = UpdateManager(root, "3.2.0", transport=FakeTransport(metadata, bundle))
            manager.check_async()
            wait_for(manager)
            manager.install_async()
            wait_for(manager)
            self.assertIsNotNone(manager.activate_ready())
            release = root / "updates/releases/3.3.0"
            launcher = release / "launcher.py"
            launcher.write_text("print('tampered but re-signed locally')\n", encoding="utf-8")
            manifest_path = release / "BUNDLE_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["launcher.py"] = hashlib.sha256(launcher.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            sums_path = release / "SHA256SUMS"
            names = sorted(path.relative_to(release).as_posix() for path in release.rglob("*")
                           if path.is_file() and path != sums_path)
            sums_path.write_text("".join("%s  %s\n" % (hashlib.sha256((release / name).read_bytes()).hexdigest(), name)
                                          for name in names), encoding="utf-8")
            manager._verify_release_dir(release, "3.3.0")
            self.assertIsNone(manager.active_launcher())

    def test_github_digest_mismatch_does_not_stage(self):
        bundle = make_bundle()
        release = json.loads(release_payload(bundle))
        release["assets"][0]["digest"] = "sha256:" + "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.2.0", transport=FakeTransport(json.dumps(release).encode(), bundle))
            manager.check_async()
            self.assertEqual(wait_for(manager)["state"], "available")
            manager.install_async()
            status = wait_for(manager)
            self.assertEqual(status["state"], "error")
            self.assertIn("SHA-256", status["message"])
            self.assertFalse((Path(directory) / "updates/releases/3.3.0").exists())

    def test_internal_checksum_mismatch_is_rejected(self):
        bundle = make_bundle(corrupt_internal=True)
        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.2.0", transport=FakeTransport(release_payload(bundle), bundle))
            manager.check_async()
            wait_for(manager)
            manager.install_async()
            self.assertEqual(wait_for(manager)["state"], "error")

    def test_path_traversal_is_rejected_without_writing_outside_stage(self):
        bundle = make_bundle(extra_entries=[("../escaped.txt", b"no")])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manager = UpdateManager(root, "3.2.0", transport=FakeTransport(release_payload(bundle), bundle))
            manager.check_async()
            wait_for(manager)
            manager.install_async()
            self.assertEqual(wait_for(manager)["state"], "error")
            self.assertFalse((root / "escaped.txt").exists())

    def test_symlink_member_is_rejected(self):
        base = make_bundle()
        source = zipfile.ZipFile(io.BytesIO(base))
        output = io.BytesIO()
        with source, zipfile.ZipFile(output, "w") as target:
            for info in source.infolist():
                target.writestr(info.filename, source.read(info))
            link = zipfile.ZipInfo("unsafe-link")
            link.create_system = 3
            link.external_attr = (0o120777 << 16)
            target.writestr(link, "launcher.py")
        bundle = output.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.2.0", transport=FakeTransport(release_payload(bundle), bundle))
            manager.check_async()
            wait_for(manager)
            manager.install_async()
            self.assertEqual(wait_for(manager)["state"], "error")

    def test_network_failure_is_non_blocking_error(self):
        def offline(request, timeout):
            raise urllib.error.URLError("no route")

        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.2.0", transport=offline)
            started = time.monotonic()
            self.assertTrue(manager.check_async())
            self.assertLess(time.monotonic() - started, 0.2)
            status = wait_for(manager)
            self.assertEqual(status["state"], "error")
            self.assertIn("GitHub", status["message"])

    def test_ssl_eof_while_reading_download_restarts_once(self):
        bundle = make_bundle()
        metadata = release_payload(bundle)
        calls = {"asset": 0}

        def flaky_transport(request, timeout):
            if request.full_url == updater.LATEST_RELEASE_URL:
                return FakeResponse(metadata, request.full_url)
            calls["asset"] += 1
            if calls["asset"] == 1:
                return SSLFailingResponse(b"", request.full_url)
            return FakeResponse(bundle, request.full_url)

        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.2.0", transport=flaky_transport)
            manager.check_async()
            wait_for(manager)
            manager.install_async()
            self.assertEqual(wait_for(manager)["state"], "ready")
            self.assertEqual(calls["asset"], 2)

    def test_backup_failure_preserves_old_active_pointer(self):
        bundle = make_bundle()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manager = UpdateManager(root, "3.2.0", transport=FakeTransport(release_payload(bundle), bundle))
            manager.check_async()
            wait_for(manager)
            manager.install_async()
            wait_for(manager)
            manager.active_path.parent.mkdir(parents=True, exist_ok=True)
            old = b'{"schema":1,"version":"3.2.1","digest":"' + b"1" * 64 + b'","activated_at":"old"}\n'
            manager.active_path.write_bytes(old)
            with patch.object(manager, "_backup_profile", side_effect=OSError("disk full")):
                self.assertIsNone(manager.activate_ready())
            self.assertEqual(manager.active_path.read_bytes(), old)
            self.assertEqual(manager.poll()["state"], "error")

    def test_active_pointer_cannot_supply_a_path_or_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manager = UpdateManager(root, "3.2.0")
            manager.active_path.parent.mkdir(parents=True)
            manager.active_path.write_text(json.dumps({
                "schema": 1, "version": "9.0.0", "digest": "0" * 64,
                "activated_at": "now", "launcher": "/tmp/evil.py",
            }), encoding="utf-8")
            self.assertIsNone(manager.active_launcher())
            manager.active_path.write_text(json.dumps({
                "schema": 1, "version": "3.2.0", "digest": "0" * 64, "activated_at": "now",
            }), encoding="utf-8")
            self.assertIsNone(manager.active_launcher())

    def test_close_returns_without_waiting_for_daemon_network_thread(self):
        gate = __import__("threading").Event()

        def blocked(request, timeout):
            gate.wait(1)
            raise urllib.error.URLError("closed")

        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(directory, "3.2.0", transport=blocked)
            manager.check_async()
            started = time.monotonic()
            manager.close()
            self.assertLess(time.monotonic() - started, 0.1)
            self.assertTrue(manager._thread.daemon)
            gate.set()


if __name__ == "__main__":
    unittest.main()
