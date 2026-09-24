"""Exercise the portable transfer workflow without a container daemon."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TransferTests(unittest.TestCase):
    def test_packaged_build_and_corruption_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            with tarfile.open(bundle / "nvidia-offline.tar.gz", "w:gz") as archive:
                data = b"fixture"
                entry = tarfile.TarInfo("nvidia-offline/fixture")
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
            for name, value in {
                "base-image.tar": "fixture image",
                "base-image.txt": "localhost/base:fixed\n",
                "base-image-id.txt": "abc123\n",
                "source-image.txt": "example/base:fixed\n",
                "Containerfile.verify": "fixture verification recipe\n",
            }.items():
                (bundle / name).write_text(value)
            (bundle / "TRANSFER.SHA256SUMS").write_text("".join(
                f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                for p in sorted(bundle.iterdir())))
            transfer = root / "transfer with spaces"
            result = subprocess.run(
                ["bash", str(ROOT / "package-transfer.sh"), str(bundle), str(transfer)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((transfer / "CUSTOM_BASE_AIRGAP.md").is_file())
            self.assertTrue((transfer / "build-custom.sh").is_file())
            engine = root / "docker"
            engine.write_text('''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$ENGINE_LOG"
case "$1" in
  load) ;;
  image) echo sha256:abc123 ;;
  build)
    [[ -f ${@: -1}/offline/nvidia-offline/fixture ]]
    [[ " $* " == *" --network=none "* ]]
    if [[ $ENGINE_NAME == podman ]]; then
      [[ " $* " == *" --pull=never "* ]]
      [[ " $* " == *" --security-opt label=disable "* ]]
    else
      [[ " $* " == *" --pull=false "* ]]
      [[ " $* " != *" --security-opt label=disable "* ]]
    fi ;;
  *) exit 90 ;;
esac
''')
            engine.chmod(0o755)
            log = root / "engine.log"
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}",
                       CONTAINER_ENGINE="docker", ENGINE_LOG=str(log),
                       ENGINE_NAME="docker")
            command = ["bash", str(transfer / "build.sh"), "localhost/test:fixed"]
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(log.read_text().splitlines()), 3)
            shutil.copy(engine, root / "podman")
            log.unlink()
            env.update(CONTAINER_ENGINE="podman", ENGINE_NAME="podman")
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(log.read_text().splitlines()), 3)
            log.unlink()
            (transfer / "offline/base-image.tar").write_text("corrupted")
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(log.exists(), "Corruption must fail before invoking the engine")

    def test_custom_builder_accepts_only_additive_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transfer = root / "transfer"
            offline = transfer / "offline"
            payload = root / "payload" / "nvidia-offline"
            offline.mkdir(parents=True)
            payload.mkdir(parents=True)
            shutil.copy(ROOT / "build-custom.sh", transfer)
            shutil.copy(ROOT / "Containerfile", transfer)
            (payload / "base-packages.txt").write_text("base-0:1.0-1.x86_64\n")
            (payload / "fixture").write_text("payload\n")
            (payload / "settings.sh").write_text(
                "DRIVER_VERSION=580.105.08\nTOOLKIT_VERSION=1.20.0-1\n")
            (payload / "SHA256SUMS").write_text("".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.name}\n"
                for path in sorted(payload.iterdir()) if path.name != "SHA256SUMS"))
            with tarfile.open(offline / "nvidia-offline.tar.gz", "w:gz") as archive:
                archive.add(payload, arcname="nvidia-offline")
            archive = offline / "nvidia-offline.tar.gz"
            base_image = offline / "base-image.tar"
            base_image.write_text("unused custom-build base image\n")
            (offline / "TRANSFER.SHA256SUMS").write_text("".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in (archive, base_image)))
            (transfer / "SHA256SUMS").write_text("".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.relative_to(transfer)}\n"
                for path in sorted(transfer.rglob("*")) if path.is_file()
                and path.name != "SHA256SUMS"))

            engine = root / "podman"
            engine.write_text('''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$ENGINE_LOG"
case "$1 $2" in
  "pull localhost/custom:fixed") ;;
  "image inspect")
    image=${@: -1}
    [[ $image != "$EXPECTED_OUTPUT" || ${OUTPUT_EXISTS:-0} == 1 || -f $ENGINE_BUILT ]] ;;
  "run --rm")
    if [[ $INVENTORY_MODE == additive ]]; then
      printf '%s\\n' base-0:1.0-1.x86_64 added-0:2.0-1.x86_64
    else
      printf '%s\\n' base-0:1.1-1.x86_64 added-0:2.0-1.x86_64
    fi ;;
  "build --pull=never")
    [[ " $* " == *" --network=none "* ]]
    [[ " $* " == *" --security-opt label=disable "* ]]
    [[ " $* " == *" --label nvidia.drivers_version=580.105.08 "* ]]
    [[ " $* " == *" --label nvidia.containers_toolkit_version=1.20.0-1 "* ]]
    context=${@: -1}
    grep -qx added-0:2.0-1.x86_64 "$context/offline/nvidia-offline/base-packages.txt"
    (cd "$context/offline/nvidia-offline" && sha256sum --check --strict SHA256SUMS >/dev/null)
    touch "$ENGINE_BUILT" ;;
  *) echo "Unexpected command: $*" >&2; exit 90 ;;
esac
''')
            engine.chmod(0o755)
            log = root / "engine.log"
            built = root / "built"
            context = root / "selected context"
            output = "localhost/output:fixed"
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}",
                       CUSTOM_BASE_IMAGE="localhost/custom:fixed", OUTPUT_IMAGE=output,
                       CONTAINER_ENGINE="podman", PULL_CUSTOM_BASE="1",
                       BUILD_CONTEXT=str(context),
                       ENGINE_LOG=str(log), ENGINE_BUILT=str(built),
                       EXPECTED_OUTPUT=output, INVENTORY_MODE="additive")
            command = ["bash", str(transfer / "build-custom.sh")]
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(built.exists())
            self.assertFalse(context.exists())
            self.assertIn("pull localhost/custom:fixed", log.read_text())

            log.unlink()
            built.unlink()
            base_image.unlink()
            env.update(OUTPUT_IMAGE="localhost/output:existing",
                       EXPECTED_OUTPUT="localhost/output:existing",
                       BUILD_CONTEXT=str(root / "existing context"),
                       PULL_CUSTOM_BASE="0", ALLOW_OUTPUT_IMAGE_OVERWRITE="0",
                       OUTPUT_EXISTS="1")
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("OUTPUT_IMAGE already exists", result.stderr)
            self.assertFalse(built.exists())

            log.unlink()
            env.update(OUTPUT_IMAGE="localhost/output:remote",
                       EXPECTED_OUTPUT="localhost/output:remote",
                       BUILD_CONTEXT=str(root / "remote context"),
                       PULL_CUSTOM_BASE="1", ALLOW_OUTPUT_IMAGE_OVERWRITE="1",
                       OUTPUT_EXISTS="1")
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(built.exists())
            self.assertIn("image inspect localhost/custom:fixed", log.read_text())

            log.unlink()
            built.unlink()
            env.update(OUTPUT_IMAGE="localhost/output:changed",
                       EXPECTED_OUTPUT="localhost/output:changed",
                       BUILD_CONTEXT=str(root / "rejected context"),
                       INVENTORY_MODE="changed", ALLOW_OUTPUT_IMAGE_OVERWRITE="0",
                       OUTPUT_EXISTS="0")
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Only additive RPM changes are allowed", result.stderr)
            self.assertFalse(built.exists())
            self.assertNotIn("build --pull=never", log.read_text())


if __name__ == "__main__":
    unittest.main()
