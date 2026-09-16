from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from app.services import app_updates as updates
from scripts.build_exe import write_update_metadata


PAYLOAD = b"MZ" + b"test-installer-never-executed" * 10
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def release(version="0.1.0-beta.10", **changes):
    name = f"Sentry_Setup_{version}.exe"
    result = {
        "tag_name": "v" + version, "name": "Sentry " + version,
        "draft": False, "prerelease": "-" in version, "body": "Cambios de prueba",
        "assets": [{"name": name, "size": len(PAYLOAD), "digest": "sha256:" + DIGEST,
                    "browser_download_url": f"https://github.com/{updates.REPOSITORY}/releases/download/v{version}/{name}"}],
    }
    result.update(changes)
    return result


def release_info(version="0.1.0-beta.10"):
    return updates._release_info(release(version))


class Response(io.BytesIO):
    def __init__(self, payload=PAYLOAD, headers=None):
        super().__init__(payload)
        self.headers = {} if headers is None else headers


class UpdateVersionTests(unittest.TestCase):
    def test_semver_order_and_no_downgrades(self):
        versions = ["0.1.0-alpha.1", "0.1.0-beta.2", "0.1.0-beta.10", "0.1.0-rc.1", "0.1.0", "0.1.1"]
        parsed = [updates.Version.parse(value) for value in versions]
        self.assertEqual(sorted(reversed(parsed)), parsed)
        self.assertEqual(updates.Version.parse("v1.2.3+build.8"), updates.Version.parse("1.2.3+build.9"))
        for invalid in ("1.2", "1.02.3", "1.2.3-beta.01", "../../1.2.3", "1.2.3.exe"):
            with self.subTest(invalid=invalid), self.assertRaises(updates.UpdateError):
                updates.Version.parse(invalid)

    @patch.object(updates, "_read_json")
    def test_numeric_beta_sort_not_release_creation_order(self, read):
        read.return_value = [release("0.1.0-beta.9"), release("0.1.0-beta.10"), release("0.1.0-beta.6")]
        self.assertEqual(updates.check_for_update("0.1.0-beta.5").version, "0.1.0-beta.10")

    @patch.object(updates, "_read_json")
    def test_stable_ignores_prereleases_and_drafts(self, read):
        read.return_value = [release("1.2.0-beta.1"), release("1.1.0"), release("2.0.0", draft=True)]
        self.assertEqual(updates.check_for_update("1.0.0").version, "1.1.0")
        self.assertEqual(updates.check_for_update("1.0.0", include_prereleases=True).version, "1.2.0-beta.1")

    @patch.object(updates, "_read_json")
    def test_equal_and_older_versions_not_installed(self, read):
        read.return_value = [release("0.1.0-beta.4"), release("0.1.0-beta.5")]
        self.assertIsNone(updates.check_for_update("0.1.0-beta.5"))

    @patch.object(updates, "_read_json")
    def test_pagination_and_invalid_tags(self, read):
        read.side_effect = [[{"tag_name": "invalid"}] * 100, [release("1.1.0")]]
        self.assertEqual(updates.check_for_update("1.0.0").version, "1.1.0")
        self.assertIn("page=2", read.call_args.args[0])

    def test_missing_installers_and_hash_fail_closed(self):
        candidate = release()
        candidate["assets"][0]["digest"] = None
        with self.assertRaisesRegex(updates.UpdateError, "huella SHA-256"):
            updates._release_info(candidate)
        with self.assertRaisesRegex(updates.UpdateError, "todavía no tiene"):
            updates._release_info(release(assets=[]))

    @patch.object(updates, "_read_json")
    def test_manifest_fallback_matches_exact_version_and_asset(self, read):
        candidate = release()
        candidate["assets"][0]["digest"] = None
        candidate["assets"].append({"name": updates.MANIFEST_NAME, "browser_download_url":
            f"https://github.com/{updates.REPOSITORY}/releases/download/v0.1.0-beta.10/{updates.MANIFEST_NAME}"})
        read.return_value = {"schema": 1, "version": "0.1.0-beta.10",
                             "asset": {"name": "Sentry_Setup_0.1.0-beta.10.exe", "size": len(PAYLOAD), "sha256": DIGEST}}
        self.assertEqual(updates._release_info(candidate).sha256, DIGEST)
        read.return_value["version"] = "0.1.0-beta.11"
        with self.assertRaisesRegex(updates.UpdateError, "manifiesto no corresponde"):
            updates._release_info(candidate)

    @patch.object(updates, "_read_json")
    def test_manifest_disagrees_with_github_digest(self, read):
        candidate = release()
        candidate["assets"].append({"name": updates.MANIFEST_NAME, "browser_download_url":
            f"https://github.com/{updates.REPOSITORY}/releases/download/v0.1.0-beta.10/{updates.MANIFEST_NAME}"})
        read.return_value = {"schema": 1, "version": "0.1.0-beta.10",
                             "asset": {"name": "Sentry_Setup_0.1.0-beta.10.exe", "size": len(PAYLOAD), "sha256": "0" * 64}}
        with self.assertRaisesRegex(updates.UpdateError, "no coincide"):
            updates._release_info(candidate)

    def test_asset_origin_path_size_and_exact_name(self):
        for url in ("https://evil.example/setup.exe", "https://github.com/another/repo/releases/download/v0.1.0-beta.10/setup.exe"):
            candidate = release()
            candidate["assets"][0]["browser_download_url"] = url
            with self.subTest(url=url), self.assertRaises(updates.UpdateError):
                updates._release_info(candidate)
        for size in (0, -1, True, "10", updates.MAX_INSTALLER_BYTES + 1):
            candidate = release()
            candidate["assets"][0]["size"] = size
            with self.subTest(size=size), self.assertRaises(updates.UpdateError):
                updates._release_info(candidate)


