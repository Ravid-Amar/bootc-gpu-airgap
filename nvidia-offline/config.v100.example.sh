# NVIDIA Tesla V100 (Volta): proprietary R580 modules compiled with DKMS.
# Copy to config.sh, then adjust the base image and entitlement setup.
# Prefer a digest; bundle preparation freezes the resolved image ID and kernel.
BASE_IMAGE='registry.redhat.io/rhel9/rhel-bootc:9.5'
CONTAINER_ENGINE='podman'
# Optional registered image with exactly the same installed RPMs as BASE_IMAGE.
DOWNLOAD_IMAGE=''

# Exact pins, shared with the open-module profile; these are not latest selectors.
# Keep V100 on R580 with proprietary modules when updating this configuration.
DRIVER_VERSION='580.105.08'
TOOLKIT_VERSION='1.20.0-1'
DRIVER_MODE='proprietary-dkms'
KMOD_NEVRA=''

# Minimal OS: NVIDIA and required dependencies, including DKMS/build tools.
# Use CUDA 12.x application containers that include Volta (sm_70) support.
EXTRA_PACKAGES=()
SAVE_BASE_IMAGE=1
