# Use with the original nvidia-offline/bundle.sh on the connected builder.
# Adjust engine and download-image entitlement setup for that machine.
BASE_IMAGE='registry.redhat.io/rhel9/rhel-bootc:9.5'
CONTAINER_ENGINE='docker'
DOWNLOAD_IMAGE='registry.redhat.io/rhel9/rhel-bootc:9.5-registered'
DRIVER_VERSION='580.105.08'
TOOLKIT_VERSION='1.20.0-1'
DRIVER_MODE='open-dkms'
KMOD_NEVRA=''
EXTRA_PACKAGES=(podman cloud-init qemu-guest-agent)
SAVE_BASE_IMAGE=1
