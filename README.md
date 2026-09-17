# bootc GPU air-gap

`bootc-gpu-airgap` builds a minimal NVIDIA-enabled Red Hat® Enterprise Linux®
(RHEL) bootc image for disconnected environments. The tooling downloads and
verifies the NVIDIA driver, NVIDIA Container Toolkit, and required RPM
dependencies on a connected machine, then packages a self-contained bundle for
an offline builder.

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

The original source code and documentation in this repository are licensed under
the [Apache License 2.0](LICENSE). That license does not cover Red Hat Enterprise
Linux, NVIDIA software, downloaded RPMs, container images, or generated transfer
bundles; those remain subject to their respective terms.

## Trademarks and affiliation

This is an independent project and is not affiliated with, endorsed by, or
sponsored by Red Hat or NVIDIA.

Red Hat, Red Hat Enterprise Linux, and RHEL are trademarks or registered
trademarks of Red Hat, Inc. or its subsidiaries in the United States and other
countries. NVIDIA is a trademark or registered trademark of NVIDIA Corporation
in the United States and other countries. All other trademarks belong to their
respective owners.
