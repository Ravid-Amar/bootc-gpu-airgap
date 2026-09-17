#!/usr/bin/env bash

die() { echo "ERROR: $*" >&2; exit 1; }

validate_config() {
    [[ ${BASE_IMAGE:-} ]] || die 'Set BASE_IMAGE in the configuration.'
    case ${CONTAINER_ENGINE:-podman} in
        podman|docker) ;;
        *) die 'CONTAINER_ENGINE must be podman or docker.' ;;
    esac
    case ${DRIVER_MODE:-} in
        open-dkms|proprietary-dkms|precompiled) ;;
        *) die 'DRIVER_MODE must be open-dkms, proprietary-dkms, or precompiled.' ;;
    esac
    [[ ${SAVE_BASE_IMAGE:-1} =~ ^[01]$ ]] || die 'SAVE_BASE_IMAGE must be 0 or 1.'
    [[ ${1:-prepare} == inspect ]] && return 0
    [[ ${DRIVER_VERSION:-} =~ ^[0-9]+(\.[0-9]+)+$ ]] || die 'Set an exact DRIVER_VERSION, for example 580.95.05.'
    [[ ${TOOLKIT_VERSION:-} =~ ^[0-9]+\.[0-9]+\.[0-9]+-[[:alnum:]._+]+$ ]] || die 'Set an exact TOOLKIT_VERSION including RPM release, for example 1.18.0-1.'
    local toolkit_major=${TOOLKIT_VERSION%%.*} toolkit_rest=${TOOLKIT_VERSION#*.}
    local toolkit_minor=${toolkit_rest%%.*}
    (( 10#$toolkit_major > 1 || (10#$toolkit_major == 1 && 10#$toolkit_minor >= 12) )) ||
        die 'CDI requires Container Toolkit 1.12 or newer.'
    if [[ $DRIVER_MODE == precompiled ]]; then
        [[ ${KMOD_NEVRA:-} =~ ^kmod-nvidia-[[:alnum:]._:+-]+\.x86_64$ && $KMOD_NEVRA != *dkms* ]] ||
            die 'Precompiled mode requires an exact kmod-nvidia RPM NEVRA ending in .x86_64.'
    fi
    local package
    for package in "${EXTRA_PACKAGES[@]}"; do
        [[ $package =~ ^[[:alnum:]][[:alnum:]._:+-]*$ ]] || die "Invalid extra package: $package"
        [[ $package != kernel && $package != kernel-* && $package != *nvidia* && $package != cuda-* ]] ||
            die "Configure kernels and NVIDIA packages through the dedicated settings: $package"
    done
}

check_platform() {
    # shellcheck disable=SC1091
    source /etc/os-release
    [[ $ID == rhel && ${VERSION_ID%%.*} == 9 ]] || die 'This workflow currently supports RHEL 9 only.'
    [[ $(rpm --eval '%{_arch}') == x86_64 ]] || die 'This workflow currently supports x86_64 only.'
}

image_kernel() {
    local kernels=()
    mapfile -t kernels < <(rpm -q kernel-core --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n')
    [[ ${#kernels[@]} == 1 && -f /usr/lib/modules/${kernels[0]}/vmlinuz ]] ||
        die 'Expected exactly one installed kernel-core with /usr/lib/modules/<version>/vmlinuz.'
    printf '%s\n' "${kernels[0]}"
}

package_inventory() {
    rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort | sed '/^gpg-pubkey-/d'
}
