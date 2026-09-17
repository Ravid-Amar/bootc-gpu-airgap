#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
export VM_NAMESPACE=${VM_NAMESPACE:-golden-images}
export VM_NAME=${VM_NAME:-nvidia-worker-01}
export DISK_PULL_SECRET=${DISK_PULL_SECRET:-containerdisk-pull}
export CLOUD_INIT_SECRET=${CLOUD_INIT_SECRET:-$VM_NAME-cloudinit}
case ${VM_PLATFORM:-openshift} in
    openshift|kubevirt) ;;
    *) die 'VM_PLATFORM must be openshift or kubevirt.' ;;
esac
case ${DEPLOY_VM:-false} in
    false|true) ;;
    *) die 'DEPLOY_VM must be true or false.' ;;
esac
require CONTAINERDISK_REF
if [[ -n ${KUBECONFIG_FILE:-} ]]; then
    export KUBECONFIG=$KUBECONFIG_FILE
fi
require KUBECONFIG
[[ -f $KUBECONFIG ]] || die 'KUBECONFIG must point to the GitLab File variable containing cluster credentials.'
# Validate local inputs before changing anything in the cluster.
if [[ ${VM_PLATFORM:-openshift} == openshift ]]; then
    python3 ci/render.py template --output build/template.json
else
    python3 ci/render.py vm --output build/vm.json
fi
if [[ ${DEPLOY_VM:-false} == true ]]; then
    require WORKLOAD_AUTH_FILE WORKLOAD_CONFIG_FILE VM_SSH_KEY_FILE WORKLOAD_IMAGE
    python3 ci/render.py cloudinit-secret >/dev/null
    python3 ci/render.py vm --output build/vm.json
fi
kubectl get namespace "$VM_NAMESPACE" >/dev/null
if [[ ${DEPLOY_VM:-false} == true ]]; then
    kubectl -n "$VM_NAMESPACE" get secret "$DISK_PULL_SECRET" >/dev/null
    existing=$(kubectl -n "$VM_NAMESPACE" get virtualmachine "$VM_NAME" --ignore-not-found -o name)
    [[ -z $existing ]] || die "VM $VM_NAME already exists. Choose a new VM_NAME."
    existing=$(kubectl -n "$VM_NAMESPACE" get secret "$CLOUD_INIT_SECRET" --ignore-not-found -o name)
    [[ -z $existing ]] || die "Secret $CLOUD_INIT_SECRET already exists. Choose a new CLOUD_INIT_SECRET; existing guest credentials are never overwritten."
    kubectl create --dry-run=server -f build/vm.json >/dev/null
fi
case ${VM_PLATFORM:-openshift} in
    openshift)
        kubectl apply --server-side --field-manager=bootc-pipeline -f build/template.json
        ;;
    kubevirt)
        # Standalone KubeVirt has no VirtualMachineTemplate API. Publish a reusable
        # VM specification in a ConfigMap; each instance gets its own root PVC.
        kubectl -n "$VM_NAMESPACE" create configmap "${TEMPLATE_NAME:-rhel95-nvidia-golden}" \
            --from-file=vm.json=build/vm.json --dry-run=client -o json > build/template.json
        kubectl apply --server-side --field-manager=bootc-pipeline -f build/template.json
        ;;
esac

if [[ ${DEPLOY_VM:-false} == false ]]; then
    echo 'Reusable VM definition published. Set DEPLOY_VM=true to create an instance with cloud-init.'
    exit 0
fi
# No secret data in tracked files, dotenv reports, build context, or job output.
# Create also rejects name collisions if another deployment races this one.
python3 ci/render.py cloudinit-secret | \
    kubectl create -f - >/dev/null
kubectl create -f build/vm.json
kubectl -n "$VM_NAMESPACE" wait --for=condition=Ready "vm/$VM_NAME" --timeout="${VM_READY_TIMEOUT:-20m}"
echo "VM $VM_NAME is Ready. Check cloud-init and the workload inside the guest (ci/README.md)."
