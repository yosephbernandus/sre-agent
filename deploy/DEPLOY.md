# Deploy - CodeCraft SRE Agent on EC2

Runbook for hosting the agent on the hackathon AWS account. Self-contained.

## Context (hackathon account `190512372740`, region `us-east-1`)

| Thing | Value |
|-------|-------|
| AWS CLI profile | `hackathon` |
| AMI (AL2023 x86_64) | `ami-08f44e8eca9095668` |
| Instance type | `t3.small` |
| Key pair | `ft-oncall` → `deploy/ft-oncall.pem` (created in Step 0) |
| Security group | `sg-08ab6f6015c0195a5` (`:22`→your IP, `:80`→world) |
| Subnet (default, public) | `subnet-07a91a17a5b4d871a` |
| systemd service | `codecraft` (port 80) |

> **Auth model:** the hackathon user cannot use an EC2 instance role (`iam:PassRole`
> is denied) and SSM is denied. So the box authenticates to Bedrock with the AWS
> keys in `app.env`, and access is over SSH only.

Set once per shell (run commands from the `sre-agent/` directory):
```bash
export AWS_PROFILE=hackathon AWS_REGION=us-east-1
PEM=deploy/ft-oncall.pem        # created/placed in Step 0 below
```

---

## Step 0 - SSH key pair + security group (one-time)

Create the key pair. The `.pem` private key is only downloadable at creation, so
this writes it to `deploy/ft-oncall.pem` (gitignored). If a key named `ft-oncall`
already exists, delete it first - its private half can't be re-downloaded.
```bash
aws ec2 delete-key-pair --key-name ft-oncall 2>/dev/null
aws ec2 create-key-pair --key-name ft-oncall \
  --query KeyMaterial --output text > deploy/ft-oncall.pem
chmod 600 deploy/ft-oncall.pem
```

**Security group** (`sg-08ab6f6015c0195a5` already exists for this project). To
recreate from scratch:
```bash
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text)
MYIP=$(curl -s https://checkip.amazonaws.com)
SG=$(aws ec2 create-security-group --group-name ft-oncall-sg \
  --description "codecraft sre" --vpc-id $VPC --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 22 --cidr ${MYIP}/32
aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 80 --cidr 0.0.0.0/0
echo "SG=$SG   # use this id in Step 1"
```
> Get your AWS access keys (for `app.env`): IAM console → your user → Security
> credentials → Create access key → CLI.

---

## Step 1 - launch instance
```bash
MYIP=$(curl -s https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress --group-id sg-08ab6f6015c0195a5 \
  --protocol tcp --port 22 --cidr ${MYIP}/32 2>/dev/null || true   # ok if exists

IID=$(aws ec2 run-instances --image-id ami-08f44e8eca9095668 --instance-type t3.small \
  --key-name ft-oncall --security-group-ids sg-08ab6f6015c0195a5 \
  --subnet-id subnet-07a91a17a5b4d871a --associate-public-ip-address \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=codecraft-sre},{Key=project,Value=codecraft}]' \
  --query "Instances[0].InstanceId" --output text)
aws ec2 wait instance-running --instance-ids $IID
DNS=$(aws ec2 describe-instances --instance-ids $IID \
  --query "Reservations[0].Instances[0].PublicDnsName" --output text)
echo "instance=$IID  url=http://$DNS"
```

## Step 2 - ship code + env
```bash
cd sre-agent
zip -qr /tmp/sre.zip app templates scripts requirements.txt -x '*/__pycache__/*'
scp -i $PEM -o StrictHostKeyChecking=accept-new /tmp/sre.zip ec2-user@$DNS:/home/ec2-user/sre.zip
scp -i $PEM app.env ec2-user@$DNS:/home/ec2-user/app.env       # secrets - over SSH only, never S3/git
```

