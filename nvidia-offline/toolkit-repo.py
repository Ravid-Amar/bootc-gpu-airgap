#!/usr/bin/python3
"""Preserve NVIDIA's signed metadata and verify its checksums for toolkit RPMs."""
import gzip
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

BASE_URL = "https://nvidia.github.io/libnvidia-container/stable/rpm/x86_64/"
TOOLKIT_NAMES = {
    "nvidia-container-toolkit", "nvidia-container-toolkit-base",
    "libnvidia-container-tools", "libnvidia-container1",
}
REPO_NS = {"r": "http://linux.duke.edu/metadata/repo"}
PRIMARY_NS = {"p": "http://linux.duke.edu/metadata/common"}


def checked_path(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or ":" in relative or not path.parts:
        raise ValueError(f"Unsafe repository path: {relative}")
    return root.joinpath(*path.parts)


def fetch(root, relative):
    target = checked_path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(BASE_URL + relative, timeout=120) as response:
        target.write_bytes(response.read())
    return target


def check_digest(path, algorithm, expected):
    if algorithm not in ("sha256", "sha512"):
        raise ValueError(f"Unsupported checksum algorithm: {algorithm}")
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError(f"Repository checksum mismatch: {path.name}")


def main():
    bundle = Path("/bundle")
    repo = bundle / "toolkit-repo"
    repomd = fetch(repo, "repodata/repomd.xml")
    signature = fetch(repo, "repodata/repomd.xml.asc")
    with tempfile.TemporaryDirectory() as keyring:
        gpg = ["gpg", "--batch", "--homedir", keyring]
        subprocess.run(gpg + ["--import", str(bundle / "keys/nvidia-container-toolkit.asc")], check=True)
        subprocess.run(gpg + ["--verify", str(signature), str(repomd)], check=True)

    primary = None
    for data in ET.parse(repomd).getroot().findall("r:data", REPO_NS):
        location = data.find("r:location", REPO_NS).attrib["href"]
        metadata = fetch(repo, location)
        checksum = data.find("r:checksum", REPO_NS)
        check_digest(metadata, checksum.attrib["type"], checksum.text)
        if data.attrib["type"] == "primary":
            primary = metadata
    if primary is None:
        raise ValueError("NVIDIA repository has no primary metadata")

    # Query identities rather than relying on filename conventions.
    rpms = {}
    for rpm in (bundle / "toolkit-packages").glob("*.rpm"):
        fields = subprocess.check_output(
            ["rpm", "-qp", "--qf", "%{NAME} %{VERSION} %{RELEASE} %{ARCH}", str(rpm)],
            text=True, stderr=subprocess.DEVNULL).split()
        if fields[0] in TOOLKIT_NAMES:
            rpms[tuple(fields)] = rpm
    if {identity[0] for identity in rpms} != TOOLKIT_NAMES:
        raise ValueError("Expected all four NVIDIA Container Toolkit packages")

    opener = gzip.open if primary.suffix == ".gz" else open
    with opener(primary, "rb") as stream:
        tree = ET.parse(stream)
    for package in tree.getroot().findall("p:package", PRIMARY_NS):
        version = package.find("p:version", PRIMARY_NS).attrib
        identity = (package.findtext("p:name", namespaces=PRIMARY_NS), version["ver"],
                    version["rel"], package.findtext("p:arch", namespaces=PRIMARY_NS))
        if identity not in rpms:
            continue
        rpm = rpms.pop(identity)
        checksum = package.find("p:checksum", PRIMARY_NS)
        check_digest(rpm, checksum.attrib["type"], checksum.text)
        relative = package.find("p:location", PRIMARY_NS).attrib["href"]
        target = checked_path(repo, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(rpm), str(target))
    if rpms:
        raise ValueError(f"Toolkit RPMs absent from signed NVIDIA metadata: {list(rpms)}")
    # Remove any copy selected from the CUDA repository during dependency
    # resolution; offline DNF must use the original signed toolkit repository.
    for rpm in (bundle / "repo").glob("*.rpm"):
        name = subprocess.check_output(["rpm", "-qp", "--qf", "%{NAME}", str(rpm)],
                                       text=True, stderr=subprocess.DEVNULL)
        if name in TOOLKIT_NAMES:
            rpm.unlink()
    (bundle / "toolkit-packages").rmdir()
    print("Toolkit packages verified against NVIDIA's signed repository metadata.")


if __name__ == "__main__":
    main()
