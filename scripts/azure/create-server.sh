#!/usr/bin/env bash
# One Azure VM for one client's Knovas stack, hardened, in one command.
#
#   ./scripts/azure/create-server.sh --name acme --ssh-from 203.0.113.7/32 \
#       [--https-from 198.51.100.4/32]    who may open the Platform (default: Internet)
#       [--location switzerlandnorth] [--size Standard_D4as_v5]
#       [--ssh-key ~/.ssh/id_ed25519.pub] [--subscription <id or name>]
#
# Creates, in resource group rg-knovas-<name>:
#   - a VNet and an NSG: 443 from --https-from, 80 from the internet (Let's
#     Encrypt only -- nginx answers nothing there but a redirect), 22 from
#     --ssh-from (your office). Nothing else inbound; no link to any other VNet.
#   - an Ubuntu 24.04 VM: Trusted Launch (secure boot, vTPM), encryption at
#     host, Premium SSD, SSH key only, Microsoft Entra ID login for admins.
#     cloud-init installs Docker, nginx, certbot and automatic security updates
#     and clones KnovasComponents to /opt/knovas.
#   - a Recovery Services vault with daily VM backups.
#
# Re-runnable: what already exists is kept. Needs the Azure CLI, logged in
# (az login) with Owner or Contributor + User Access Administrator on the
# subscription. See docs/azure-server.md for what to do afterwards.
set -euo pipefail

NAME="" SSH_FROM="" HTTPS_FROM="Internet" LOCATION="switzerlandnorth"
SIZE="Standard_D4as_v5" SSH_KEY="$HOME/.ssh/id_ed25519.pub" SUBSCRIPTION=""
ADMIN_USER="knovas"

usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="${2:-}"; shift 2 ;;
    --ssh-from) SSH_FROM="${2:-}"; shift 2 ;;
    --https-from) HTTPS_FROM="${2:-}"; shift 2 ;;
    --location) LOCATION="${2:-}"; shift 2 ;;
    --size) SIZE="${2:-}"; shift 2 ;;
    --ssh-key) SSH_KEY="${2:-}"; shift 2 ;;
    --subscription) SUBSCRIPTION="${2:-}"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done
[[ "$NAME" =~ ^[a-z0-9-]{2,20}$ ]] || { echo "--name: 2-20 lowercase letters, digits or '-' (e.g. acme)" >&2; exit 1; }
[[ -n "$SSH_FROM" ]] || { echo "--ssh-from is required: the address you administer from, e.g. 203.0.113.7/32" >&2; exit 1; }
[[ "$SSH_FROM" != "Internet" && "$SSH_FROM" != "*" && "$SSH_FROM" != "0.0.0.0/0" ]] \
  || { echo "--ssh-from must be your office address, not the whole internet." >&2; exit 1; }
