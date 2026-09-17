#!/usr/bin/python3
"""Copy public signing keys, never repository credentials, into the bundle."""
import hashlib
from pathlib import Path
import urllib.request

import dnf

with dnf.Base() as base:
    base.read_all_repos()
    for repo in base.repos.iter_enabled():
        if not repo.gpgcheck and not (repo.id == "nvidia-container-toolkit" and repo.repo_gpgcheck):
            raise SystemExit(f"Repository {repo.id} has gpgcheck disabled; fix it before preparing.")
        for url in repo.gpgkey:
            if not url.startswith(("https://", "file://")):
                raise SystemExit(f"Unsupported signing-key URL for {repo.id}: {url}")
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            name = ("nvidia-container-toolkit.asc" if repo.id == "nvidia-container-toolkit"
                    else hashlib.sha256(data).hexdigest() + ".asc")
            Path("/bundle/keys", name).write_bytes(data)
