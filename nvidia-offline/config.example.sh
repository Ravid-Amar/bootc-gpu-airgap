# Trusted Bash configuration. Copy to config.sh and edit before preparing.
# Prefer a digest. The preparation script freezes the locally resolved image ID.
BASE_IMAGE='registry.redhat.io/rhel9/rhel-bootc:latest'

# Use docker when the images are stored in Docker rather than Podman.
CONTAINER_ENGINE='podman'
# Optional registered image used only for connected downloads. Its RPM inventory
# must match BASE_IMAGE. Keep registration credentials out of BASE_IMAGE, which
# is saved for transfer and becomes the installed OS.
DOWNLOAD_IMAGE=''

# Required: driver RPM Version, and toolkit RPM Version-Release (no wildcards).
# Examples of syntax only, not recommendations: 580.95.05 and 1.18.0-1.
DRIVER_VERSION='580.105.08'
TOOLKIT_VERSION='1.20.0-1'

# RHEL 9, x86_64 only in this implementation.
# open-dkms: Turing or newer GPUs; compile for the image kernel during build.
# proprietary-dkms: use only a driver branch that supports your GPU.
# precompiled: proprietary NVIDIA RPM for the exact image kernel; no compiler.
DRIVER_MODE='open-dkms'

# Required only for precompiled mode: exact kmod RPM NEVRA from NVIDIA's repo.
# Use the inspect command to see available packages. No wildcard/latest selector.
KMOD_NEVRA=''

# Minimal installation: only NVIDIA and its required dependencies.
# Optional application dependencies belong here only when you need them.
EXTRA_PACKAGES=()

# Save the exact base image alongside the RPM archive for offline builds.
SAVE_BASE_IMAGE=1
