#!/usr/bin/env bash
set -euo pipefail
source /offline-tools/common.sh
source /offline-tools/config.sh
validate_config "${1:-prepare}"
source /bundle/target-os-release
kver=$(cat /bundle/kernel.txt)

if [[ ${1:-prepare} == inspect ]]; then
    printf '\nTarget image kernel: %s\n' "$kver"
    dnf --releasever="$VERSION_ID" module list nvidia-driver
    dnf --releasever="$VERSION_ID" repoquery --available --disable-modular-filtering \
        --qf '%{name}-%{epoch}:%{version}-%{release}.%{arch}' \
        nvidia-driver-cuda nvidia-container-toolkit 'kmod-nvidia*'
    exit 0
fi

branch=${DRIVER_VERSION%%.*}
case $DRIVER_MODE in
    open-dkms) stream="${branch}-open"; kmod="kmod-nvidia-open-dkms-${DRIVER_VERSION}" ;;
    proprietary-dkms) stream="${branch}-dkms"; kmod="kmod-nvidia-latest-dkms-${DRIVER_VERSION}" ;;
    precompiled) stream=$branch; kmod=$KMOD_NEVRA ;;
esac
dnf --releasever="$VERSION_ID" -y module enable "nvidia-driver:$stream"
toolkit_packages=("nvidia-container-toolkit-${TOOLKIT_VERSION}"
    "nvidia-container-toolkit-base-${TOOLKIT_VERSION}"
    "libnvidia-container-tools-${TOOLKIT_VERSION}"
    "libnvidia-container1-${TOOLKIT_VERSION}")
packages=("nvidia-driver-cuda-${DRIVER_VERSION}" "$kmod"
    "${toolkit_packages[@]}"
    "${EXTRA_PACKAGES[@]}")
if [[ $DRIVER_MODE == *dkms ]]; then
    packages+=("kernel-devel-${kver}" "kernel-devel-matched-${kver}" "kernel-headers-${kver}" dkms gcc make elfutils-libelf-devel)
fi
printf '%s\n' "${packages[@]}" > /bundle/requested-packages.txt

# --alldeps is essential: bootstrap/installed packages must not hide dependencies.
# The final clean-image build below is the actual dependency-closure check.
dnf --releasever="$VERSION_ID" download --resolve --alldeps --archlist=x86_64,noarch \
    --setopt=install_weak_deps=False --destdir=/bundle/repo "${packages[@]}"

# CUDA can publish the same toolkit NEVRA with different RPM bytes. Fetch the
# four toolkit RPMs explicitly from the repository whose signed metadata we keep.
mkdir -p /bundle/toolkit-packages
dnf --releasever="$VERSION_ID" --disablerepo='*' --enablerepo=nvidia-container-toolkit \
    download --archlist=x86_64,noarch --destdir=/bundle/toolkit-packages "${toolkit_packages[@]}"

python3 /offline-tools/collect-keys.py
rpm -qp --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' /bundle/repo/*.rpm | LC_ALL=C sort > /bundle/packages.txt
python3 /offline-tools/toolkit-repo.py
shopt -s nullglob
keys=(/bundle/keys/*.asc)
rpms=(/bundle/repo/*.rpm)
[[ ${#keys[@]} -gt 0 && ${#rpms[@]} -gt 0 ]] || die 'No signing keys or RPMs were collected.'
rpm --import "${keys[@]}"
for package in "${rpms[@]}"; do
    # rpmkeys can report digest-only success for unsigned RPMs; require a signature too.
    result=$(LC_ALL=C rpmkeys --checksig "$package")
    [[ $result == *'signatures OK'* ]] || die "Missing or invalid RPM signature: $package ($result)"
done
createrepo_c /bundle/repo
cp /offline-tools/{common.sh,install.sh} /bundle/
# Emit only the installation settings, excluding downloader credentials/configuration.
{
    printf 'DRIVER_VERSION=%q\n' "$DRIVER_VERSION"
    printf 'TOOLKIT_VERSION=%q\n' "$TOOLKIT_VERSION"
    printf 'DRIVER_MODE=%q\n' "$DRIVER_MODE"
} > /bundle/settings.sh
cd /bundle
find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS
