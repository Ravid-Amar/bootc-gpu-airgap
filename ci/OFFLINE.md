# Run without internet access

This setup uses **internal GitLab, an image registry, S3-compatible storage, and
a cluster**. Only the preparation machine needs internet access. The same three
pipeline jobs support both HTTP-readable RPM repositories and private S3 buckets.

Images go in the internal registry. RPMs and repository metadata go in S3.

## 1. Prepare on the connected machine

Prepare the NVIDIA/VM RPM bundle using [step 2 of the setup guide](README.md#2-prepare-the-vm-package-bundle).
It includes the clean RHEL base archive, NVIDIA packages, `cloud-init`, and the
guest agent. The older NVIDIA-only bundle is insufficient.

Also load your tested bootc-image-builder and application images into rootful
Podman. Log in to their source registries and pull them on this machine as needed.
Tag them with the names you will use offline. For example, replace these source
tags and the internal hostname with yours:

```bash
sudo podman tag registry.redhat.io/rhel9/bootc-image-builder:YOUR_VERSION localhost/bootc-image-builder:offline
sudo podman tag SOURCE_REGISTRY/team/baseline:YOUR_VERSION registry.internal/apps/baseline:v1
sudo podman tag SOURCE_REGISTRY/team/workload:YOUR_VERSION registry.internal/apps/workload:v1

bash ci/offline-images.sh save build/offline-images \
  localhost/bootc-image-builder:offline \
  registry.internal/apps/baseline:v1 \
  registry.internal/apps/workload:v1
```

The helper saves local images and checksums. It never pulls or pushes. Use a new
output directory each time. It uses Podman's [multi-image archive format](https://docs.podman.io/en/latest/markdown/podman-save.1.html).

Transfer these to the offline network:

- The project source, including `.gitlab-ci.yml`, `ci/`, and `nvidia-offline/`.
- The complete VM RPM bundle, including its base image archive and checksums.
- The complete `build/offline-images/` directory.
- The packages/tools needed to provision the shell runner, as listed in the setup guide.
- A matching RHEL RPM repository snapshot, uploaded to S3 as described below.

## 2. Load inside the offline network

Put the VM RPM bundle at `/srv/bootc/offline/` on each base-build runner.
Load the image archive on each golden-build runner:

```bash
bash ci/offline-images.sh load /path/to/offline-images
```

This verifies the transfer checksums before loading. Keep the builder in this
rootful Podman store. Publish the application images once to your internal registry:

```bash
sudo podman login registry.internal
sudo podman push registry.internal/apps/baseline:v1
sudo podman push registry.internal/apps/workload:v1
```

These pushes use only the internal network. The VMs pull these application images
at boot; putting the images in the runner's store alone does not put them in a VM.

## 3. Store RPM content in S3

Keep two separate prefixes:

```text
s3://bootc/releases/v1/bundle/
  nvidia-offline.tar.gz
  base-image.tar
  base-image.txt
  base-image-id.txt
  TRANSFER.SHA256SUMS
  ...other bundle files...

s3://bootc/releases/v1/repos/
  BaseOS/repodata/repomd.xml
  BaseOS/...RPMs and remaining metadata...
  AppStream/repodata/repomd.xml
  AppStream/...RPMs and remaining metadata...
```

The first prefix is the output of the existing NVIDIA bundle tool. The second is
RPM content for disk creation: **loose RPMs alone are insufficient**. On a
connected, entitled RHEL preparation machine, mirror the repositories matching
your base image and tested builder. For example, substitute your repository IDs:

```bash
dnf reposync --repoid=YOUR_BASEOS_REPO --download-metadata --norepopath --download-path=build/rpm-repos/BaseOS
dnf reposync --repoid=YOUR_APPSTREAM_REPO --download-metadata --norepopath --download-path=build/rpm-repos/AppStream
```

Add further repositories if required. Keep all metadata, including module
metadata, and the RPM paths it references. Avoid `--newest-only` with an unchanged
metadata snapshot. See the [DNF reposync reference](https://dnf-plugins-core.readthedocs.io/en/latest/reposync.html).

On a transfer machine that can reach the internal S3 service, upload both prefixes
using your AWS CLI profile or credentials:

```bash
aws --endpoint-url https://s3.internal s3 sync nvidia-offline/dist/gpu-vm/ s3://bootc/releases/v1/bundle/ --exclude 'nvidia-offline/*'
aws --endpoint-url https://s3.internal s3 sync build/rpm-repos/ s3://bootc/releases/v1/repos/
```

Use a new, complete release prefix before starting a pipeline. Do not modify a
snapshot while jobs are reading it. Full repository snapshots can be large.

For the base job, either keep using `OFFLINE_DIR`, or set:

```text
OFFLINE_S3_URI=s3://bootc/releases/v1/bundle/
S3_ENDPOINT_URL=https://s3.internal
```

With `OFFLINE_S3_URI`, the runner downloads into a fresh directory and verifies
`TRANSFER.SHA256SUMS` before loading or building anything.

### Option A: HTTP/HTTPS-readable repository objects

Allow read-only HTTP access to the repository objects within your internal
network. Set this to the actual object URL prefix (path-style or virtual-hosted
style, according to your S3 service):

```text
RPM_REPO_BASEURL=https://s3.internal/bootc/releases/v1/repos
```

The builder reads `BaseOS/repodata/repomd.xml`, `AppStream/repodata/repomd.xml`, and
their RPMs directly. The generated `.repo` file keeps package signature and TLS
verification enabled. No S3 credentials are needed for these reads. Leave
`RPM_REPO_S3_URI` unset. This mode does not make the bucket public on the internet.

### Option B: Private repository bucket

Install AWS CLI on the golden-build runner and set:

```text
RPM_REPO_S3_URI=s3://bootc/releases/v1/repos/
S3_ENDPOINT_URL=https://s3.internal
BIB_NETWORK=host
```

Leave `RPM_REPO_BASEURL` unset. Supply a runner AWS profile, or protected/masked
GitLab variables `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and, if needed,
`AWS_SESSION_TOKEN`. Set the region required by your S3 service through
`AWS_DEFAULT_REGION`. Grant prefix-scoped listing and object-read access. The
script uses [AWS CLI sync with a custom endpoint](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html).

The golden job downloads a fresh snapshot, checks that each repository has
`repodata/repomd.xml`, and starts a temporary HTTP server for the builder. It
stops that server and removes the downloaded snapshot when the build exits.
S3 credentials stay on the runner; they are not copied into images or manifests.

By default, the server binds to `127.0.0.1` on an available port. If your tested
builder uses a separate network namespace for dependency resolution, set
`RPM_REPO_HOST` to the runner's internal IPv4 address reachable from its containers.
Set `RPM_REPO_PORT` to a fixed available port if your internal firewall needs one.
The server binds only to that address; allow access from the builder. It serves
only the downloaded RPM snapshot, without S3 credentials.

The generated private-mode repository URL is temporary build configuration. It
is not a permanent RPM update endpoint inside booted VMs. Container workloads
and bootc image updates use your internal image registry.

### Settings shared by both options

- `RPM_REPO_NAMES`: space-separated directory names, default `BaseOS AppStream`.
- `RPM_REPO_GPGKEY`: signing-key URL, default
  `file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release`. For other signers, place
  their trusted key in the base image and point this variable to it.
- Put private CA certificates in
  `ci/base/rootfs/etc/pki/ca-trust/source/anchors/`. Configure trust on the runner
  and cluster nodes too; use `AWS_CA_BUNDLE` for the AWS CLI when needed.

Both modes replace inherited `.repo` files in the golden image with the selected
repositories. If you use neither variable, provide your own internal `.repo`
files under `ci/base/rootfs/etc/yum.repos.d/`.

The NVIDIA bundle alone is not a full set of disk-build inputs. Include all
packages needed by your exact builder, architecture, and RHEL release. See
[Red Hat's disconnected build guidance](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html-single/using_image_mode_for_rhel_to_build_deploy_and_manage_operating_systems/index).

## 4. Run the same pipeline

Set the normal [GitLab variables](README.md#4-set-gitlab-variables-and-run), using
your internal endpoints:

```text
BIB_IMAGE=localhost/bootc-image-builder:offline
BIB_NETWORK=host
BASELINE_IMAGE=registry.internal/apps/baseline:v1
WORKLOAD_IMAGE=registry.internal/apps/workload:v1
```

Use internal registry credentials and cluster kubeconfig. If GitLab's registry
is not the target registry, also set `REGISTRY_HOST`, `IMAGE_PREFIX`,
`REGISTRY_USER`, and `REGISTRY_PASSWORD`.

`BIB_NETWORK=host` lets disk creation reach S3 over HTTP or the temporary RPM server. It does not
require internet, and it does not block internet by itself: run on the isolated
network or a runner whose egress allows only your internal services. Image build
steps already use `--network=none`; the builder image uses `--pull=never`.

Run the pipeline with external egress blocked, then check cloud-init, both guest
containers, and the GPU as described in the setup guide. A successful local test
suite does not replace this environment check.

The isolated network still needs the internal registry and S3 service. This is
an internet-disconnected workflow; it is not a files-only workflow with all
networking disabled. A full image build and VM boot against your S3 service
remain the final verification step.
