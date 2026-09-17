#!/usr/bin/env bash
set -euo pipefail
usage() { echo "Usage: bash install.sh [--bootc | --host | --verify-only]"; }
mode=${1:---bootc}
case $mode in
    --help|-h) usage; exit 0 ;;
    --bootc|--host|--verify-only) ;;
    *) usage >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { usage >&2; exit 2; }
bundle=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$bundle"
# Verify before reading the settings or invoking the package manager.
sha256sum --check --strict SHA256SUMS
[[ $mode == --verify-only ]] && exit 0
source "$bundle/common.sh"
source "$bundle/settings.sh"
[[ $EUID == 0 ]] || die 'Run as root (or in a Containerfile RUN instruction).'
check_platform
[[ ! -e /run/ostree-booted ]] || die 'Install into a new bootc image, then deploy it; do not modify a running bootc host.'
kver=$(cat "$bundle/kernel.txt")
if [[ $mode == --bootc ]]; then
    # BuildKit does not always provide .dockerenv or .containerenv. The build
    # recipe explicitly declares its context; live ostree hosts are still denied.
    [[ ${NVIDIA_OFFLINE_IMAGE_BUILD:-0} == 1 || -e /run/.containerenv || -e /.dockerenv || ${container:-} ]] ||
        die '--bootc must run inside an image build/container (set NVIDIA_OFFLINE_IMAGE_BUILD=1 in a BuildKit RUN instruction).'
    [[ $(image_kernel) == "$kver" ]] || die 'Image kernel differs from the bundle; prepare a new bundle.'
    current_inventory=$(package_inventory)
    [[ $current_inventory == "$(cat "$bundle/base-packages.txt")" ]] ||
        die 'Base RPM inventory differs from the downloaded base. Install the bundle immediately after FROM, using the saved base image.'
else
    [[ $(uname -r) == "$kver" ]] || die 'Host must be booted into the exact kernel recorded in kernel.txt.'
    target_version=$(bash -c 'source "$1"; printf "%s" "$VERSION_ID"' bash "$bundle/target-os-release")
    [[ $VERSION_ID == "$target_version" ]] || die 'Host RHEL minor version differs from the bundle.'
fi

# A private reposdir plus disabled plugins prevents subscription/network plugins
# and cached host repositories from participating in the offline transaction.
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/repos" "$work/cache" "$work/persist"
cat > "$work/repos/offline.repo" <<EOF
[nvidia-offline]
name=Verified NVIDIA offline bundle
baseurl=file://$bundle/repo
enabled=1
gpgcheck=1
repo_gpgcheck=0
module_hotfixes=1

