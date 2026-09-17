#!/usr/bin/env bash
set -euo pipefail
source /offline-tools/common.sh
source /offline-tools/config.sh
check_platform
mkdir -p /bundle/repo /bundle/keys
package_inventory > /bundle/base-packages.txt
cmp -s /bundle/base-packages.txt /offline-tools/base-packages.txt ||
    die 'DOWNLOAD_IMAGE and BASE_IMAGE must have identical installed RPMs before preparation.'
image_kernel > /bundle/kernel.txt
cp /etc/os-release /bundle/target-os-release

# Only the disposable downloader image gets these tools and network repos.
dnf --releasever="$VERSION_ID" -y install dnf-plugins-core createrepo_c findutils gnupg2
curl --fail --location --retry 3 \
    https://developer.download.nvidia.com/compute/cuda/repos/rhel9/x86_64/cuda-rhel9.repo \
    -o /etc/yum.repos.d/nvidia-driver.repo
curl --fail --location --retry 3 \
    https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
    -o /etc/yum.repos.d/nvidia-container-toolkit.repo

if [[ $DRIVER_MODE == *dkms ]]; then
    # CRB supplies dependencies used by EPEL's DKMS/build packages.
    dnf --releasever="$VERSION_ID" config-manager --set-enabled codeready-builder-for-rhel-9-x86_64-rpms
    cat > /etc/yum.repos.d/nvidia-offline-epel.repo <<'EOF'
[nvidia-offline-epel]
name=EPEL 9 for NVIDIA DKMS build dependencies
baseurl=https://dl.fedoraproject.org/pub/epel/9/Everything/$basearch/
enabled=1
gpgcheck=1
gpgkey=https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-9
EOF
fi
dnf --releasever="$VERSION_ID" -y module reset nvidia-driver
