# Build NVIDIA on an air-gapped custom RHEL bootc image

This workflow downloads and verifies the clean RHEL/NVIDIA inputs on a connected
machine, transfers them into the isolated network, creates a custom RHEL image
there, and installs NVIDIA onto that custom image with `build-custom.sh`.

The custom image may add files, configuration, services, and RPM packages. It
must preserve the original kernel and every RPM from the transferred base at the
original version. Do not run `dnf upgrade`, remove packages, replace base RPMs,
or install another kernel. The script checks this before it changes anything.

Examples use rootful Podman in the air-gapped environment. Use the same Podman
user for every command. If you deliberately use rootless Podman, omit `sudo`
throughout. Docker is also supported by setting `CONTAINER_ENGINE=docker`.

## Part 1: prepare everything on the connected machine

### 1. Configure the source image and NVIDIA versions

From the project root, create the local configuration if it does not exist:

```bash
cp nvidia-offline/config.example.sh nvidia-offline/config.sh
```

Edit `nvidia-offline/config.sh`:

```bash
BASE_IMAGE='registry.redhat.io/rhel9/rhel-bootc:YOUR_VERSION'
CONTAINER_ENGINE='podman'

# Leave empty when Podman already receives entitlement access from the host.
# Otherwise specify a registered image with the same RPM inventory as BASE_IMAGE.
DOWNLOAD_IMAGE=''

DRIVER_VERSION='YOUR_EXACT_DRIVER_VERSION'
TOOLKIT_VERSION='YOUR_EXACT_TOOLKIT_VERSION_AND_RPM_RELEASE'
DRIVER_MODE='open-dkms'
KMOD_NEVRA=''
EXTRA_PACKAGES=()
SAVE_BASE_IMAGE=1
```

`BASE_IMAGE` is the clean image saved for transfer. `DOWNLOAD_IMAGE` is used only
while downloading RPMs. Keep subscription credentials out of `BASE_IMAGE`.
`EXTRA_PACKAGES=()` keeps the connected bundle independent of later custom
packages; those packages are added to the custom image inside the isolated network.

### 2. Download and verify the offline bundle

Log in and pull the clean base explicitly. Substitute your selected tag:

```bash
podman login registry.redhat.io
podman pull registry.redhat.io/rhel9/rhel-bootc:YOUR_VERSION
```

List the available NVIDIA versions and the base-image kernel:

```bash
bash nvidia-offline/bundle.sh inspect nvidia-offline/config.sh
```

After setting exact versions in `config.sh`, prepare a new output directory:

```bash
bash nvidia-offline/bundle.sh prepare \
  nvidia-offline/config.sh \
  nvidia-offline/dist/release-01
```

Preparation downloads NVIDIA and required dependency RPMs, verifies their
signatures or signed repository metadata, installs them onto a clean copy of the
base with networking disabled, compiles the driver for the image kernel, rebuilds
initramfs, and runs `bootc container lint`. Continue only after it prints
`Verified bundle:`.

### 3. Create the directory to transfer

```bash
bash nvidia-offline/package-transfer.sh \
  nvidia-offline/dist/release-01 \
  transfer-release-01
```

Transfer the entire `transfer-release-01/` directory through the approved
process. It includes:

- `offline/base-image.tar`: the clean RHEL bootc image;
- `offline/nvidia-offline.tar.gz`: NVIDIA RPMs, dependencies, keys, and installer;
- `Containerfile`: the offline NVIDIA image recipe;
- `build-custom.sh`: the automated custom-base workflow;
- checksum files and documentation.

## Part 2: create the custom image in the air-gapped environment

### 4. Verify and load the transferred base

```bash
cd /path/to/transfer-release-01
sha256sum --check --strict SHA256SUMS
(
  cd offline
  sha256sum --check --strict TRANSFER.SHA256SUMS
)

sudo podman load -i offline/base-image.tar
cat offline/base-image.txt
cat offline/source-image.txt
```

