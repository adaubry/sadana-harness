#!/usr/bin/env bash
# Fresh Ubuntu 22.04/24.04 -> a connected box, in one script. `docs/
# install.md` is the step-by-step narrative this same sequence follows;
# this is the runnable version, parameterized by environment variables so
# it can run unattended (a staging checkpoint's own EC2 user-data, in
# particular).
#
# Required:
#   SADANA_INSTALL_REPO_URL      the repository's own clone URL
#   SADANA_INSTALL_TAG           the release tag to install
#   SADANA_INSTALL_ENROLL_TOKEN  the one-time token minted in the console
#   SADANA_INSTALL_RELAY_URL     the console's relay URL
#   SADANA_OPENROUTER_KEY        an OpenRouter API key
#   SADANA_INSTALL_WEBHOOK_SECRET  a webhook secret this box will sign with
#
# Optional:
#   SADANA_INSTALL_CONSOLE_URL   defaults to SADANA_INSTALL_RELAY_URL
#   SADANA_INSTALL_DIR           defaults to $HOME/sadana-harness
set -euo pipefail

: "${SADANA_INSTALL_REPO_URL:?set to the repository clone URL}"
: "${SADANA_INSTALL_TAG:?set to the release tag to install}"
: "${SADANA_INSTALL_ENROLL_TOKEN:?set to the one-time token minted in the console}"
: "${SADANA_INSTALL_RELAY_URL:?set to the console relay URL}"
: "${SADANA_OPENROUTER_KEY:?set to an OpenRouter API key}"
: "${SADANA_INSTALL_WEBHOOK_SECRET:?set to a webhook secret}"

install_dir="${SADANA_INSTALL_DIR:-$HOME/sadana-harness}"
console_url="${SADANA_INSTALL_CONSOLE_URL:-$SADANA_INSTALL_RELAY_URL}"
gateway_user="$(whoami)"

echo "== prerequisites: python3.11, python3-venv, git =="
sudo apt-get update -y
sudo apt-get install -y python3.11 python3.11-venv git

echo "== clone at $SADANA_INSTALL_TAG =="
git clone --branch "$SADANA_INSTALL_TAG" -- "$SADANA_INSTALL_REPO_URL" "$install_dir"
cd "$install_dir"

echo "== .venv =="
python3.11 -m venv .venv

echo "== pip install -e . =="
.venv/bin/pip install -e . --quiet

echo "== sadana setup =="
.venv/bin/sadana setup --openrouter-key "$SADANA_OPENROUTER_KEY" --webhook-secret "$SADANA_INSTALL_WEBHOOK_SECRET"

echo "== sadana enroll =="
.venv/bin/sadana enroll "$SADANA_INSTALL_ENROLL_TOKEN" --relay "$SADANA_INSTALL_RELAY_URL" --console "$console_url"

echo "== allowing the gateway's own user to restart its service (the tether's 'upgrade' action needs this — it runs as this same user, not root) =="
echo "$gateway_user ALL=(root) NOPASSWD: /usr/bin/systemctl restart sadana-gateway" \
  | sudo tee /etc/sudoers.d/sadana-gateway-restart > /dev/null
sudo chmod 0440 /etc/sudoers.d/sadana-gateway-restart

echo "== installing and starting the gateway service =="
sudo .venv/bin/sadana gateway install
sudo .venv/bin/sadana gateway start

echo "== status =="
.venv/bin/sadana gateway status
