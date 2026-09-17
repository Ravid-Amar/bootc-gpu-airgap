#!/usr/bin/env python3
"""Render HTTP RPM repositories, or run a build against a private S3 snapshot."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from threading import Thread
from urllib.parse import urlsplit


def repo_names():
    names = os.environ.get("RPM_REPO_NAMES", "BaseOS AppStream").split()
    if not names or any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in names):
        raise ValueError("RPM_REPO_NAMES must contain space-separated repository directory names")
    return names


def repo_config():
    url = os.environ.get("RPM_REPO_BASEURL", "").rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or re.search(r"\s", url)):
        raise ValueError("RPM_REPO_BASEURL must be an HTTP/HTTPS URL without credentials or a query string")
    key = os.environ.get("RPM_REPO_GPGKEY", "file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release")
    key_url = urlsplit(key)
    if (not key.startswith(("file:///", "https://", "http://")) or re.search(r"\s", key)
            or key_url.username or key_url.password or key_url.query or key_url.fragment):
        raise ValueError("RPM_REPO_GPGKEY must be a local-file or HTTP/HTTPS URL without credentials")
    return "\n".join(
        f"[bootc-{name}]\nname=Offline {name}\nbaseurl={url}/{name}/\n"
        f"enabled=1\ngpgcheck=1\ngpgkey={key}\nsslverify=1\n"
        for name in repo_names()
    )


class RepoHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def run_with_s3(command):
    source = os.environ.get("RPM_REPO_S3_URI", "")
    endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    if not source.startswith("s3://") or not endpoint.startswith(("https://", "http://")):
        raise ValueError("Set RPM_REPO_S3_URI=s3://bucket/snapshot/ and S3_ENDPOINT_URL")
    if os.environ.get("RPM_REPO_BASEURL"):
        raise ValueError("Choose RPM_REPO_BASEURL or RPM_REPO_S3_URI, not both")
    if os.environ.get("BIB_NETWORK", "host") != "host":
        raise ValueError("Private S3 repository mode requires BIB_NETWORK=host")
    host = os.environ.get("RPM_REPO_HOST", "127.0.0.1")
    port = int(os.environ.get("RPM_REPO_PORT", "0"))
    if not re.fullmatch(r"[0-9.]+", host) or not 0 <= port <= 65535:
        raise ValueError("RPM_REPO_HOST must be a runner IPv4 address; RPM_REPO_PORT must be 0..65535")
    names = repo_names()
    Path("build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rpm-snapshot-", dir="build") as directory:
        result = subprocess.run(["aws", "--endpoint-url", endpoint, "s3", "sync", source,
                                 directory, "--only-show-errors"])
        if result.returncode:
            return result.returncode
        for name in names:
            if not (Path(directory) / name / "repodata/repomd.xml").is_file():
                raise ValueError(f"S3 snapshot is missing {name}/repodata/repomd.xml")
        # Bind only to the selected runner address, and serve only the snapshot.
        with ThreadingHTTPServer((host, port), partial(RepoHandler, directory=directory)) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            env = {**os.environ, "RPM_REPO_BASEURL": f"http://{host}:{server.server_port}",
                   "BOOTC_RPM_REPOS_READY": "1"}
            process = None
            previous = {}

            def forward(signum, frame):
                if process is not None:
                    try:
                        os.killpg(process.pid, signum)
                    except ProcessLookupError:
                        pass

            try:
                process = subprocess.Popen(command, env=env, start_new_session=True)
                for signum in (signal.SIGTERM, signal.SIGINT):
                    previous[signum] = signal.signal(signum, forward)
                return process.wait()
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
                server.shutdown()
                thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    render = commands.add_parser("render")
    render.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.action == "render":
            args.output.write_text(repo_config())
            return 0
        command = args.command
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            parser.error("run requires a command after --")
        return run_with_s3(command)
    except (ValueError, OSError):
        print("RPM repository setup failed. Check S3 access, repository metadata, URLs, and runner address; see ci/OFFLINE.md.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