The loaded name looks like
`localhost/nvidia-offline-base:IMAGE_ID_PREFIX`. It is the clean RHEL image named
in `source-image.txt`, retagged locally so the offline build never pulls it.

### 5. Add your configuration and packages

Use the loaded image in your custom Containerfile. For example:

```dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

COPY files/ /

# This is allowed only when the packages are additional packages. They must come
# from RPM content already available inside the isolated environment.
# RUN dnf -y install YOUR_ADDITIONAL_PACKAGES && dnf clean all

RUN bootc container lint
```

Build and tag it. Adjust the context and Containerfile paths:

```bash
sudo podman build \
  --pull=never \
  --build-arg "BASE_IMAGE=$(cat offline/base-image.txt)" \
  -f /path/to/custom-context/Containerfile \
  -t localhost/rhel9-custom-bootc:release-01 \
  /path/to/custom-context
```

Allowed changes include new configuration, new files, enabled services, and new
RPM packages. Do not upgrade, downgrade, reinstall with a different build, or
remove any original package. Do not change the kernel.

## Part 3: install NVIDIA onto the custom image

### 6. Run the automated build

The script requires two environment variables:

| Variable | Meaning |
| --- | --- |
| `CUSTOM_BASE_IMAGE` | Custom image reference to use as `FROM`; it may be local or pulled from a reachable registry. |
| `OUTPUT_IMAGE` | New tag for the completed custom + NVIDIA image; it must not already exist. |

Optional variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CONTAINER_ENGINE` | `podman` | Use `podman` or `docker`. |
| `PULL_CUSTOM_BASE` | `0` | Set to `1` to pull `CUSTOM_BASE_IMAGE` before validation and building. |
| `BUILD_CONTEXT` | Automatically created | A new absolute path for temporary extracted files. It must not already exist. |
| `TMPDIR` | `/var/tmp` | Parent used for an automatically created `BUILD_CONTEXT`. |

If the custom image is already in the local image store, use automatic temporary
directory creation and keep pulls disabled:

```bash
sudo env \
  CUSTOM_BASE_IMAGE=localhost/rhel9-custom-bootc:release-01 \
  OUTPUT_IMAGE=localhost/rhel9-custom-nvidia-bootc:release-01 \
  CONTAINER_ENGINE=podman \
  PULL_CUSTOM_BASE=0 \
  bash build-custom.sh
```

To obtain the custom base from a registry reachable inside the isolated network,
log in first and enable the explicit pull. Use your real registry and image path:

```bash
sudo podman login registry.internal

sudo env \
  CUSTOM_BASE_IMAGE=registry.internal/team/rhel9-custom-bootc:release-01 \
  OUTPUT_IMAGE=localhost/rhel9-custom-nvidia-bootc:release-01 \
  CONTAINER_ENGINE=podman \
  PULL_CUSTOM_BASE=1 \
  bash build-custom.sh
```

An internet-hosted registry is reachable only if the environment is not actually
air-gapped. In an isolated network, use an internal registry or load the image
archive locally and leave `PULL_CUSTOM_BASE=0`.

To choose the temporary path yourself, pass `BUILD_CONTEXT` through `sudo env`:

```bash
sudo env \
  CUSTOM_BASE_IMAGE=localhost/rhel9-custom-bootc:release-01 \
  OUTPUT_IMAGE=localhost/rhel9-custom-nvidia-bootc:release-01 \
  CONTAINER_ENGINE=podman \
  PULL_CUSTOM_BASE=0 \
  BUILD_CONTEXT=/var/tmp/nvidia-custom-release-01 \
  bash build-custom.sh
