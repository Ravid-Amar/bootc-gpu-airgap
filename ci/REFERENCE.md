# GitLab → NVIDIA bootc → golden VM

For the setup steps, start with [the short guide](README.md). This page contains
the complete configuration and operational details.

The pipeline has two build jobs and one deployment job for an air-gapped Linux
shell runner. It uses your internal registry to pass images between jobs, so
jobs can run on different prepared runners. No external CI includes or job
executor images are needed.

```text
Saved RHEL base + verified offline RPM bundle
  → build_nvidia_base: NVIDIA + cloud-init + environment customization
  → internal registry: nvidia-base@sha256:...
  → build_golden_containerdisk: baked baseline.container → final bootc image
      → bootc-image-builder → qcow2 → scratch containerDisk → internal registry
  → deploy_vm_template: reusable template pinned to the containerDisk digest
      → optional new VM → persistent root PVC + per-VM cloud-init Secret
          → injected workload.container + config.yaml + registry auth
```

OpenShift Virtualization is the default. Its reusable object is a
`template.openshift.io/v1` **Template containing a KubeVirt VirtualMachine**.
There is no assumed `VirtualMachineTemplate` CRD. Set `VM_PLATFORM=kubevirt`
for standalone KubeVirt: the job publishes a reusable VM JSON in a ConfigMap,
and the same renderer creates concrete instances.

## 1. Prepare the additional offline packages

**The previously created transfer bundle is sufficient for its original NVIDIA
build, but does not contain cloud-init. This new VM workflow requires a fresh
bundle.** The pipeline checks this before building.

On the connected preparation machine, from the repository root:

```bash
# First review the base image, versions, and registered download image settings.
bash nvidia-offline/bundle.sh inspect ci/nvidia-vm.config.sh
bash nvidia-offline/bundle.sh prepare ci/nvidia-vm.config.sh nvidia-offline/dist/gpu-vm
```

[nvidia-vm.config.sh](nvidia-vm.config.sh) adds `cloud-init` and `qemu-guest-agent`
to the existing NVIDIA/Podman packages. Add any other environment RPMs to
`EXTRA_PACKAGES` before preparing. The output directory must be new. Use your
existing entitlement setup; these commands have not downloaded a new bundle
as part of adding this pipeline.

Transfer the contents of `nvidia-offline/dist/gpu-vm/` to `/srv/bootc/offline/`
on each runner that may execute the base job, or point `OFFLINE_DIR` to a shared
readable copy. Keep the archives, image reference files, verification recipe,
and `TRANSFER.SHA256SUMS`. The pipeline extracts the RPM archive itself.
The multi-gigabyte archives stay outside Git; `.gitignore` excludes them.

## 2. Prepare the shell runner and cluster

Use a dedicated Linux x86_64 GitLab **Shell executor** tagged `bootc-shell`.
Install Bash, Podman, Python 3.9+, GNU tar/coreutils, grep, `qemu-img`, and
`kubectl`. GitLab needs its normal runner/Git prerequisites. No Python libraries
are required at runtime. Tests additionally use Python's standard library.

Builds use **rootful Podman**. By default the runner account invokes
`sudo -n podman`, so configure its noninteractive access on the dedicated build
host. This capability is effectively root access; restrict the runner to trusted
projects/protected branches. If the runner itself runs as root, set
`PODMAN_USE_SUDO=0`. Rootless storage is not used by this workflow.

Preload your existing bootc-image-builder image into that same rootful store:

```bash
sudo podman image inspect localhost/bootc-image-builder:offline
```

