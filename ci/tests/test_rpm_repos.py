import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import urlopen

CI = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rpm_repos", CI / "rpm-repos.py")
repos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repos)


class RPMRepoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="bootc-rpm-tests-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        env = patch.dict(os.environ, {"PATH": os.environ["PATH"],
                                     "RPM_REPO_S3_URI": "s3://rpms/snapshot/",
                                     "S3_ENDPOINT_URL": "https://s3.internal"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.snapshot = self.root / "source"
        for name in ("BaseOS", "AppStream"):
            repo = self.snapshot / name
            (repo / "repodata").mkdir(parents=True)
            (repo / "repodata/repomd.xml").write_bytes(b"test metadata")
            (repo / "package.rpm").write_bytes(b"test RPM bytes")

    def fake_download(self, args):
        self.assertEqual(args[:6], ["aws", "--endpoint-url", "https://s3.internal", "s3", "sync", "s3://rpms/snapshot/"])
        shutil.copytree(self.snapshot, args[6], dirs_exist_ok=True)
        return subprocess.CompletedProcess(args, 0)

    def test_http_repo_config_keeps_signature_and_tls_checks(self):
        with patch.dict(os.environ, {"RPM_REPO_BASEURL": "https://s3.internal/rpms/snapshot/"}):
            text = repos.repo_config()
        self.assertIn("baseurl=https://s3.internal/rpms/snapshot/BaseOS/", text)
        self.assertIn("baseurl=https://s3.internal/rpms/snapshot/AppStream/", text)
        self.assertEqual(text.count("gpgcheck=1"), 2)
        self.assertEqual(text.count("sslverify=1"), 2)
        self.assertNotIn("s3://", text)

    def test_credentials_and_injected_repo_settings_are_rejected(self):
        for url in ("https://user:password@s3.internal/rpms", "https://s3.internal/rpms?token=secret",
                    "https://s3.internal/rpms\ngpgcheck=0", "s3://bucket/rpms"):
            with self.subTest(url=url), patch.dict(os.environ, {"RPM_REPO_BASEURL": url}):
                with self.assertRaises(ValueError):
                    repos.repo_config()

    def test_private_snapshot_is_served_and_cleaned_on_child_failure(self):
        probe = self.root / "probe.py"
        probe.write_text('''import os, pathlib, sys
from urllib.request import urlopen
url = os.environ["RPM_REPO_BASEURL"]
assert os.environ["BOOTC_RPM_REPOS_READY"] == "1"
assert urlopen(url + "/BaseOS/repodata/repomd.xml", timeout=5).read() == b"test metadata"
assert urlopen(url + "/AppStream/package.rpm", timeout=5).read() == b"test RPM bytes"
pathlib.Path("served-url").write_text(url)
sys.exit(17)
''')
        with patch.object(repos.subprocess, "run", side_effect=self.fake_download):
            result = repos.run_with_s3([sys.executable, str(probe)])
        self.assertEqual(result, 17)
        self.assertEqual(list((self.root / "build").iterdir()), [])
        with self.assertRaises(OSError):
            urlopen((self.root / "served-url").read_text(), timeout=1)

    def test_s3_failure_does_not_start_build(self):
        with patch.object(repos.subprocess, "run", return_value=subprocess.CompletedProcess([], 45)), \
                patch.object(repos.subprocess, "Popen") as build:
            self.assertEqual(repos.run_with_s3(["unused"]), 45)
            build.assert_not_called()
        self.assertEqual(list((self.root / "build").iterdir()), [])

    def test_missing_metadata_does_not_start_build(self):
        (self.snapshot / "BaseOS/repodata/repomd.xml").unlink()
        with patch.object(repos.subprocess, "run", side_effect=self.fake_download), \
                patch.object(repos.subprocess, "Popen") as build:
            with self.assertRaisesRegex(ValueError, "BaseOS/repodata/repomd.xml"):
                repos.run_with_s3(["unused"])
            build.assert_not_called()

    def test_conflicting_sources_or_network_fail_before_s3_access(self):
        for extra in ({"RPM_REPO_BASEURL": "https://s3.internal/rpms"}, {"BIB_NETWORK": "none"}):
            with self.subTest(extra=extra), patch.dict(os.environ, extra), \
                    patch.object(repos.subprocess, "run") as download:
                with self.assertRaises(ValueError):
                    repos.run_with_s3(["unused"])
                download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
