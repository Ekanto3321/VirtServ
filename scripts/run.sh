#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
command_exists(){ command -v "$1" >/dev/null 2>&1; }
if ! command_exists sudo; then echo "sudo is required" >&2; exit 1; fi
sudo apt update -y
sudo apt install -y python3 python3-venv python3-pip pkg-config libvirt-dev python3-dev build-essential libvirt-daemon-system libvirt-clients novnc websockify dnsmasq-base iptables
if [ ! -d .venv ]; then python3 -m venv .venv; fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
export PYTHONPATH="$PROJECT_ROOT"
uvicorn app.main:app --host "${APP_HOST:-0.0.0.0}" --port "${APP_PORT:-8088}" --reload