Set `BIB_IMAGE` to the actual local tag or digest. The job uses `--pull=never`.
Use [the offline setup](OFFLINE.md) to save and load builder/application images.
The invocation targets the RHEL 9 bootc-image-builder CLI with `--local`,
`--type qcow2`, `--rootfs xfs`, and `--chown`. Confirm these against the image
already present at your site. It mounts the standard rootful storage at
`/var/lib/containers/storage`. Install the required `osbuild-selinux` policy on
SELinux hosts. Allow privileged containers and sufficient disk space; plan for
80 GB or more free for RPM extraction, OS images, qcow2, and caches, then measure
your actual peak. Runner maximum job timeout must permit the configured jobs.
See the [bootc-image-builder prerequisites and invocation](https://github.com/osbuild/bootc-image-builder).

Air-gapped does not mean disconnected from the internal network. Both build
jobs push to the internal registry. Disk creation uses `BIB_NETWORK=host` so
the builder can access **internal RPM content**. Set `RPM_REPO_BASEURL` to an
HTTP-readable S3 repository prefix, or `RPM_REPO_S3_URI` for a private snapshot
downloaded and served temporarily by the runner. See [both S3 options](OFFLINE.md).
Having the builder image alone does not guarantee all disk-build inputs are
cached. Verify that the snapshot covers the installed builder and RHEL release.
Set `BIB_NETWORK=none` only after making all required build inputs available
locally. See [Red Hat's disconnected build guidance](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html-single/using_image_mode_for_rhel_to_build_deploy_and_manage_operating_systems/index).

The target cluster needs OpenShift Virtualization or KubeVirt **with CDI**, a
working storage class/profile, and enough PVC capacity for the qcow2 virtual
size. Nodes must resolve and trust the internal registry. Create the target
namespace in advance. The kubeconfig identity needs access there to get the
namespace; get/apply Templates (OpenShift) or ConfigMaps (standalone); and, when
creating VMs, get/create VMs, get/create Secrets, watch VMs, and create
DataVolumes/PVCs through the cluster's supported VM provisioning permissions.

For GPU operation, the cluster must already expose and permit a **PCI passthrough**
GPU resource. Set `GPU_RESOURCE_NAME` to that exact name. The existing open-driver
bundle targets supported Turing-or-newer physical GPUs, not NVIDIA vGPU guest
drivers. Cluster device configuration is outside this guest-image pipeline.
See [KubeVirt host device assignment](https://kubevirt.io/user-guide/compute/host-devices/).

## 3. Customize the base and baked Quadlet

Edit [base/Containerfile](base/Containerfile). Put environment files beneath
`ci/base/rootfs/` using the target filesystem layout, for example:

```text
ci/base/rootfs/etc/pki/ca-trust/source/anchors/internal-registry.crt
ci/base/rootfs/etc/yum.repos.d/internal-mirrors.repo
ci/base/rootfs/etc/containers/registries.conf.d/10-internal.conf
```

Use real internal mirror URLs and trusted signing keys in your repo files.
Remove/disable any unwanted upstream repositories in your customization steps.
Keep credentials out of this tree. Additional packages belong in the offline
bundle, because the base build runs with `--network=none`.

The NVIDIA installer must remain immediately after `FROM`: it verifies the
original RPM inventory and compiles against the bundled kernel. Environment
customization follows it in the same image. Changing the kernel/base needs a
new verified bundle. The base also enables cloud-init, SSH, and the guest agent.

Edit [golden/baseline.container](golden/baseline.container) for the container
every VM should have. `BASELINE_IMAGE` replaces its image marker during the
golden build. This Quadlet is baked under `/usr/share/containers/systemd/`.
It starts after cloud-init supplies `/etc/containers/registry-auth.json`.
Mirror this application image separately; copying a Quadlet does not embed its
container layers. Both baseline and workload images are pulled inside the guest.

Edit [vm/workload.container](vm/workload.container) for the per-VM workload.
The renderer injects it under `/etc/containers/systemd/workload.container`, with
the VM's chosen image and optional GPU settings. It mounts the injected config
at `/etc/workload/config.yaml`; change this path or add `Exec=` to match your app.
You can override the source with `WORKLOAD_QUADLET_FILE` (a repository path or
GitLab File variable). Keep its cloud-final ordering, auth path, and `[Install]`
behavior unless you intentionally implement another startup design.

Quadlet generates the corresponding services at boot and daemon reload; the
pipeline does not run `systemctl enable` on generated services. Cloud-init
queues their start with `--no-block` to avoid waiting on services ordered after
cloud-final itself. GPU mode adds the NVIDIA CDI device and disables SELinux
label separation for that workload; adapt the isolation policy for your app.
See [the Podman 5.2 Quadlet reference](https://docs.podman.io/en/v5.2.2/markdown/podman-systemd.unit.5.html).

## 4. Set GitLab CI/CD variables

Configure these under **Settings → CI/CD → Variables**. Use protected variables
for credentials, and do not enable `CI_DEBUG_TRACE` for credential-bearing jobs.
Registry and cluster actions happen when you run this pipeline.

| Variable | Value / purpose |
| --- | --- |
| `REGISTRY_HOST` | Internal registry hostname, optionally with port. Defaults to `CI_REGISTRY`. |
| `IMAGE_PREFIX` | Repository prefix such as `registry.internal/platform/bootc`. Defaults to `CI_REGISTRY_IMAGE`. |
| `REGISTRY_USER`, `REGISTRY_PASSWORD` | Push credentials; password masked/protected. Default to GitLab's job registry credentials. |
| `BASELINE_IMAGE` | Required internal image reference for the baked Quadlet; prefer a digest. |
| `OFFLINE_DIR` | Prepared bundle directory on the base runner; default `/srv/bootc/offline`. |
| `OFFLINE_S3_URI` | Optional `s3://bucket/prefix/` containing the verified bundle. Takes precedence over `OFFLINE_DIR`. |
| `S3_ENDPOINT_URL` | Internal S3-compatible endpoint used by AWS CLI downloads. |
| `RPM_REPO_BASEURL` | HTTP/HTTPS-readable prefix containing the RPM repository directories. Mutually exclusive with `RPM_REPO_S3_URI`. |
| `RPM_REPO_S3_URI` | Private S3 snapshot prefix. Downloads with runner credentials and serves the RPMs locally during the golden build. |
| `RPM_REPO_NAMES` | Space-separated repository directory names; default `BaseOS AppStream`. |
| `RPM_REPO_GPGKEY` | Package signing-key URL; default `file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release`. |
| `RPM_REPO_HOST`, `RPM_REPO_PORT` | Private-mode HTTP server binding; default `127.0.0.1` and an available port. Use a reachable internal runner IPv4 address when needed. |
| `BIB_IMAGE` | Already loaded bootc-image-builder image; pin your tested version. |
| `BIB_CONFIG` | Optional path to disk build settings, default `ci/image-builder/config.toml`. |
| `KUBECONFIG_FILE` | **File** variable holding cluster kubeconfig; or set `KUBECONFIG` as a File variable directly. |
| `VM_PLATFORM` | `openshift` (default) or `kubevirt`. |
| `VM_NAMESPACE` | Existing namespace; default `golden-images`. |
| `TEMPLATE_NAME` | Reusable Template/ConfigMap name; default `rhel95-nvidia-golden`. |
| `VM_CPU_CORES`, `VM_MEMORY`, `VM_DISK_SIZE` | Defaults `4`, `16Gi`, `40Gi`. PVC must be larger than the disk's virtual size. |
| `VM_STORAGE_CLASS` | Omit for the cluster default; otherwise a working CDI storage class. |
| `GPU_RESOURCE_NAME` | Exact permitted passthrough resource; empty means no GPU attachment and no GPU options in the injected Quadlet. |
| `DEPLOY_VM` | `false`: publish reusable definition only (default). `true`: also create and start a new VM. |

For `DEPLOY_VM=true`, also set:

| Variable | Value / purpose |
| --- | --- |
| `VM_NAME` | Unique instance name; default `nvidia-worker-01`. Existing VMs are not overwritten. |
| `DISK_PULL_SECRET` | Existing Kubernetes `kubernetes.io/dockerconfigjson` Secret name for pulling the containerDisk; default `containerdisk-pull`. |
| `WORKLOAD_IMAGE` | Actual internal application image for the injected Quadlet. |
| `WORKLOAD_AUTH_FILE` | Protected **File** variable containing Podman/Docker auth JSON with inline `auths`; use a long-lived, pull-only robot credential. It must cover both guest images. |
| `WORKLOAD_CONFIG_FILE` | **File** variable containing your application's configuration. |
| `VM_SSH_KEY_FILE` | **File** variable containing one SSH **public** key. |
| `VM_USER` | Guest admin account; default `vmadmin`, key-only login and passwordless sudo. |
| `CLOUD_INIT_SECRET` | Optional unique Secret name; defaults to `<VM_NAME>-cloudinit`. Must not already exist. |
| `WORKLOAD_CA_FILE` | Optional **File** variable containing a PEM registry CA to trust inside the guest. |
| `VM_READY_TIMEOUT` | Optional wait timeout, default `20m`. VM Ready does not prove application readiness. |

Create `DISK_PULL_SECRET` once with your cluster administrator using a local
pull-only auth JSON file (the file content is not placed on the command line):

```bash
kubectl -n golden-images create secret generic containerdisk-pull \
  --type=kubernetes.io/dockerconfigjson \
  --from-file=.dockerconfigjson=/secure/path/containerdisk-auth.json
```

There are three separate authentication uses: CI pushes, cluster nodes pull the
containerDisk, and guest Podman pulls applications. Do not inject GitLab's
short-lived job token into the guest. The pipeline passes guest credentials in
a Kubernetes Secret and writes their guest auth file with mode `0600`; it never
includes them in the golden image, dotenv reports, or exported manifests.
Cloud-init data remains sensitive in Kubernetes and in the guest's cloud-init
cache; use appropriate namespace/Secret access controls and rotate the robot
credential through your guest-management process.

## 5. Run and use the pipeline

Commit `.gitlab-ci.yml` and `ci/` to your GitLab project. Configure the runner
and variables, then run on the default branch or through **Run pipeline**.
Tags include commit, pipeline, and job IDs. Published image **digests** pass
between jobs via small dotenv artifacts; never store credentials in those
reports. See [GitLab dotenv artifacts](https://docs.gitlab.com/ci/variables/dotenv_variables/).

The jobs produce:

1. `nvidia-base:<tag>` with environment files, NVIDIA, Podman, and cloud-init.
2. `golden-bootc:<tag>` with the baked Quadlet, plus `containerdisk:<tag>` containing
   the generated qcow2. The qcow2 is checked with `qemu-img check` and checked for
   backing-file dependencies. Its checksum is a small job artifact.
3. A published OpenShift Template or standalone reusable ConfigMap, and optionally
   a running VM. `build/template.json` / `build/vm.json` are safe manifest artifacts.

The qcow2 and temporary build contexts remain under that runner checkout's
`build/` until normal checkout/runner cleanup. They are not uploaded as huge
GitLab artifacts; the durable distribution artifact is the registry containerDisk.
Manage Podman cache/image retention on the dedicated runner separately. The
resource group serializes Podman builds in this project; do not share the same
storage with unrelated simultaneous BIB jobs from other projects.

The disk container uses `FROM scratch`, places qcow2 at `/disk/disk.qcow2`, and
makes it readable by UID 107. Each VM imports it with CDI into its own persistent
root PVC. Changes survive a VM stop/start; publishing a new template does not
replace an existing PVC. See [KubeVirt disk formats](https://kubevirt.io/user-guide/storage/disks_and_volumes/)
and [CDI registry imports](https://github.com/kubevirt/containerized-data-importer/blob/main/doc/image-from-registry.md).

To create another VM without rebuilding, download `build/golden.env` from the
successful job, set `CONTAINERDISK_REF` to its recorded digest, provide the
per-VM variables above, and run `bash ci/deploy.sh` with `DEPLOY_VM=true` from
the repository root. Use a new `VM_NAME` and cloud-init Secret. The script
renders a fresh VM and imports a fresh root PVC using the same golden artifact.
It validates local VM inputs and checks for existing VM/Secret names before
publishing the template. Guest Secrets are created, never overwritten. If VM
creation fails after its Secret was created, use a new `CLOUD_INIT_SECRET` on
retry, or have your administrator remove the unused Secret after checking it
is not referenced by a VM.
Alternatively, after creating a matching cloud-init Secret, process the stored
OpenShift Template:

```bash
oc process -n golden-images rhel95-nvidia-golden \
  -p VM_NAME=nvidia-worker-02 \
  -p CLOUD_INIT_SECRET=nvidia-worker-02-cloudinit \
  | oc create -f -
```

The template carries the GPU/storage settings selected when published. For
standalone KubeVirt, rerun the renderer with each instance's environment instead
of expecting OpenShift parameter processing. KubeVirt consumes the Secret's
`userdata` key through `cloudInitNoCloud.secretRef`; see [KubeVirt cloud-init](https://kubevirt.io/user-guide/user_workloads/startup_scripts/).

## 6. Verify a booted VM

```bash
kubectl -n golden-images get vm,vmi,dv,pvc
virtctl -n golden-images console nvidia-worker-01
```

For SSH, use your cluster's networking or `virtctl ssh` with the injected key.
Inside the guest:

```bash
sudo cloud-init status --wait --long
sudo systemctl status baseline.service workload.service --no-pager
sudo journalctl -u workload.service -b --no-pager
sudo podman ps
nvidia-smi
nvidia-ctk cdi list
```

Check GPU commands only when a GPU was assigned. The template uses UEFI with
Secure Boot disabled because the existing DKMS module signing key is not trusted
by a new VM. Enabling Secure Boot requires a separate signed-module/key-enrollment
workflow. PVC-backed cloud-init normally runs once for a new instance; changing
the Secret does not automatically reconfigure an already initialized guest.
Use a new VM for a fresh golden release, or manage existing guests and their
bootc updates explicitly. Guest registry auth here serves Quadlets; bootc OS
update credentials are a separate lifecycle concern.

## Local checks and current verification limits

```bash
python3 -m unittest discover -s ci/tests -v
for script in ci/*.sh; do bash -n "$script" || exit; done
```

Tests check template rendering, persistent-disk/Secret references, safe config
and auth round trips, GPU selection, and real Quadlet generation when the host
generator is available. The final GitLab configuration should also be validated
with your GitLab instance's CI Lint. A full build and VM boot require the fresh
cloud-init bundle, your internal registry/mirrors, installed BIB version, cluster
credentials, storage, and GPU resource; those site-specific checks cannot be
completed from the supplied workspace alone.
