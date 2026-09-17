#!/usr/bin/env python3
"""Render JSON (also valid YAML) and cloud-config without shell interpolation.

Secrets are only emitted for the explicit cloudinit-secret command. Pipe that
command directly to kubectl; do not save its output as a pipeline artifact.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent


def value(name, default=None):
    result = os.environ.get(name, default)
    if not result:
        raise ValueError(f"Set {name}; see ci/README.md")
    return result


def dns_name(name, default=None):
    result = value(name, default)
    if len(result) > 63 or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", result):
        raise ValueError(f"{name} must be a DNS label of at most 63 characters")
    return result


def image_ref(name, immutable=False):
    result = value(name)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]+", result) or "/" not in result:
        raise ValueError(f"{name} must be a fully qualified image reference")
    if immutable and not re.search(r"@sha256:[a-f0-9]{64}$", result):
        raise ValueError(f"{name} must be pinned by sha256 digest")
    return result


def quantity(name, default):
    result = value(name, default)
    if not re.fullmatch(r"[1-9][0-9]*(?:[KMGT]i)?", result):
        raise ValueError(f"{name} must be a positive quantity such as {default}")
    return result


def gpu_resource():
    result = os.environ.get("GPU_RESOURCE_NAME", "")
    if result and not re.fullmatch(r"[a-z0-9.-]+/[A-Za-z0-9_.-]+", result):
        raise ValueError("GPU_RESOURCE_NAME must be a cluster-advertised resource such as vendor.example/device")
    return result


def baseline():
    return (ROOT / "golden/baseline.container").read_text().replace("@BASELINE_IMAGE@", image_ref("BASELINE_IMAGE"))


def workload():
    source = Path(os.environ.get("WORKLOAD_QUADLET_FILE", str(ROOT / "vm/workload.container")))
    text = source.read_text()
    gpu = gpu_resource()
    replacements = {
        "@WORKLOAD_IMAGE@": image_ref("WORKLOAD_IMAGE"),
        "@GPU_UNIT@": "Requires=nvidia-cdi-refresh.service\nAfter=nvidia-cdi-refresh.service" if gpu else "",
        "@GPU_CONTAINER@": "AddDevice=nvidia.com/gpu=all\nSecurityLabelDisable=true" if gpu else "",
    }
    for marker, replacement in replacements.items():
        text = text.replace(marker, replacement)
    if re.search(r"@[A-Z_]+@", text):
        raise ValueError("Unresolved placeholder in workload Quadlet")
    if "[Container]" not in text or "[Install]" not in text:
        raise ValueError("Workload Quadlet needs [Container] and [Install] sections")
    return text


def cloud_config():
    auth_path = Path(value("WORKLOAD_AUTH_FILE"))
    auth = json.loads(auth_path.read_text())
    if not isinstance(auth, dict) or not isinstance(auth.get("auths"), dict) or not auth["auths"]:
        raise ValueError("WORKLOAD_AUTH_FILE must be a Docker/Podman auth JSON with inline auths")
    for entry in auth["auths"].values():
        if not isinstance(entry, dict) or not (entry.get("auth") or entry.get("identitytoken")):
            raise ValueError("Use inline registry auth entries; host credential helpers are not available inside the VM")
    config = Path(value("WORKLOAD_CONFIG_FILE")).read_bytes()
    ssh_key = Path(value("VM_SSH_KEY_FILE")).read_text().strip()
    if "\n" in ssh_key or not ssh_key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        raise ValueError("VM_SSH_KEY_FILE must contain one SSH public key")
    username = dns_name("VM_USER", "vmadmin")

    def file(path, data, mode="0600"):
        if isinstance(data, str):
            data = data.encode()
        return {"path": path, "owner": "root:root", "permissions": mode,
                "encoding": "b64", "content": base64.b64encode(data).decode()}

    files = [
        file("/etc/containers/registry-auth.json", json.dumps(auth)),
        file("/etc/workload/config.yaml", config),
        file("/etc/containers/systemd/workload.container", workload(), "0644"),
    ]
    commands = []
    if os.environ.get("WORKLOAD_CA_FILE"):
        files.append(file("/etc/pki/ca-trust/source/anchors/workload-registry.crt",
                          Path(os.environ["WORKLOAD_CA_FILE"]).read_bytes(), "0644"))
        commands.append(["update-ca-trust"])
    commands += [
        ["restorecon", "-RF", "/etc/containers", "/etc/workload"],
        ["systemctl", "daemon-reload"],
        # Both units are ordered after cloud-final. A blocking start deadlocks.
        ["systemctl", "start", "--no-block", "baseline.service", "workload.service"],
    ]
    return {"hostname": dns_name("VM_NAME", "nvidia-worker-01"),
            "ssh_pwauth": False, "disable_root": True,
            "users": [{"name": username, "groups": ["wheel"], "shell": "/bin/bash",
                       "lock_passwd": True, "sudo": ["ALL=(ALL) NOPASSWD:ALL"],
                       "ssh_authorized_keys": [ssh_key]}],
            "write_files": files, "runcmd": commands}


def cloudinit_secret():
    userdata = "#cloud-config\n" + json.dumps(cloud_config(), indent=2) + "\n"
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": cloud_secret_name(), "namespace": dns_name("VM_NAMESPACE", "golden-images")},
            "data": {"userdata": base64.b64encode(userdata.encode()).decode()}}


def cloud_secret_name():
    return dns_name("CLOUD_INIT_SECRET", dns_name("VM_NAME", "nvidia-worker-01") + "-cloudinit")


def vm_manifest(template=False):
    cores = int(value("VM_CPU_CORES", "4"))
    if not 1 <= cores <= 256:
        raise ValueError("VM_CPU_CORES must be between 1 and 256")
    disk_ref = image_ref("CONTAINERDISK_REF", immutable=True)
    gpu = gpu_resource()

    def param(name, concrete):
        return "${" + name + "}" if template else concrete

    name = param("VM_NAME", dns_name("VM_NAME", "nvidia-worker-01"))
    namespace = param("VM_NAMESPACE", dns_name("VM_NAMESPACE", "golden-images"))
    storage = {"resources": {"requests": {"storage": param("VM_DISK_SIZE", quantity("VM_DISK_SIZE", "40Gi"))}}}
    storage_class = os.environ.get("VM_STORAGE_CLASS", "")
    if storage_class:
        storage["storageClassName"] = param("VM_STORAGE_CLASS", storage_class)
    devices = {
        "disks": [{"name": "rootdisk", "disk": {"bus": "virtio"}, "bootOrder": 1},
                  {"name": "cloudinit", "disk": {"bus": "virtio"}}],
        "interfaces": [{"name": "default", "masquerade": {}}],
        "rng": {},
    }
    if gpu:
        devices["gpus"] = [{"name": "gpu0", "deviceName": param("GPU_RESOURCE_NAME", gpu)}]
    return {
        "apiVersion": "kubevirt.io/v1", "kind": "VirtualMachine",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "runStrategy": "Always",
            "dataVolumeTemplates": [{"metadata": {"name": name + "-root"}, "spec": {
                "source": {"registry": {"url": "docker://" + param("CONTAINERDISK_REF", disk_ref),
                                         "pullMethod": "node",
                                         "secretRef": param("DISK_PULL_SECRET", dns_name("DISK_PULL_SECRET", "containerdisk-pull"))}},
                "storage": storage,
            }}],
            "template": {"metadata": {"labels": {"kubevirt.io/domain": name}}, "spec": {
                "domain": {"machine": {"type": "q35"},
                           "firmware": {"bootloader": {"efi": {"secureBoot": False}}},
                           "cpu": {"cores": "${{VM_CPU_CORES}}" if template else cores},
                           "resources": {"requests": {"memory": param("VM_MEMORY", quantity("VM_MEMORY", "16Gi"))}},
                           "devices": devices},
                "networks": [{"name": "default", "pod": {}}],
                "volumes": [{"name": "rootdisk", "dataVolume": {"name": name + "-root"}},
                            {"name": "cloudinit", "cloudInitNoCloud": {
                                "secretRef": {"name": param("CLOUD_INIT_SECRET", cloud_secret_name())}}}],
            }},
        },
    }


def openshift_template():
    defaults = {
        "VM_NAME": dns_name("VM_NAME", "nvidia-worker-01"),
        "VM_NAMESPACE": dns_name("VM_NAMESPACE", "golden-images"),
        "CONTAINERDISK_REF": image_ref("CONTAINERDISK_REF", immutable=True),
        "CLOUD_INIT_SECRET": cloud_secret_name(),
        "DISK_PULL_SECRET": dns_name("DISK_PULL_SECRET", "containerdisk-pull"),
        "VM_CPU_CORES": value("VM_CPU_CORES", "4"),
        "VM_MEMORY": quantity("VM_MEMORY", "16Gi"),
        "VM_DISK_SIZE": quantity("VM_DISK_SIZE", "40Gi"),
    }
    if gpu_resource():
        defaults["GPU_RESOURCE_NAME"] = gpu_resource()
    if os.environ.get("VM_STORAGE_CLASS"):
        defaults["VM_STORAGE_CLASS"] = os.environ["VM_STORAGE_CLASS"]
    return {"apiVersion": "template.openshift.io/v1", "kind": "Template",
            "metadata": {"name": dns_name("TEMPLATE_NAME", "rhel95-nvidia-golden"),
                         "namespace": dns_name("VM_NAMESPACE", "golden-images"),
                         "annotations": {"description": "RHEL NVIDIA golden VM with a persistent root disk and per-VM cloud-init Secret."}},
            "parameters": [{"name": key, "value": val, "required": True} for key, val in defaults.items()],
            "objects": [vm_manifest(template=True)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["baseline", "template", "vm", "cloudinit-secret"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.kind == "cloudinit-secret" and args.output:
        parser.error("Pipe cloudinit-secret directly to kubectl; do not write it to build artifacts")
    try:
        result = {"baseline": baseline, "template": openshift_template,
                  "vm": vm_manifest, "cloudinit-secret": cloudinit_secret}[args.kind]()
        text = result if isinstance(result, str) else json.dumps(result, indent=2) + "\n"
        if args.output:
            args.output.write_text(text)
        else:
            sys.stdout.write(text)
    except (ValueError, OSError, KeyError) as exc:
        # Do not print file contents (possibly auth/config secrets).
        print(f"Rendering failed: {type(exc).__name__}. Check required variables and input file formats in ci/README.md.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
