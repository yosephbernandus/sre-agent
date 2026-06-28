#!/usr/bin/env bash
# One-shot ship + restart for the CodeCraft SRE Agent on EC2.
#   Usage: PEM=path/to/ft-oncall.pem IP=<public-ip-or-dns> bash deploy/deploy.sh
# Ships code (not app.env — that's set once at provision time, see DEPLOY.md).
set -euo pipefail

IP="${IP:?set IP=<ec2 public ip or dns>}"
PEM="${PEM:?set PEM=path to ft-oncall.pem}"
SSH_USER="${SSH_USER:-ec2-user}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo ">> packaging $HERE"
cd "$HERE"
rm -f /tmp/sre.zip
zip -qr /tmp/sre.zip app templates scripts requirements.txt -x '*/__pycache__/*'

echo ">> shipping to $SSH_USER@$IP"
scp -q -i "$PEM" -o StrictHostKeyChecking=accept-new /tmp/sre.zip "$SSH_USER@$IP:/home/$SSH_USER/sre.zip"

echo ">> unpack + restart"
ssh -i "$PEM" "$SSH_USER@$IP" '
  cd ~/sre-agent && unzip -oq ~/sre.zip
  .venv/bin/pip install -q -r requirements.txt
  sudo systemctl restart codecraft && sleep 3
  echo "status: $(systemctl is-active codecraft)  health: $(curl -s localhost/healthz)"
'
echo ">> done: http://$IP"
