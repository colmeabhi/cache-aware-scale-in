#!/usr/bin/env bash
# Provider "manual": the machine already exists. You give GPU_SSH_HOST.
# Every provider script defines these three functions.

provider_create() {            # -> prints an instance id
    [ -n "${GPU_SSH_HOST:-}" ] || { echo "manual provider needs GPU_SSH_HOST" >&2; return 1; }
    echo "manual:$GPU_SSH_HOST"
}

provider_wait_ip() {           # provider_wait_ip ID -> prints the SSH host/IP once reachable
    echo "$GPU_SSH_HOST"
}

provider_destroy() {           # provider_destroy ID
    echo "manual provider: not destroying $1 (stop it yourself to save credits)" >&2
}
