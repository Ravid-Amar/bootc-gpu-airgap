import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render", CI / "render.py")
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        directory = Path(self.tmp.name)
        self.auth = {"auths": {"registry.internal": {"auth": base64.b64encode(b'robot:password-$()-%-"').decode()}}}
        self.config = b'endpoint: "https://internal/a?x=$HOME"\nmessage: "100% ready"\n'
        (directory / "auth.json").write_text(json.dumps(self.auth))
        (directory / "config.yaml").write_bytes(self.config)
        (directory / "key.pub").write_text("ssh-ed25519 AAAATEST fixture\n")
        self.env = {
            "VM_NAME": "test-vm", "VM_NAMESPACE": "test-vms",
            "CONTAINERDISK_REF": "registry.internal/team/disk@sha256:" + "a" * 64,
            "WORKLOAD_IMAGE": "registry.internal/team/workload:v1",
            "BASELINE_IMAGE": "registry.internal/team/baseline:v1",
            "WORKLOAD_AUTH_FILE": str(directory / "auth.json"),
            "WORKLOAD_CONFIG_FILE": str(directory / "config.yaml"),
            "VM_SSH_KEY_FILE": str(directory / "key.pub"),
        }
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_secret_roundtrip_preserves_bytes_and_permissions(self):
        secret = render.cloudinit_secret()
        userdata = base64.b64decode(secret["data"]["userdata"]).decode()
        self.assertTrue(userdata.startswith("#cloud-config\n"))
        config = json.loads(userdata.split("\n", 1)[1])
        files = {item["path"]: item for item in config["write_files"]}
        auth_file = files["/etc/containers/registry-auth.json"]
        self.assertEqual(json.loads(base64.b64decode(auth_file["content"])), self.auth)
        self.assertEqual(auth_file["permissions"], "0600")
        self.assertEqual(base64.b64decode(files["/etc/workload/config.yaml"]["content"]), self.config)
        self.assertIn(["systemctl", "start", "--no-block", "baseline.service", "workload.service"], config["runcmd"])
        self.assertEqual(secret["metadata"]["name"], "test-vm-cloudinit")

    def test_vm_has_persistent_disk_and_only_secret_reference(self):
        vm = render.vm_manifest()
        spec = vm["spec"]
        root = spec["dataVolumeTemplates"][0]
        self.assertEqual(root["spec"]["source"]["registry"]["url"], "docker://" + self.env["CONTAINERDISK_REF"])
        volumes = spec["template"]["spec"]["volumes"]
        self.assertEqual(volumes[0]["dataVolume"]["name"], root["metadata"]["name"])
        self.assertEqual(volumes[1]["cloudInitNoCloud"]["secretRef"]["name"], "test-vm-cloudinit")
        text = json.dumps(vm)
        self.assertNotIn("userdata", text)
        self.assertNotIn(self.auth["auths"]["registry.internal"]["auth"], text)

    def test_template_parameter_processing_matches_concrete_vm(self):
        for gpu, storage in (("", ""), ("nvidia.com/TU104GL_Tesla_T4", "fast-storage")):
            with self.subTest(gpu=gpu), patch.dict(os.environ, {"GPU_RESOURCE_NAME": gpu, "VM_STORAGE_CLASS": storage}):
                template = render.openshift_template()
                parameters = {p["name"]: p["value"] for p in template["parameters"]}
                text = json.dumps(template["objects"][0])
                for key, val in parameters.items():
                    text = text.replace('"${{' + key + '}}"', val)
                    text = text.replace("${" + key + "}", val)
                self.assertNotRegex(text, r"\$\{")
                self.assertEqual(json.loads(text), render.vm_manifest())

    def test_gpu_is_consistent_between_vm_and_quadlet(self):
        self.assertNotIn("gpus", render.vm_manifest()["spec"]["template"]["spec"]["domain"]["devices"])
        self.assertNotIn("AddDevice", render.workload())
        with patch.dict(os.environ, {"GPU_RESOURCE_NAME": "nvidia.com/TU104GL_Tesla_T4"}):
            self.assertEqual(render.vm_manifest()["spec"]["template"]["spec"]["domain"]["devices"]["gpus"][0]["deviceName"], "nvidia.com/TU104GL_Tesla_T4")
            self.assertIn("AddDevice=nvidia.com/gpu=all", render.workload())
            self.assertIn("After=nvidia-cdi-refresh.service", render.workload())

    def test_invalid_image_and_name_cannot_inject_unit_or_manifest(self):
        for name, val, function in (
            ("WORKLOAD_IMAGE", "internal/image\nExec=bad", render.workload),
            ("BASELINE_IMAGE", "internal/image%name", render.baseline),
            ("VM_NAME", "../bad", render.vm_manifest),
            ("CONTAINERDISK_REF", "registry.internal/disk:latest", render.vm_manifest),
        ):
            with self.subTest(name=name), patch.dict(os.environ, {name: val}):
                with self.assertRaises(ValueError):
                    function()

    def test_credential_helpers_without_inline_auth_are_rejected(self):
        Path(self.env["WORKLOAD_AUTH_FILE"]).write_text('{"auths":{},"credsStore":"secretservice"}')
        with self.assertRaises(ValueError):
            render.cloudinit_secret()

    def test_secret_cli_refuses_artifact_output(self):
        path = Path(self.tmp.name) / "secret.json"
        result = subprocess.run(["python3", str(CI / "render.py"), "cloudinit-secret", "--output", str(path)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(path.exists())
        self.assertEqual(result.stdout, b"")

    def test_render_errors_do_not_echo_secret_input(self):
        Path(self.env["WORKLOAD_AUTH_FILE"]).write_text("TOP_SECRET_INVALID_JSON")
        result = subprocess.run(["python3", str(CI / "render.py"), "cloudinit-secret"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"TOP_SECRET", result.stdout + result.stderr)

    def test_quadlets_generate_real_systemd_services(self):
        generator = Path("/usr/libexec/podman/quadlet")
        if not generator.exists():
            self.skipTest("Podman Quadlet generator not installed")
        quadlets = Path(self.tmp.name) / "quadlets"
        quadlets.mkdir()
        with patch.dict(os.environ, {"GPU_RESOURCE_NAME": "nvidia.com/TU104GL_Tesla_T4"}):
            (quadlets / "baseline.container").write_text(render.baseline())
            (quadlets / "workload.container").write_text(render.workload())
        result = subprocess.run([str(generator), "--dryrun"], capture_output=True, text=True,
                                env={**os.environ, "QUADLET_UNIT_DIRS": str(quadlets)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("baseline.service", result.stdout)
        self.assertIn("workload.service", result.stdout)
        self.assertIn("--authfile=/etc/containers/registry-auth.json", result.stdout)
        self.assertIn("nvidia.com/gpu=all", result.stdout)
        self.assertNotRegex(result.stderr, r"(?i)(unsupported|failed|error)")


if __name__ == "__main__":
    unittest.main()
