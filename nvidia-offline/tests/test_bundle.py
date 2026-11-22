"""Host-independent input, integrity, and publication-failure regressions."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("toolkit_repo", ROOT / "toolkit-repo.py")
TOOLKIT_REPO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLKIT_REPO)


def run(*args, **kwargs):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kwargs)


class BundleTests(unittest.TestCase):
    def test_signed_metadata_paths_cannot_escape_repository(self):
        for relative in ("../package.rpm", "/tmp/package.rpm", "https://elsewhere/package.rpm"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                TOOLKIT_REPO.checked_path(Path("/bundle/toolkit-repo"), relative)

    def test_metadata_digest_rejects_changed_rpm_and_weak_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            rpm = Path(directory) / "package.rpm"
            rpm.write_bytes(b"original RPM content")
            checksum = hashlib.sha256(rpm.read_bytes()).hexdigest()
            TOOLKIT_REPO.check_digest(rpm, "sha256", checksum)
            rpm.write_bytes(b"changed RPM content")
            with self.assertRaises(ValueError):
                TOOLKIT_REPO.check_digest(rpm, "sha256", checksum)
            with self.assertRaises(ValueError):
                TOOLKIT_REPO.check_digest(rpm, "md5", "unused")

    def validate(self, **overrides):
        env = dict(os.environ, COMMON=str(ROOT / "common.sh"),
                   BASE_IMAGE="registry.redhat.io/rhel9/rhel-bootc:latest",
                   DRIVER_MODE="open-dkms", DRIVER_VERSION="580.95.05",
                   TOOLKIT_VERSION="1.18.0-1", SAVE_BASE_IMAGE="1")
        env.update(overrides)
        return run("bash", "-eu", "-c",
                   'source "$COMMON"; EXTRA_PACKAGES=(nginx python3); validate_config',
                   env=env)

    def test_shell_syntax(self):
        for path in ROOT.glob("*.sh"):
            with self.subTest(path=path.name):
                result = run("bash", "-n", str(path))
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_versions_accepted(self):
        for version in ("1.12.0-1", "1.18.0-1", "2.0.0-1"):
            result = self.validate(TOOLKIT_VERSION=version)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_wildcard_and_unsupported_versions_rejected(self):
        for overrides in (
            {"DRIVER_VERSION": ""}, {"DRIVER_VERSION": "580.*"},
            {"TOOLKIT_VERSION": "1.18.0"}, {"TOOLKIT_VERSION": "1.11.0-1"},
            {"TOOLKIT_VERSION": "latest"}, {"DRIVER_MODE": "auto"},
            {"DRIVER_MODE": "precompiled", "KMOD_NEVRA": "kmod-nvidia*"},
            {"SAVE_BASE_IMAGE": "yes"}, {"CONTAINER_ENGINE": "unknown"},
        ):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.validate(**overrides).returncode, 0)

    def test_precompiled_requires_concrete_rpm(self):
        result = self.validate(
            DRIVER_MODE="precompiled",
            KMOD_NEVRA="kmod-nvidia-580.95.05-5.14.0-570.el9.x86_64-3:580.95.05-1.el9.x86_64")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_profiles_select_driver_stream_and_exact_kernel_build_dependencies(self):
        profiles = (
            (ROOT / "config.example.sh", "580-open", "kmod-nvidia-open-dkms", ()),
            (ROOT / "config.v100.example.sh", "580-dkms", "kmod-nvidia-latest-dkms", ()),
            (ROOT.parent / "ci/nvidia-vm.config.sh", "580-open", "kmod-nvidia-open-dkms",
             ("podman", "cloud-init", "qemu-guest-agent")),
            (ROOT.parent / "ci/nvidia-v100-vm.config.sh", "580-dkms", "kmod-nvidia-latest-dkms",
             ("podman", "cloud-init", "qemu-guest-agent")),
        )
        for profile, stream, kmod, extras in profiles:
            with self.subTest(profile=profile.name), tempfile.TemporaryDirectory() as directory:
                tmp = Path(directory)
                tools = tmp / "tools"
                bundle = tmp / "bundle"
                tools.mkdir()
                bundle.mkdir()
                shutil.copy(ROOT / "common.sh", tools)
                shutil.copy(profile, tools / "config.sh")
                kernel = "5.14.0-503.99.1.el9_5.x86_64"
                (bundle / "kernel.txt").write_text(kernel + "\n")
                (bundle / "target-os-release").write_text("VERSION_ID=9.5\n")
                # Relocate the downloader's container paths; execute its real
                # package selection but stop at DNF's dependency resolution.
                script = (ROOT / "download.sh").read_text()
                script = script.replace("/offline-tools/", f"{tools}/")
                script = script.replace("/bundle/", f"{bundle}/")
                downloader = tmp / "download.sh"
                downloader.write_text(script)
                dnf = tmp / "dnf"
                dnf.write_text('''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$DNF_LOG"
if [[ " $* " == *" download --resolve "* ]]; then exit 42; fi
''')
                dnf.chmod(0o755)
                log = tmp / "dnf.log"
                result = run("bash", str(downloader), env=dict(
                    os.environ, PATH=f"{tmp}:{os.environ['PATH']}", DNF_LOG=str(log)))
                self.assertEqual(result.returncode, 42, result.stderr)
                commands = log.read_text().splitlines()
                self.assertEqual(commands[0],
                                 f"--releasever=9.5 -y module enable nvidia-driver:{stream}")
                packages = (bundle / "requested-packages.txt").read_text().splitlines()
                self.assertIn(f"{kmod}-580.105.08", packages)
                self.assertEqual([p for p in packages if p.startswith("kmod-nvidia")],
                                 [f"{kmod}-580.105.08"])
                self.assertIn("nvidia-driver-cuda-580.105.08", packages)
                self.assertIn("nvidia-container-toolkit-1.20.0-1", packages)
                for package in (f"kernel-devel-{kernel}", f"kernel-devel-matched-{kernel}",
                                f"kernel-headers-{kernel}", "dkms", "gcc", "make",
                                "elfutils-libelf-devel", *extras):
                    self.assertIn(package, packages)
                if not extras:
                    self.assertNotIn("cloud-init", packages)
                self.assertIn("--alldeps", commands[1])
                for package in packages:
                    self.assertIn(package, commands[1].split())

    def test_empty_config_fails_before_podman(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.sh"
            config.write_text("BASE_IMAGE=example/base:fixed\nDRIVER_MODE=open-dkms\nDRIVER_VERSION=''\n")
            result = run("bash", str(ROOT / "bundle.sh"), "prepare",
                         str(config), str(Path(directory) / "output"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set an exact DRIVER_VERSION", result.stderr)

    def test_checksum_verification_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            shutil.copy(ROOT / "install.sh", bundle)
            payload = bundle / "payload.rpm"
            payload.write_bytes(b"test payload, not a real RPM")
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            (bundle / "SHA256SUMS").write_text(f"{digest}  payload.rpm\n")
            command = ("bash", str(bundle / "install.sh"), "--verify-only")
            self.assertEqual(run(*command).returncode, 0)
            payload.write_bytes(b"corrupted transfer")
            self.assertNotEqual(run(*command).returncode, 0)
            payload.unlink()
            self.assertNotEqual(run(*command).returncode, 0)

    def test_failed_offline_build_does_not_publish_archive(self):
        self.check_failed_offline_build("podman")

    def test_docker_uses_registered_image_only_for_downloads(self):
        self.check_failed_offline_build("docker")

    def check_failed_offline_build(self, engine):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            fake = tmp / engine
            fake.write_text('''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$ENGINE_LOG"
case "$1" in
  image)
    if [[ $2 == inspect ]]; then printf '%064d\\n' 123; fi ;;
  tag|rm) ;;
  run) echo base-rpm-0:1.0-1.x86_64 ;;
  build)
    for arg in "$@"; do
      if [[ $arg == --network=none ]]; then
        echo 'simulated offline dependency failure' >&2
        exit 42
      fi
    done
    while [[ $# -gt 0 ]]; do
      if [[ $1 == --iidfile ]]; then echo downloader-id > "$2"; break; fi
      shift
    done ;;
  create) echo temporary-container ;;
  cp) echo fixture > "$3/fixture" ;;
  *) echo "Unexpected command: $*" >&2; exit 1 ;;
esac
''')
            fake.chmod(0o755)
            config = tmp / "config.sh"
            config.write_text("BASE_IMAGE='example/base:fixed'\n"
                              f"CONTAINER_ENGINE={engine}\n"
                              "DOWNLOAD_IMAGE='example/base:registered'\n"
                              "DRIVER_MODE=open-dkms\nDRIVER_VERSION=580.95.05\n"
                              "TOOLKIT_VERSION=1.18.0-1\nEXTRA_PACKAGES=()\n")
            output = tmp / "result"
            result = run("bash", str(ROOT / "bundle.sh"), "prepare", str(config), str(output),
                         env=dict(os.environ, PATH=f"{tmp}:{os.environ['PATH']}", ENGINE_LOG=str(tmp / "commands.log")))
            self.assertEqual(result.returncode, 42, result.stderr)
            self.assertTrue((output / "nvidia-offline/fixture").exists())
            self.assertFalse((output / "nvidia-offline.tar.gz").exists())
            self.assertFalse((output / "TRANSFER.SHA256SUMS").exists())
            commands = (tmp / "commands.log").read_text().splitlines()
            builds = [line for line in commands if line.startswith("build ")]
            self.assertEqual(len(builds), 2)
            self.assertIn("BASE_IMAGE=example/base:registered", builds[0])
            self.assertIn("BASE_IMAGE=localhost/nvidia-offline-base:", builds[1])
            self.assertNotIn("registered", builds[1])
            self.assertIn("--network=none", builds[1])
            self.assertIn("--pull=false" if engine == "docker" else "--pull=never", builds[1])


if __name__ == "__main__":
    unittest.main()
