#!/usr/bin/env bash
#
# One-time hardening + Docker install for a fresh OVH VPS-1 (Ubuntu 26.04, SYD).
#
# The Docker repo line below derives its suite from /etc/os-release, so this
# tracks whatever Ubuntu LTS the box runs. Verified against Docker's published
# suites, which include 26.04's `resolute`.
#
# Run ONCE on the new box, before deploying anything. An OVH Ubuntu image ships
# with an unprivileged `ubuntu` user and root SSH already disabled, so pipe it
# through sudo rather than logging in as root:
#
#   ssh ubuntu@<vps-ip> 'sudo bash -s -- "<your-ssh-public-key>"' \
#       < deploy/bootstrap-vps.sh
#
# Note the `--`: without it, sudo swallows the key argument as its own option.
# If your image does permit root login, `ssh root@<vps-ip> 'bash -s' < …` also
# works.
#
# Idempotent — safe to re-run. Deliberately conservative: it never removes an
# existing key and never locks you out before verifying a key is present.

set -euo pipefail

PUBKEY="${1:-}"
DEPLOY_USER="${DEPLOY_USER:-dsec}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"

# --- Deploy user -------------------------------------------------------------
log "Creating deploy user '$DEPLOY_USER'"
if ! id -u "$DEPLOY_USER" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi
usermod -aG sudo "$DEPLOY_USER"

if [ -n "$PUBKEY" ]; then
    install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
    touch "/home/$DEPLOY_USER/.ssh/authorized_keys"
    grep -qxF "$PUBKEY" "/home/$DEPLOY_USER/.ssh/authorized_keys" \
        || echo "$PUBKEY" >> "/home/$DEPLOY_USER/.ssh/authorized_keys"
    chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys"
    chown -R "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
fi

# --- SSH hardening -----------------------------------------------------------
# Refuse to disable password auth unless a key is actually installed, otherwise
# a typo in the pubkey argument locks everyone out of the box permanently.
# The filename must sort BEFORE Ubuntu's own drop-ins. sshd_config takes the
# FIRST value it finds for a keyword, and the cloud image ships
# /etc/ssh/sshd_config.d/50-cloud-init.conf containing `PasswordAuthentication
# yes`. A 99- prefix therefore loses every time: the file is written, sshd
# reloads happily, `sshd -t` passes, and password login stays ON while this
# script prints that it disabled it. Verified on the real box — that is exactly
# what happened. Always confirm with `sshd -T`, never by reading the file back.
log "Hardening SSH"
if [ -s "/home/$DEPLOY_USER/.ssh/authorized_keys" ]; then
    rm -f /etc/ssh/sshd_config.d/99-dsec.conf   # tidy up pre-fix installs
    cat > /etc/ssh/sshd_config.d/01-dsec.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
MaxAuthTries 3
EOF
    sshd -t && systemctl reload ssh
    # Assert the result rather than announcing it. The whole point of the 01-
    # prefix above is that writing the file proves nothing.
    if sshd -T 2>/dev/null | grep -qi '^passwordauthentication no'; then
        echo "  password login disabled; key-only access for $DEPLOY_USER"
    else
        echo "  !! WARNING: password authentication is STILL ENABLED"
        echo "  !! another drop-in in /etc/ssh/sshd_config.d/ is overriding us:"
        sshd -T | grep -i '^passwordauthentication' | sed 's/^/  !!   /'
        grep -ril passwordauthentication /etc/ssh/sshd_config.d/ | sed 's/^/  !!   /'
    fi
else
    echo "  !! no authorized_keys for $DEPLOY_USER — leaving password auth ON"
    echo "  !! re-run with your public key as the first argument"
fi

# Wait for any other apt/dpkg holder before touching the package system.
# `systemctl enable --now unattended-upgrades` below starts a run immediately,
# which then races the Docker install a few lines later and kills it with
# "Could not get lock /var/lib/dpkg/lock-frontend". Observed on the real box.
wait_for_apt() {
    local waited=0
    while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
       || fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do
        [ "$waited" -eq 0 ] && echo "  waiting for another apt/dpkg process to finish…"
        sleep 5
        waited=$((waited + 5))
        if [ "$waited" -ge 600 ]; then
            die "apt/dpkg still locked after 10 minutes"
        fi
    done
}

# --- Firewall ----------------------------------------------------------------
log "Configuring firewall (ufw)"
wait_for_apt
apt-get update -qq
apt-get install -y -qq ufw fail2ban unattended-upgrades >/dev/null
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# --- Automatic security updates ---------------------------------------------
log "Enabling unattended security upgrades"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true

# --- fail2ban ----------------------------------------------------------------
log "Enabling fail2ban for sshd"
cat > /etc/fail2ban/jail.d/dsec.conf <<'EOF'
[sshd]
enabled = true
maxretry = 5
bantime = 1h
EOF
systemctl enable --now fail2ban >/dev/null 2>&1 || true

# --- Docker ------------------------------------------------------------------
log "Installing Docker Engine + compose plugin"
if ! command -v docker >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    wait_for_apt
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin >/dev/null
fi
usermod -aG docker "$DEPLOY_USER"
systemctl enable --now docker >/dev/null 2>&1 || true

# --- Swap --------------------------------------------------------------------
# VPS-1 has 4 GB against a measured ~1.1 GB peak, so this is not for capacity —
# it is a cushion so a transient spike (a bulk image import) cannot OOM-kill the
# API on a box with no memory overcommit headroom.
log "Adding 2 GB swap"
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Deliberately OUTSIDE the block above. Nested inside it, this ran only on the
# very first invocation — so a re-run against a box that already had a swapfile
# skipped it entirely, and the setting was left applied to the running kernel
# but absent from disk. Ubuntu 26.04 also ships no /etc/sysctl.conf, so the
# original `>> /etc/sysctl.conf` failed outright. Both were silent; swappiness
# simply would not have survived the first reboot.
sysctl -w vm.swappiness=10 >/dev/null
install -d /etc/sysctl.d
echo 'vm.swappiness=10' > /etc/sysctl.d/60-dsec-swappiness.conf

log "Done"
cat <<EOF

  Next steps:
    1. Verify key login WITHOUT closing this session:
         ssh $DEPLOY_USER@<vps-ip>
    2. Clone the repo and deploy:
         git clone https://github.com/dsec-hub/dsec-api.git
         cd dsec-api
         cp .env.production.example .env    # fill in real values
         docker compose up -d --build

  Firewall: 22, 80, 443 open. Everything else denied.
EOF