```

Use a new output tag and build-context path for every attempt. The input and
output image variables must have different values. `sudo env` is significant:
it passes these named variables into the root shell that runs the script.

### 7. Understand each automated step

`build-custom.sh` prints seven numbered steps and fails immediately if any command
or validation fails:

1. **Check the transfer.** It verifies `SHA256SUMS` and the inner
   `offline/TRANSFER.SHA256SUMS` before using any archive or image.
2. **Obtain and check the image.** With `PULL_CUSTOM_BASE=1`, it explicitly pulls
   `CUSTOM_BASE_IMAGE` using the selected engine and existing registry login.
   Otherwise it requires the image locally. It then confirms that `OUTPUT_IMAGE`
   does not exist and that the input and output names differ.
3. **Create and verify `BUILD_CONTEXT`.** It extracts the NVIDIA bundle into a
   unique disposable directory and verifies the bundle's internal checksums.
   If you supplied `BUILD_CONTEXT`, the script refuses an existing, relative, or
   dangerously broad path.
4. **Compare RPM inventories.** It records every installed custom-base RPM. It
   uses `comm` to prove that every original package and exact version still
   exists. Added entries are accepted. A removed, upgraded, or downgraded package
   stops the build and is printed in the error output.
5. **Adapt the disposable inventory.** It copies the accepted custom inventory
   into the extracted working bundle and regenerates that copy's checksums. The
   original transferred archive, RPMs, and signed metadata remain unchanged.
6. **Build offline.** It builds from `CUSTOM_BASE_IMAGE` with image pulls and
   networking disabled. For Podman, `label=disable` applies only to temporary
   build containers so SELinux can read the mounted bundle. The resulting OS
   keeps its normal SELinux configuration. The installer verifies RPM content,
   compiles NVIDIA modules for the image kernel, checks versions and vermagic,
   rebuilds initramfs, configures CDI refresh, and runs `bootc container lint`.
   The command labels the result with the verified bundle values as
   `nvidia.drivers_version` and `nvidia.containers_toolkit_version`.
7. **Confirm the output.** It checks that `OUTPUT_IMAGE` now exists and prints
   the commands to run after booting it on GPU hardware.

The exit trap removes `BUILD_CONTEXT` after success or failure. This includes a
path supplied by you, but only after the script proves that it did not exist and
creates it itself. The original transfer directory and custom input image are
never modified.

### Failure rules

The script stops without producing an approved output when:

- a required variable, command, transfer file, or checksum is missing;
- the custom image is unavailable locally or the output tag already exists;
- any original RPM is missing or has a different version;
- the kernel differs, which the NVIDIA installer verifies during the build;
- an RPM signature, dependency transaction, DKMS compilation, module check,
  initramfs generation, or bootc lint check fails.

If the script rejects the RPM inventory, rebuild the custom image without package
removal or replacement. Do not disable the inventory check.

## Part 4: deploy, verify, and clean up

### 8. Publish and test the finished image

The example result is:

```text
localhost/rhel9-custom-nvidia-bootc:release-01
```

Inspect the version labels without starting a container:

```bash
sudo podman image inspect \
  --format '{{ index .Config.Labels "nvidia.drivers_version" }}' \
  localhost/rhel9-custom-nvidia-bootc:release-01

sudo podman image inspect \
  --format '{{ index .Config.Labels "nvidia.containers_toolkit_version" }}' \
  localhost/rhel9-custom-nvidia-bootc:release-01
```

Tag and push it to a registry reachable by the target machines, or pass it to
your normal offline bootc image/ISO provisioning workflow. After booting the GPU
machine, verify both the driver and CDI devices:

```bash
nvidia-smi
nvidia-ctk cdi list
```

Secure Boot requires the target to trust the module-signing key. Keep the old OS
image until the new image has booted and passed these checks.

### 9. Cleanup behavior

The script automatically deletes its `BUILD_CONTEXT` on success and failure.
There are no inventory files in `/tmp` to remove. Keep the unchanged transfer
directory for rebuilding or auditing.

If an interrupted Buildah process leaves a busy mount, first confirm that no build
is running, then use the same Podman user that ran the build:

```bash
sudo podman unmount --all
```

Future driver, toolkit, clean-base, or kernel updates require a new bundle from
the connected machine. Repeat the workflow with new release names, input tags,
and output tags.
