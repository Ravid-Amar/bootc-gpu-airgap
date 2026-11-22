# V100 variant of nvidia-vm.config.sh for the optional GitLab/VM pipeline.
# Adjust engine and download-image entitlement setup for the connected builder.
BASE_IMAGE='registry.redhat.io/rhel9/rhel-bootc:9.5'
CONTAINER_ENGINE='docker'
DOWNLOAD_IMAGE='registry.redhat.io/rhel9/rhel-bootc:9.5-registered'
DRIVER_VERSION='580.105.08'
TOOLKIT_VERSION='1.20.0-1'
# V100 requires proprietary modules. Keep the driver on the R580 branch.
DRIVER_MODE='proprietary-dkms'
KMOD_NEVRA=''
EXTRA_PACKAGES=(podman cloud-init qemu-guest-agent)
SAVE_BASE_IMAGE=1
