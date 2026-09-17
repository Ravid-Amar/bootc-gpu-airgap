# NVIDIA bootc air-gap

Build a minimal NVIDIA-enabled RHEL bootc image for disconnected environments.
The tooling downloads and verifies the NVIDIA driver, NVIDIA Container Toolkit,
and required RPM dependencies on a connected machine, then packages a
self-contained bundle for an offline builder.

No sample application or optional RPMs are installed in the base image. Required
NVIDIA dependencies (including DKMS compilation tools) and packages already in
RHEL remain.

| Directory | Purpose |
| --- | --- |
| [nvidia-offline/](nvidia-offline/README.md) | Prepare new bundles or update driver/toolkit versions on the connected machine. |
| [examples/](examples/README.md) | Optional application and registry helper examples. |
| [ci/](ci/README.md) | Optional GitLab/VM pipeline; separate from the minimal installation. |

## Quick start

Requirements: Linux x86_64, Bash, Python 3.9+, Docker or Podman, access to the
matching RHEL repositories, and enough disk space for container images and RPMs.
A GPU is not required on the connected preparation machine.

```bash
cp nvidia-offline/config.example.sh nvidia-offline/config.sh
# Edit config.sh with the base image and exact NVIDIA versions.
bash nvidia-offline/bundle.sh inspect nvidia-offline/config.sh
bash nvidia-offline/bundle.sh prepare nvidia-offline/config.sh nvidia-offline/dist/release-01
bash nvidia-offline/package-transfer.sh nvidia-offline/dist/release-01 transfer
```

The generated `transfer/` directory contains its own README and everything to
copy to the offline builder. Generated bundles, local configuration, and archived
releases are intentionally excluded from Git.

Read the [offline preparation guide](nvidia-offline/README.md) before building.
To add packages or configuration inside the isolated network, follow the
[custom-base workflow](nvidia-offline/CUSTOM_BASE_AIRGAP.md).

## Development

Run the local checks with:

```bash
bash check.sh
```

The optional [GitLab pipeline](ci/README.md) builds a golden VM disk and publishes
an OpenShift Virtualization template. It is separate from the minimal offline
installation workflow.

## Licensing

No license has been granted yet. Add a `LICENSE` file before distributing the
project as open source.