[nvidia-offline-toolkit]
name=NVIDIA Container Toolkit (original signed metadata)
baseurl=file://$bundle/toolkit-repo
enabled=1
gpgcheck=0
repo_gpgcheck=1
gpgkey=file://$bundle/keys/nvidia-container-toolkit.asc
EOF
shopt -s nullglob
keys=("$bundle"/keys/*.asc)
rpms=("$bundle"/repo/*.rpm)
[[ ${#keys[@]} -gt 0 && ${#rpms[@]} -gt 0 ]] || die 'Bundle contains no keys or RPMs.'
rpm --import "${keys[@]}"
for package in "${rpms[@]}"; do
    result=$(LC_ALL=C rpmkeys --checksig "$package")
    [[ $result == *'signatures OK'* ]] || die "Missing or invalid RPM signature: $package"
done
mapfile -t packages < "$bundle/requested-packages.txt"
dnf -y --noplugins --disablerepo='*' --enablerepo=nvidia-offline,nvidia-offline-toolkit \
    --setopt="reposdir=$work/repos" --setopt="cachedir=$work/cache" \
    --setopt="persistdir=$work/persist" --setopt=install_weak_deps=False \
    --exclude=kernel --exclude=kernel-core --exclude=kernel-modules \
    --exclude=kernel-modules-core --exclude=kernel-modules-extra \
    install "${packages[@]}"

for package in nvidia-driver-cuda nvidia-driver-cuda-libs; do
    [[ $(rpm -q --qf '%{VERSION}' "$package") == "$DRIVER_VERSION" ]] || die "Driver version mismatch: $package"
done
for package in nvidia-container-toolkit nvidia-container-toolkit-base libnvidia-container-tools libnvidia-container1; do
    [[ $(rpm -q --qf '%{VERSION}-%{RELEASE}' "$package") == "$TOOLKIT_VERSION" ]] || die "Toolkit version mismatch: $package"
done
if [[ $DRIVER_MODE == *dkms ]]; then
    # uname -r would return the builder HOST kernel and is wrong for bootc.
    dkms autoinstall -k "$kver"
    if [[ $mode == --bootc && -f /usr/lib/systemd/system/dkms.service ]]; then
        systemctl mask dkms.service
    fi
fi
depmod -a "$kver"
for module in nvidia nvidia_uvm nvidia_modeset nvidia_drm; do
    [[ $(modinfo -k "$kver" -F version "$module") == "$DRIVER_VERSION" ]] || die "Wrong or missing module: $module"
    vermagic=$(modinfo -k "$kver" -F vermagic "$module")
    [[ ${vermagic%% *} == "$kver" ]] || die "Module $module was built for a different kernel: $vermagic"
    module_path=$(readlink -f "$(modinfo -k "$kver" -F filename "$module")")
    [[ $module_path == /usr/lib/modules/"$kver"/* ]] || die "Module is not stored in the image kernel tree: $module_path"
done

mkdir -p /usr/lib/modprobe.d /usr/lib/modules-load.d /usr/lib/dracut/dracut.conf.d
cat > /usr/lib/modprobe.d/nvidia-offline.conf <<'EOF'
blacklist nouveau
options nouveau modeset=0
EOF
printf 'nvidia\nnvidia_uvm\n' > /usr/lib/modules-load.d/nvidia-offline.conf
cat > /usr/lib/dracut/dracut.conf.d/90-nvidia-offline.conf <<'EOF'
omit_drivers+=" nouveau "
add_drivers+=" nvidia nvidia_uvm nvidia_modeset nvidia_drm "
EOF
if [[ $mode == --bootc ]]; then
    mkdir -p /usr/lib/bootc/kargs.d
    cat > /usr/lib/bootc/kargs.d/90-nvidia.toml <<'EOF'
kargs = ["rd.driver.blacklist=nouveau", "modprobe.blacklist=nouveau"]
EOF
    dracut --force --no-hostonly "/usr/lib/modules/$kver/initramfs.img" "$kver"
else
    dracut --force --no-hostonly "/boot/initramfs-$kver.img" "$kver"
fi

# Discover hardware only at boot. nvidia-smi initializes device nodes before CDI.
if [[ -f /usr/lib/systemd/system/nvidia-cdi-refresh.service ]]; then
    mkdir -p /etc/systemd/system/nvidia-cdi-refresh.service.d
    cat > /etc/systemd/system/nvidia-cdi-refresh.service.d/offline.conf <<'EOF'
[Unit]
After=systemd-modules-load.service
[Service]
ExecStartPre=/usr/bin/nvidia-smi
EOF
    systemctl enable nvidia-cdi-refresh.service nvidia-cdi-refresh.path
else
    cat > /etc/systemd/system/nvidia-offline-cdi.service <<'EOF'
[Unit]
Description=Generate NVIDIA CDI devices after loading GPU drivers
After=systemd-modules-load.service
Before=multi-user.target
[Service]
Type=oneshot
RuntimeDirectory=cdi
RuntimeDirectoryPreserve=yes
ExecStartPre=/usr/bin/nvidia-smi
ExecStart=/usr/bin/nvidia-ctk cdi generate --output=/run/cdi/nvidia.yaml
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
    systemctl enable nvidia-offline-cdi.service
fi
# Keep a small version inventory in the OS, without embedding the RPM archive.
mkdir -p /usr/share/nvidia-offline
cp "$bundle/"{settings.sh,kernel.txt,packages.txt} /usr/share/nvidia-offline/
echo "Installed NVIDIA $DRIVER_VERSION and toolkit $TOOLKIT_VERSION for kernel $kver."
[[ $mode != --host ]] || echo 'Reboot before running GPU workloads.'
