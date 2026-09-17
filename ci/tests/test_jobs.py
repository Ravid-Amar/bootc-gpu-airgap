"""Exercise the real job scripts with isolated fake external services.

These checks validate orchestration/failure handling, not RPM installation or
bootc-image-builder itself. No registry or cluster is contacted.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from urllib.request import urlopen


CI = Path(__file__).resolve().parents[1]


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bootc-jobs-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project with spaces"
        self.root.mkdir()
        shutil.copytree(CI, self.root / "ci", ignore=shutil.ignore_patterns("__pycache__"))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "commands.jsonl"
        mock = self.bin / "mock.py"
        mock.write_text('''#!/usr/bin/env python3
import hashlib,json,os,pathlib,shutil,sys
from urllib.request import urlopen
tool=pathlib.Path(sys.argv[0]).name
a=sys.argv[1:]
with open(os.environ["MOCK_LOG"],"a") as log:
    log.write(json.dumps([tool]+a)+"\\n")
if tool=="sudo":
    os.execvp(a[1],a[1:])
elif tool=="aws":
    if os.environ.get("MOCK_S3_FAIL"): sys.exit(45)
    shutil.copytree(os.environ["MOCK_S3_SOURCE"],a[a.index("sync")+2],dirs_exist_ok=True)
elif tool=="podman":
    if a[0]=="info":
        print("false" if "Rootless" in a[-1] else "/var/lib/containers/storage")
    elif a[0]=="login":
        assert sys.stdin.read()=="ci-secret"
        pathlib.Path(a[a.index("--authfile")+1]).write_text("dummy-auth")
    elif a[:2]==["image","inspect"]:
        print("sha256:"+"a"*64)
    elif a[0]=="save":
        if os.environ.get("MOCK_SAVE_FAIL"): sys.exit(46)
        sys.stdout.buffer.write(b"fake image archive")
    elif a[0]=="build" and os.environ.get("MOCK_REPO_CONFIG"):
        repo=pathlib.Path(a[-1])/"rpm-repos/bootc.repo"
        if repo.exists(): pathlib.Path(os.environ["MOCK_REPO_CONFIG"]).write_text(repo.read_text())
    elif a[0]=="push":
        if os.environ.get("MOCK_PUSH_FAIL"): sys.exit(43)
        pathlib.Path(a[a.index("--digestfile")+1]).write_text("sha256:"+"b"*64)
    elif a[0]=="run":
        if os.environ.get("MOCK_BIB_FAIL"): sys.exit(42)
        if os.environ.get("MOCK_CHECK_REPO"):
            repo=pathlib.Path(os.environ["MOCK_REPO_CONFIG"]).read_text()
            urls=[line[8:] for line in repo.splitlines() if line.startswith("baseurl=")]
            assert len(urls)==2
            for url in urls:
                assert urlopen(url+"repodata/repomd.xml",timeout=5).read()==b"test metadata"
        output=next(x[:-8] for x in a if x.endswith(":/output"))
        p=pathlib.Path(output)/"qcow2/disk.qcow2"
        p.parent.mkdir()
        p.write_bytes(b"fake disk")
elif tool=="qemu-img":
    if a[0]=="check" and os.environ.get("MOCK_DISK_FAIL"): sys.exit(2)
    if a[0]=="info": print(json.dumps({"format":"qcow2","virtual-size":21474836480}))
elif tool=="kubectl":
    if "--from-file=vm.json=build/vm.json" in a:
        print(json.dumps({"apiVersion":"v1","kind":"ConfigMap","metadata":{"name":"test"}}))
    elif a[-2:]==["-f","-"]:
        obj=json.load(sys.stdin)
        if obj["kind"]=="Secret":
            assert "userdata" in obj["data"]
    elif "--ignore-not-found" in a and "virtualmachine" in a and os.environ.get("MOCK_VM_EXISTS"):
        print("virtualmachine.kubevirt.io/already-exists")
    elif "--ignore-not-found" in a and "secret" in a and os.environ.get("MOCK_SECRET_EXISTS"):
        print("secret/already-exists")
    elif "create" in a and "--dry-run=server" in a and os.environ.get("MOCK_DRY_RUN_FAIL"):
        sys.exit(44)
''')
        mock.chmod(0o755)
        for tool in ("sudo", "podman", "qemu-img", "kubectl", "aws"):
            (self.bin / tool).symlink_to(mock)
        self.env = {**os.environ, "PATH": str(self.bin) + ":" + os.environ["PATH"],
                    "MOCK_LOG": str(self.log), "REGISTRY_HOST": "registry.internal",
                    "IMAGE_PREFIX": "registry.internal/team", "REGISTRY_USER": "robot",
                    "REGISTRY_PASSWORD": "ci-secret", "PODMAN_USE_SUDO": "1",
                    "NVIDIA_BASE_REF": "registry.internal/base@sha256:" + "a" * 64,
                    "CONTAINERDISK_REF": "registry.internal/disk@sha256:" + "b" * 64,
                    "BIB_IMAGE": "internal/bib:v1", "BASELINE_IMAGE": "internal/baseline:v1",
                    "VM_NAMESPACE": "test", "VM_NAME": "test-vm", "DISK_PULL_SECRET": "disk-auth",
                    "DEPLOY_VM": "false", "VM_PLATFORM": "openshift"}
        kubeconfig = self.root / "kubeconfig"
        kubeconfig.write_text("mock")
        self.env["KUBECONFIG"] = str(kubeconfig)

    def run_job(self, name, **extra):
        return subprocess.run(["bash", str(self.root / "ci" / name)], cwd=self.root,
                              env={**self.env, **extra}, text=True, capture_output=True)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def vm_inputs(self):
        files = {"WORKLOAD_AUTH_FILE": '{"auths":{"internal":{"auth":"cm9ib3Q6c2VjcmV0"}}}',
                 "WORKLOAD_CONFIG_FILE": "message: private-application-config\n",
                 "VM_SSH_KEY_FILE": "ssh-ed25519 AAAATEST fixture\n"}
        result = {"DEPLOY_VM": "true", "WORKLOAD_IMAGE": "internal/app:v1"}
        for name, content in files.items():
            path = self.root / name
            path.write_text(content)
            result[name] = str(path)
        return result

    def test_golden_job_pins_outputs_and_uses_local_bib(self):
        result = self.run_job("build-golden.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        envfile = (self.root / "build/golden.env").read_text()
        self.assertIn("CONTAINERDISK_REF=registry.internal/team/containerdisk@sha256:", envfile)
        self.assertNotIn("ci-secret", envfile + result.stdout + result.stderr)
        commands = self.commands()
        run = next(c for c in commands if c[:2] == ["podman", "run"])
        for flag in ("--privileged", "--pull=never", "--local", "--type"):
            self.assertIn(flag, run)
        self.assertIn("--network=host", run)
        for build in [c for c in commands if c[:2] == ["podman", "build"]]:
            self.assertIn("--network=none", build)
            self.assertIn("--pull=never", build)
        login = next(c for c in commands if c[:2] == ["podman", "login"])
        self.assertFalse(Path(login[login.index("--authfile")+1]).exists())

    def test_failed_bib_or_disk_validation_never_publishes_containerdisk(self):
        for fail in ("MOCK_BIB_FAIL", "MOCK_DISK_FAIL"):
            with self.subTest(fail=fail):
                self.log.write_text("")
                result = self.run_job("build-golden.sh", **{fail: "1"})
                self.assertNotEqual(result.returncode, 0)
                pushes = [c for c in self.commands() if c[:2] == ["podman", "push"]]
                self.assertEqual(len(pushes), 1)  # OS published; disk not published.
                self.assertNotIn("containerdisk", pushes[0][-1])
                self.assertFalse((self.root / "build/golden.env").exists())

    def test_template_publish_does_not_require_or_send_guest_credentials(self):
        result = self.run_job("deploy.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "build/template.json").exists())
        self.assertFalse(any("secret" in c for c in self.commands()))
        self.assertFalse(any("create" in c and "build/vm.json" in c for c in self.commands()))

    def test_failed_registry_push_stops_before_disk_build(self):
        result = self.run_job("build-golden.sh", MOCK_PUSH_FAIL="1")
        self.assertEqual(result.returncode, 43)
        self.assertFalse(any(c[:2] == ["podman", "run"] for c in self.commands()))
        self.assertFalse((self.root / "build/golden.env").exists())

    def test_invalid_golden_inputs_fail_before_podman_or_registry(self):
        for extra in ({"BASELINE_IMAGE": "internal/image%bad"}, {"BIB_CONFIG": "missing.toml"}):
            with self.subTest(extra=extra):
                result = self.run_job("build-golden.sh", **extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())

    def test_golden_uses_http_repository_without_aws(self):
        repo = self.root / "rendered.repo"
        result = self.run_job("build-golden.sh", RPM_REPO_BASEURL="https://s3.internal/bucket/repos",
                              MOCK_REPO_CONFIG=str(repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("baseurl=https://s3.internal/bucket/repos/BaseOS/", repo.read_text())
        self.assertFalse(any(c[0] == "aws" for c in self.commands()))

    def test_golden_uses_private_s3_repository_and_closes_server(self):
        snapshot = self.root / "rpm snapshot"
        for name in ("BaseOS", "AppStream"):
            (snapshot / name / "repodata").mkdir(parents=True)
            (snapshot / name / "repodata/repomd.xml").write_bytes(b"test metadata")
        repo = self.root / "rendered.repo"
        result = self.run_job("build-golden.sh", RPM_REPO_S3_URI="s3://rpms/snapshot/",
                              S3_ENDPOINT_URL="https://s3.internal", MOCK_S3_SOURCE=str(snapshot),
                              MOCK_REPO_CONFIG=str(repo), MOCK_CHECK_REPO="1",
                              AWS_SECRET_ACCESS_KEY="private-s3-test-credential")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "build/golden.env").exists())
        self.assertEqual(list((self.root / "build").glob("rpm-snapshot-*")), [])
        url = next(line[8:] for line in repo.read_text().splitlines() if line.startswith("baseurl="))
        with self.assertRaises(OSError):
            urlopen(url, timeout=1)
        self.assertNotIn("private-s3-test-credential", repo.read_text() + result.stdout + result.stderr + self.log.read_text())

    def test_existing_vm_is_not_modified(self):
        result = self.run_job("deploy.sh", MOCK_VM_EXISTS="1", **self.vm_inputs())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertFalse(any("apply" in c or "create" in c for c in self.commands()))

    def test_existing_cloudinit_secret_is_not_modified(self):
        result = self.run_job("deploy.sh", MOCK_SECRET_EXISTS="1", **self.vm_inputs())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing guest credentials are never overwritten", result.stderr)
        self.assertFalse(any("apply" in c or "create" in c for c in self.commands()))

    def test_invalid_deployment_inputs_do_not_contact_cluster(self):
        inputs = self.vm_inputs()
        for extra in ({"DEPLOY_VM": "typo"}, {"VM_PLATFORM": "typo"},
                      {"WORKLOAD_AUTH_FILE": "missing.json"}, {"VM_CPU_CORES": "0"}):
            with self.subTest(extra=extra):
                result = self.run_job("deploy.sh", **{**inputs, **extra})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())

    def test_server_rejection_does_not_publish_or_create_secrets(self):
        result = self.run_job("deploy.sh", MOCK_DRY_RUN_FAIL="1", **self.vm_inputs())
        self.assertEqual(result.returncode, 44)
        self.assertFalse(any("apply" in c or c[-2:] == ["-f", "-"] for c in self.commands()))

    def test_full_vm_deployment_creates_secret_before_vm_and_waits(self):
        result = self.run_job("deploy.sh", **self.vm_inputs())
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        secret = commands.index(["kubectl", "create", "-f", "-"])
        vm = commands.index(["kubectl", "create", "-f", "build/vm.json"])
        self.assertLess(secret, vm)
        self.assertIn("wait", commands[-1])
        artifacts = "".join(p.read_text() for p in (self.root / "build").glob("*.json"))
        for sensitive in ("cm9ib3Q6c2VjcmV0", "private-application-config"):
            self.assertNotIn(sensitive, artifacts + result.stdout + result.stderr + self.log.read_text())

    def test_standalone_template_is_a_configmap_artifact(self):
        result = self.run_job("deploy.sh", VM_PLATFORM="kubevirt")
        self.assertEqual(result.returncode, 0, result.stderr)
        template = json.loads((self.root / "build/template.json").read_text())
        self.assertEqual(template["kind"], "ConfigMap")
        self.assertFalse(any("secret" in c for c in self.commands()))

    def test_direct_deployment_uses_documented_defaults(self):
        for name in ("VM_NAMESPACE", "VM_NAME", "DISK_PULL_SECRET"):
            self.env.pop(name)
        result = self.run_job("deploy.sh", **self.vm_inputs())
        self.assertEqual(result.returncode, 0, result.stderr)
        vm = json.loads((self.root / "build/vm.json").read_text())
        self.assertEqual(vm["metadata"], {"name": "nvidia-worker-01", "namespace": "golden-images"})

    def test_base_rejects_old_bundle_before_registry_or_podman(self):
        offline = self.offline_inputs("podman-0:5.2.2-1.x86_64\n")
        result = self.run_job("build-base.sh", OFFLINE_DIR=str(offline))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lacks cloud-init", result.stderr)
        self.assertFalse(self.log.exists())

    def test_base_rejects_missing_guest_agent(self):
        offline = self.offline_inputs("cloud-init-0:23.4-1.noarch\n")
        result = self.run_job("build-base.sh", OFFLINE_DIR=str(offline))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lacks qemu-guest-agent", result.stderr)
        self.assertFalse(self.log.exists())

    def test_base_build_verifies_loads_builds_and_publishes_digest(self):
        offline = self.offline_inputs("cloud-init-0:23.4-1.noarch\nqemu-guest-agent-0:9.0-1.x86_64\n")
        result = self.run_job("build-base.sh", OFFLINE_DIR=str(offline))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "build/base.env").read_text(),
                         "NVIDIA_BASE_REF=registry.internal/team/nvidia-base@sha256:" + "b" * 64 + "\n")
        commands = self.commands()
        build = next(c for c in commands if c[:2] == ["podman", "build"])
        self.assertIn("--network=none", build)
        self.assertIn("--pull=never", build)
        self.assertTrue(any(c[:2] == ["podman", "load"] for c in commands))

    def test_base_downloads_and_verifies_s3_bundle(self):
        offline = self.offline_inputs("cloud-init-0:23.4-1.noarch\nqemu-guest-agent-0:9.0-1.x86_64\n")
        result = self.run_job("build-base.sh", OFFLINE_S3_URI="s3://bootc/releases/v1/",
                              S3_ENDPOINT_URL="https://s3.internal", MOCK_S3_SOURCE=str(offline))
        self.assertEqual(result.returncode, 0, result.stderr)
        command = self.commands()[0]
        self.assertEqual(command[:6], ["aws", "--endpoint-url", "https://s3.internal", "s3", "sync", "s3://bootc/releases/v1/"])
        self.assertTrue((self.root / "build/base.env").exists())

    def test_s3_failure_stops_before_registry_or_podman(self):
        result = self.run_job("build-base.sh", OFFLINE_S3_URI="s3://bootc/releases/v1/",
                              S3_ENDPOINT_URL="https://s3.internal", MOCK_S3_FAIL="1")
        self.assertEqual(result.returncode, 45)
        self.assertFalse(any(c[0] in ("podman", "sudo") for c in self.commands()))

    def test_s3_corruption_is_rejected_before_podman(self):
        offline = self.offline_inputs("cloud-init-0:23.4-1.noarch\nqemu-guest-agent-0:9.0-1.x86_64\n")
        (offline / "base-image.tar").write_bytes(b"corrupt")
        result = self.run_job("build-base.sh", OFFLINE_S3_URI="s3://bootc/releases/v1/",
                              S3_ENDPOINT_URL="https://s3.internal", MOCK_S3_SOURCE=str(offline))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] in ("podman", "sudo") for c in self.commands()))

    def run_image_transfer(self, *args, **extra):
        return subprocess.run(["bash", str(self.root / "ci/offline-images.sh"), *map(str, args)],
                              cwd=self.root, env={**self.env, **extra}, text=True, capture_output=True)

    def test_offline_image_save_and_load_never_pull_or_push(self):
        output = self.root / "image transfer"
        result = self.run_image_transfer("save", output, "localhost/bib:offline", "internal/app:v1")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_image_transfer("load", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        self.assertTrue(any(c[:2] == ["podman", "load"] for c in commands))
        self.assertFalse(any(c[:2] in (["podman", "pull"], ["podman", "push"]) for c in commands))

    def test_offline_image_tampering_is_rejected_before_loading(self):
        output = self.root / "image transfer"
        result = self.run_image_transfer("save", output, "localhost/bib:offline")
        self.assertEqual(result.returncode, 0, result.stderr)
        (output / "images.tar").write_bytes(b"corrupt")
        result = self.run_image_transfer("load", output)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ["podman", "load"] for c in self.commands()))

    def test_failed_image_save_does_not_publish_checksum(self):
        output = self.root / "image transfer"
        result = self.run_image_transfer("save", output, "localhost/bib:offline", MOCK_SAVE_FAIL="1")
        self.assertEqual(result.returncode, 46)
        self.assertFalse((output / "SHA256SUMS").exists())

    def offline_inputs(self, packages):
        offline = self.root / "offline"
        offline.mkdir()
        source = self.root / "fixture/nvidia-offline"
        source.mkdir(parents=True)
        (source / "install.sh").write_text("#!/bin/bash\nexit 0\n")
        (source / "packages.txt").write_text(packages)
        (source / "base-packages.txt").write_text("kernel-core-0:1-1.x86_64\n")
        with tarfile.open(offline / "nvidia-offline.tar.gz", "w:gz") as archive:
            archive.add(source, arcname="nvidia-offline")
        (offline / "base-image.tar").write_bytes(b"mock base")
        (offline / "base-image.txt").write_text("localhost/base:test\n")
        (offline / "base-image-id.txt").write_text("a" * 64 + "\n")
        manifest = "".join(hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.name+"\n" for p in offline.iterdir())
        (offline / "TRANSFER.SHA256SUMS").write_text(manifest)
        return offline


if __name__ == "__main__":
    unittest.main()