## Step 3 - provision on the box (over SSH)
```bash
ssh -i $PEM ec2-user@$DNS 'bash -s' <<'REMOTE'
set -e
sudo dnf install -y python3.11 python3.11-pip unzip
mkdir -p ~/sre-agent && cd ~/sre-agent && unzip -oq ~/sre.zip
cp ~/app.env ~/sre-agent/app.env
python3.11 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
sudo tee /etc/systemd/system/codecraft.service >/dev/null <<'UNIT'
[Unit]
Description=CodeCraft SRE Agent
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=/home/ec2-user/sre-agent
ExecStart=/home/ec2-user/sre-agent/.venv/bin/uvicorn app.server:app --host 0.0.0.0 --port 80 --workers 1 --proxy-headers --timeout-keep-alive 30
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now codecraft
sleep 4
systemctl is-active codecraft && curl -s localhost/healthz && echo
REMOTE
```
(The systemd unit is also kept at `deploy/codecraft.service`.)

## Step 4 - open it
```
http://<PublicDnsName>      # e.g. http://ec2-98-93-5-140.compute-1.amazonaws.com
```

## Step 5 - (optional) seed Datadog + create dashboard/monitors
Run locally or on the box (same Datadog us5 account):
```bash
.venv/bin/python scripts/seed_telemetry.py
.venv/bin/python scripts/create_dashboard.py
.venv/bin/python scripts/create_monitors.py
```

---

## Updating code later (fresh terminal - nothing cached)

From a terminal where no env vars are set, this is the full, self-contained
redeploy. No secrets in these commands (instance id / pem path / profile name
are not secrets):
```bash
export AWS_PROFILE=hackathon AWS_REGION=us-east-1
cd <repo>/jkt-hackathon-2026/sre-agent

# find the running instance BY TAG (no hardcoded id - survives relaunch) + its IP
IP=$(aws ec2 describe-instances \
     --filters "Name=tag:Name,Values=codecraft-sre" "Name=instance-state-name,Values=running" \
     --query "Reservations[0].Instances[0].PublicIpAddress" --output text)

# deploy (PEM = wherever your ft-oncall.pem lives; copy it to deploy/ via Step 0)
PEM=deploy/ft-oncall.pem IP=$IP bash deploy/deploy.sh
```

## From a brand-new laptop (first-time setup)
Secrets are NOT in the repo. On a new machine you must re-supply them:
1. **AWS profile** - `aws configure --profile hackathon` (paste the hackathon
   access key/secret from your password manager; never commit them).
2. **SSH key** - you need `ft-oncall.pem`. The private key can't be
   re-downloaded; if you don't have it, create a fresh key pair (Step 0) and
   relaunch, or copy the `.pem` from a secure backup to `deploy/ft-oncall.pem`
   (`chmod 600`).
3. **`app.env`** - `cp app.env.template app.env` then fill the real values
   (DD keys, AWS keys, `SLACK_WEBHOOK_URL`, `FT_TRIAGE_TOKEN`) from your
   password manager. `app.env` is gitignored - keep it that way.
4. Then run the redeploy block above. (If the instance was terminated, do
   Step 1 to relaunch first.)

> Secrets live only in: `app.env` (on disk, gitignored), `~/.aws/credentials`
> (the `hackathon` profile), and `deploy/ft-oncall.pem`. None are in git or this
> doc. Rotate DD keys + the Slack webhook after the event.

## Debugging
```bash
ssh -i $PEM ec2-user@$DNS 'journalctl -u codecraft -n 60 --no-pager -l'
```

## Teardown (protect the $50)
```bash
# resolve the instance by tag (no hardcoded id)
IID=$(aws ec2 describe-instances \
      --filters "Name=tag:Name,Values=codecraft-sre" "Name=instance-state-name,Values=running,stopped" \
      --query "Reservations[0].Instances[0].InstanceId" --output text)

aws ec2 stop-instances      --instance-ids $IID   # pause (~free); IP/DNS change on restart
aws ec2 terminate-instances --instance-ids $IID   # delete when fully done
```

## Gotchas
1. **`app.env` = plain `KEY=value`** (no `export`, no inline `#`). Read by `load_dotenv`.
2. **Public IP/DNS changes on stop→start.** Re-fetch `PublicDnsName`; re-point SG `:22` at your current IP.
3. **`iam:PassRole` + `ssm:*` denied** → no instance role (keys in `app.env`), SSH only.
4. **No HTTPS without a domain** - CloudFront/ECS denied on this account. Use the EC2 public DNS (http) for the demo.
5. **Rotate `DD_API_KEY` / `DD_APP_KEY`** after the event.
