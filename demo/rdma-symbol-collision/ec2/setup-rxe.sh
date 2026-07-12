#!/usr/bin/env bash
# Set up two soft-RoCE (rxe) devices on the EC2 box so "wrong device" is literal:
#   rxe_train  on the real NIC   (ens5)
#   rxe_store  on a dummy netdev (rxedummy0)
# Idempotent-ish: safe to re-run.
set -uo pipefail

NIC="${NIC:-ens5}"

sudo modprobe rdma_rxe
sudo modprobe dummy

# second netdev so the two rxe devices are genuinely distinct
sudo ip link add rxedummy0 type dummy 2>/dev/null || true
sudo ip addr add 10.99.0.1/24 dev rxedummy0 2>/dev/null || true
sudo ip link set rxedummy0 up

sudo rdma link add rxe_train type rxe netdev "$NIC" 2>/dev/null || true
sudo rdma link add rxe_store type rxe netdev rxedummy0 2>/dev/null || true

echo "=== rdma link ==="
rdma link
echo "=== ibv_devices ==="
ibv_devices