class UpdateNetworkTests(unittest.TestCase):
    def test_redirect_rejects_plain_http_external_userinfo_and_ports(self):
        handler = updates._SafeRedirectHandler()
        request = Request("https://github.com/test")
        for url in ("http://github.com/test", "https://github.com.evil.example/", "https://github.com:444/",
                    "https://user:password@github.com/", "file:///tmp/test", "https://evil.example/"):
            with self.subTest(url=url), self.assertRaises(updates.UpdateError):
                handler.redirect_request(request, None, 302, "", {}, url)
        redirected = handler.redirect_request(request, None, 302, "", {}, "https://release-assets.githubusercontent.com/test?sig=private")
        self.assertEqual(redirected.host, "release-assets.githubusercontent.com")

    @patch.object(updates, "build_opener")
    def test_requests_have_no_authorization_or_local_secrets(self, opener):
        opener.return_value.open.return_value = Response(b"[]")
        self.assertEqual(updates._read_json(updates.RELEASES_URL), [])
        request = opener.return_value.open.call_args.args[0]
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(request.get_header("User-agent"), "Sentry-Updater")

    @patch.object(updates, "build_opener")
    def test_private_repository_is_explicit_without_leaking_url(self, opener):
        opener.return_value.open.side_effect = HTTPError("https://test?secret", 404, "", {}, None)
        with self.assertRaisesRegex(updates.UpdateError, "repositorio público") as caught:
            updates._read_json(updates.RELEASES_URL)
        self.assertNotIn("secret", str(caught.exception))

    @patch.object(updates, "_open_url")
    def test_json_limit_and_invalid_content(self, opened):
        opened.return_value = Response(b"not json")
        with self.assertRaises(updates.UpdateError):
            updates._read_json(updates.RELEASES_URL)
        opened.return_value = Response(b"[]" * 20)
        with patch.object(updates, "MAX_JSON_BYTES", 16), self.assertRaisesRegex(updates.UpdateError, "tamaño"):
            updates._read_json(updates.RELEASES_URL)


class UpdateDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = release_info()

    @patch.object(updates, "_open_url")
    def test_atomic_verified_download_and_cache_reuse(self, opened):
        opened.return_value = Response(headers={"Content-Length": str(len(PAYLOAD))})
        progress = []
        path = updates.download_update(self.release, self.root, progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertEqual(progress[-1], (len(PAYLOAD), len(PAYLOAD)))
        self.assertEqual(updates.download_update(self.release, self.root), path)
        self.assertEqual(opened.call_count, 1)
        self.assertFalse(list(self.root.glob("*.part")))

    @patch.object(updates, "_open_url")
    def test_corrupt_download_never_replaces_existing(self, opened):
        existing = self.root / self.release.asset_name
        existing.write_bytes(b"previous-file")
        opened.return_value = Response(b"MZ" + b"x" * (len(PAYLOAD) - 2))
        with self.assertRaisesRegex(updates.UpdateError, "SHA-256"):
            updates.download_update(self.release, self.root)
        self.assertEqual(existing.read_bytes(), b"previous-file")
        self.assertFalse(list(self.root.glob("*.part")))

    @patch.object(updates, "_open_url")
    def test_cancel_cleans_partial_file(self, opened):
        opened.return_value = Response()
        cancelled = False
        def progress(done, total):
            nonlocal cancelled
            cancelled = done > 0
        with self.assertRaises(updates.UpdateCancelled):
            updates.download_update(self.release, self.root, progress=progress, cancel=lambda: cancelled)
        self.assertEqual(list(self.root.iterdir()), [])

    @patch.object(updates, "_open_url")
    def test_short_oversized_and_wrong_length_downloads(self, opened):
        for payload, headers in ((PAYLOAD[:-1], {}), (PAYLOAD + b"x", {}), (PAYLOAD, {"Content-Length": "1"})):
            opened.return_value = Response(payload, headers)
            with self.subTest(size=len(payload)), self.assertRaises(updates.UpdateError):
                updates.download_update(self.release, self.root)
            self.assertEqual(list(self.root.iterdir()), [])

    @patch.object(updates.shutil, "disk_usage", return_value=SimpleNamespace(free=1))
    @patch.object(updates, "_open_url")
    def test_not_enough_space_does_not_start_download(self, opened, _usage):
        with self.assertRaisesRegex(updates.UpdateError, "espacio"):
            updates.download_update(self.release, self.root)
        opened.assert_not_called()

    @patch.object(updates.subprocess, "Popen")
    def test_launch_reverifies_and_uses_no_shell_or_forced_closing(self, popen):
        path = self.root / self.release.asset_name
        path.write_bytes(PAYLOAD)
        (self.root / "Sentry.exe").write_bytes(b"fake-existing-app")
        updates.launch_installer(path, self.release, self.root, self.root / "install.log", parent_pid=123,
                                 current_version="0.1.0-beta.4")
        args = popen.call_args.args[0]
        self.assertIn("/UPDATEFROMPID=123", args)
        self.assertIn("/SENTRYUPDATE=1", args)
        self.assertIn("/NOCLOSEAPPLICATIONS", args)
        self.assertNotIn("/FORCECLOSEAPPLICATIONS", args)
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(popen.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        path.write_bytes(b"MZ" + b"x" * (len(PAYLOAD) - 2))
        popen.reset_mock()
        with self.assertRaisesRegex(updates.UpdateError, "SHA-256"):
            updates.launch_installer(path, self.release, self.root, self.root / "install.log", parent_pid=123,
                                     current_version="0.1.0-beta.4")
        popen.assert_not_called()

    def test_crafted_release_filename_cannot_traverse_cache(self):
        with self.assertRaises(updates.UpdateError):
            updates.download_update(replace(self.release, asset_name="../outside.exe"), self.root)

    @patch.object(updates.subprocess, "Popen")
    def test_launch_rejects_downgrades_even_after_download(self, popen):
        with self.assertRaisesRegex(updates.UpdateError, "más reciente"):
            updates.launch_installer(self.root / self.release.asset_name, self.release,
                                     self.root, self.root / "install.log", current_version="1.0.0")
        popen.assert_not_called()

    def test_windows_executable_header_required_even_with_valid_hash(self):
        path = self.root / self.release.asset_name
        payload = b"<html>not-an-executable</html>"
        path.write_bytes(payload)
        candidate = replace(self.release, sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
        with self.assertRaisesRegex(updates.UpdateError, "Windows"):
            updates.verify_installer(path, candidate)

    def test_build_manifest_is_compatible_and_has_no_client_data(self):
        installer = self.root / self.release.asset_name
        installer.write_bytes(PAYLOAD)
        manifest, checksum = write_update_metadata(installer, self.release.version)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data, {"schema": 1, "version": self.release.version,
                               "asset": {"name": installer.name, "size": len(PAYLOAD), "sha256": DIGEST}})
        self.assertEqual(checksum.read_text(encoding="utf-8"), f"{DIGEST}  {installer.name}\n")
        self.assertFalse(list(self.root.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