[[ -f "$SSH_KEY" ]] || { echo "No SSH public key at $SSH_KEY (ssh-keygen -t ed25519, or --ssh-key)" >&2; exit 1; }
command -v az >/dev/null || { echo "Azure CLI not found: https://aka.ms/installazurecli" >&2; exit 1; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLOUD_INIT="$ROOT_DIR/scripts/azure/cloud-init.yaml"
RG="rg-knovas-$NAME" VM="vm-knovas-$NAME" NSG="nsg-knovas-$NAME"
VNET="vnet-knovas-$NAME" SUBNET="snet-vm" VAULT="rsv-knovas-$NAME"

step() { printf '\n==> %s\n' "$*"; }

if [[ -n "$SUBSCRIPTION" ]]; then
  az account set --subscription "$SUBSCRIPTION"
fi
az account show --query '{subscription:name, id:id}' -o tsv >/dev/null \
  || { echo "Not logged in: az login" >&2; exit 1; }
echo "Subscription: $(az account show --query name -o tsv)   Region: $LOCATION   VM: $VM ($SIZE)"

step "Encryption at host (one-time per subscription)"
state="$(az feature show --namespace Microsoft.Compute --name EncryptionAtHost --query properties.state -o tsv 2>/dev/null || true)"
if [[ "$state" != "Registered" ]]; then
  az feature register --namespace Microsoft.Compute --name EncryptionAtHost -o none
  echo "    registering -- this takes a few minutes, once"
  for _ in $(seq 60); do
    state="$(az feature show --namespace Microsoft.Compute --name EncryptionAtHost --query properties.state -o tsv)"
    [[ "$state" == "Registered" ]] && break
    sleep 20
  done
  [[ "$state" == "Registered" ]] || { echo "Still '$state'. Re-run this script in a few minutes." >&2; exit 1; }
  az provider register --namespace Microsoft.Compute -o none
fi
echo "    registered"

step "Resource group $RG"
az group create -n "$RG" -l "$LOCATION" -o none

step "Network: $VNET, firewall $NSG"
az network nsg create -g "$RG" -n "$NSG" -l "$LOCATION" -o none
az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-https --priority 100 \
  --protocol Tcp --destination-port-ranges 443 --source-address-prefixes "$HTTPS_FROM" -o none
az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-http-acme --priority 110 \
  --protocol Tcp --destination-port-ranges 80 --source-address-prefixes Internet -o none
az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-ssh-admin --priority 120 \
  --protocol Tcp --destination-port-ranges 22 --source-address-prefixes "$SSH_FROM" -o none
az network vnet create -g "$RG" -n "$VNET" -l "$LOCATION" --address-prefixes 10.60.0.0/24 \
  --subnet-name "$SUBNET" --subnet-prefixes 10.60.0.0/27 --network-security-group "$NSG" -o none

step "VM $VM"
if az vm show -g "$RG" -n "$VM" -o none 2>/dev/null; then
  echo "    exists -- kept as it is"
else
  az vm create -g "$RG" -n "$VM" -l "$LOCATION" \
    --image Canonical:ubuntu-24_04-lts:server:latest --size "$SIZE" \
    --os-disk-size-gb 64 --storage-sku Premium_LRS \
    --vnet-name "$VNET" --subnet "$SUBNET" --nsg "" --public-ip-sku Standard \
    --security-type TrustedLaunch --enable-secure-boot true --enable-vtpm true \
    --encryption-at-host true \
    --admin-username "$ADMIN_USER" --ssh-key-values "$SSH_KEY" \
    --custom-data "$CLOUD_INIT" -o none
fi

step "Microsoft Entra ID login for administrators"
az vm extension set -g "$RG" --vm-name "$VM" -n AADSSHLoginForLinux \
  --publisher Microsoft.Azure.ActiveDirectory -o none
me="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)"
if [[ -n "$me" ]]; then
  az role assignment create --assignee-object-id "$me" --assignee-principal-type User \
    --role "Virtual Machine Administrator Login" \
    --scope "$(az vm show -g "$RG" -n "$VM" --query id -o tsv)" -o none 2>/dev/null || true
  echo "    you may log in with: az ssh vm -g $RG -n $VM"
fi

step "Daily backups ($VAULT)"
az backup vault create -g "$RG" -n "$VAULT" -l "$LOCATION" -o none
if ! az backup item list -g "$RG" --vault-name "$VAULT" --query "[?properties.friendlyName=='$VM']" -o tsv 2>/dev/null | grep -q .; then
  az backup protection enable-for-vm -g "$RG" --vault-name "$VAULT" --vm "$VM" \
    --policy-name DefaultPolicy -o none
fi

IP="$(az vm show -d -g "$RG" -n "$VM" --query publicIps -o tsv)"
cat <<EOF

Done. Public IP: $IP

Next (docs/azure-server.md):
  1. DNS: point your Platform name (e.g. $NAME.knovas.ch) at $IP.
  2. Copy the client's certificates to the server:
       scp client-cert.pem client-key.pem ca-root.pem $ADMIN_USER@$IP:/opt/knovas/certs/
  3. ssh $ADMIN_USER@$IP, then in /opt/knovas: knovas.env, setup, start, HTTPS.
     First boot installs Docker for ~5 minutes; /var/lib/cloud/instance/knovas-ready
     appears when it is done.
EOF
