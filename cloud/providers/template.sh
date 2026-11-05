#!/usr/bin/env bash
# Template for a new provider (copy to cloud/providers/<name>.sh, set GPU_PROVIDER=<name>).
# Fill in the three functions with the provider's REST API or CLI. The rest of
# the pipeline only needs an SSH-reachable Ubuntu box with NVIDIA drivers.
#
# Contract:
#   provider_create          creates a machine with >= 3 GPUs (A100 80GB preferred),
#                            injects the public key of $GPU_SSH_KEY, prints its instance id
#   provider_wait_ip ID      blocks until the machine has an address, prints it
#   provider_destroy ID      terminates it (called on exit, even on failure, unless KEEP_INSTANCE=1)
#
# Environment available: GPU_PROVIDER_API_KEY, GPU_SSH_KEY, GPU_SSH_USER,
#                        GPU_TYPE (optional), GPU_REGION (optional)
#
# Example shape with a JSON REST API (adapt paths and fields to the real docs):

API="${PROVIDER_API_URL:-https://api.example.com/v1}"
auth=(-H "Authorization: Bearer $GPU_PROVIDER_API_KEY" -H "Content-Type: application/json")

provider_create() {
    local pubkey; pubkey=$(cat "${GPU_SSH_KEY%.pub}.pub" 2>/dev/null || ssh-keygen -y -f "$GPU_SSH_KEY")
    curl -fsS "${auth[@]}" -X POST "$API/instances" \
        -d "$(jq -n --arg t "${GPU_TYPE:-a100-80gb}" --arg k "$pubkey" --arg r "${GPU_REGION:-}" \
              '{gpu_type: $t, gpu_count: 3, image: "ubuntu-22.04-cuda", ssh_public_key: $k, region: $r}')" \
        | jq -r '.id'
}

provider_wait_ip() {
    local ip=""
    for _ in $(seq 1 120); do
        ip=$(curl -fsS "${auth[@]}" "$API/instances/$1" | jq -r '.ip // empty')
        [ -n "$ip" ] && { echo "$ip"; return 0; }
        sleep 10
    done
    echo "instance $1 never got an IP" >&2; return 1
}

provider_destroy() {
    curl -fsS "${auth[@]}" -X DELETE "$API/instances/$1" >/dev/null
}
