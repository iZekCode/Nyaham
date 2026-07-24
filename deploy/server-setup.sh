#!/usr/bin/env bash
# One-shot environment bootstrap for a FRESH Ubuntu VM (Oracle Cloud Ampere ARM
# or any x86 box). Installs Docker + the compose plugin and preps the host so
# `docker compose ... up -d --build` just works.
#
# It does NOT fetch your code or secrets — those come after (see DEPLOY.md /
# the provisioning guide). Polling mode needs NO inbound ports, so no firewall
# changes are required here.
#
# Usage (on the server, as the default 'ubuntu' user):
#   curl -fsSL <raw-url>/server-setup.sh | bash      # if the repo is reachable
#   # or scp this file over and:  bash server-setup.sh
set -euo pipefail

echo "==> Updating base packages"
sudo apt-get update -y
sudo apt-get upgrade -y

echo "==> Installing prerequisites"
sudo apt-get install -y ca-certificates curl git

if command -v docker >/dev/null 2>&1; then
  echo "==> Docker already installed ($(docker --version))"
else
  echo "==> Installing Docker Engine + compose plugin (official script)"
  curl -fsSL https://get.docker.com | sudo sh
fi

echo "==> Enabling Docker to start on boot"
sudo systemctl enable --now docker

echo "==> Adding '$USER' to the docker group (run docker without sudo)"
sudo usermod -aG docker "$USER" || true

# Oracle Ubuntu images ship a restrictive iptables INPUT policy. The bot uses
# OUTBOUND connections only (Telegram + Yahoo), so no ports need opening. If you
# later switch to webhook mode you'll need to open 443 in BOTH the OCI Security
# List AND here.

cat <<'NEXT'

==============================================================
Docker is installed. Log out and back in (or run: newgrp docker)
so the group change takes effect, then:

  1. Get the code onto this box (pick ONE):
       git clone <your-private-repo-url> ihsg-skem-bot
       # or from your Mac:  rsync -av --exclude .venv --exclude data \
       #     --exclude logs ./ ubuntu@<server-ip>:~/ihsg-skem-bot/

  2. cd ihsg-skem-bot
     cp .env.example .env
     nano .env            # set BOT_TOKEN + ADMIN_CHAT_ID (use a token that is
                          # NOT also running on your laptop!)

  3. docker compose -f deploy/docker-compose.yml up -d --build

  4. docker compose -f deploy/docker-compose.yml logs -f   # watch it boot
     # You should get a "Bot started" DM. Ctrl-C stops watching (not the bot).
==============================================================
NEXT
