#!/bin/bash
# Fix SSH setup for mutilate distributed benchmark
# This script fixes SSH key permissions and verifies connectivity

set -euo pipefail

CURRENT_USER=$(whoami)
USER_SSH_DIR="$HOME/.ssh"
ROOT_SSH_DIR="/root/.ssh"

echo "=== Fixing SSH Setup ==="
echo ""

# Fix user SSH key permissions
echo "[1/4] Fixing user SSH key permissions..."
if [ -d "$USER_SSH_DIR" ]; then
    chmod 700 "$USER_SSH_DIR"
    if [ -f "$USER_SSH_DIR/id_rsa" ]; then
        # Fix ownership if owned by root
        if [ "$(stat -c '%U' "$USER_SSH_DIR/id_rsa" 2>/dev/null)" != "$CURRENT_USER" ]; then
            sudo chown "$CURRENT_USER:$CURRENT_USER" "$USER_SSH_DIR/id_rsa"
            echo "✓ Fixed ownership of $USER_SSH_DIR/id_rsa"
        fi
        sudo chmod 600 "$USER_SSH_DIR/id_rsa"
        echo "✓ Fixed permissions on $USER_SSH_DIR/id_rsa"
    fi
    if [ -f "$USER_SSH_DIR/id_rsa.pub" ]; then
        # Fix ownership if owned by root
        if [ "$(stat -c '%U' "$USER_SSH_DIR/id_rsa.pub" 2>/dev/null)" != "$CURRENT_USER" ]; then
            sudo chown "$CURRENT_USER:$CURRENT_USER" "$USER_SSH_DIR/id_rsa.pub"
            echo "✓ Fixed ownership of $USER_SSH_DIR/id_rsa.pub"
        fi
        sudo chmod 644 "$USER_SSH_DIR/id_rsa.pub"
        echo "✓ Fixed permissions on $USER_SSH_DIR/id_rsa.pub"
    fi
    if [ -f "$USER_SSH_DIR/authorized_keys" ]; then
        chmod 600 "$USER_SSH_DIR/authorized_keys"
        echo "✓ Fixed permissions on $USER_SSH_DIR/authorized_keys"
    fi
    if [ -f "$USER_SSH_DIR/known_hosts" ]; then
        chmod 644 "$USER_SSH_DIR/known_hosts"
        echo "✓ Fixed permissions on $USER_SSH_DIR/known_hosts"
    fi
else
    echo "⚠ User SSH directory not found: $USER_SSH_DIR"
fi
echo ""

# Fix root SSH key permissions
echo "[2/4] Fixing root SSH key permissions..."
if sudo [ -d "$ROOT_SSH_DIR" ]; then
    sudo chmod 700 "$ROOT_SSH_DIR"
    if sudo [ -f "$ROOT_SSH_DIR/id_rsa" ]; then
        sudo chmod 600 "$ROOT_SSH_DIR/id_rsa"
        echo "✓ Fixed permissions on $ROOT_SSH_DIR/id_rsa"
    fi
    if sudo [ -f "$ROOT_SSH_DIR/id_rsa.pub" ]; then
        sudo chmod 644 "$ROOT_SSH_DIR/id_rsa.pub"
        echo "✓ Fixed permissions on $ROOT_SSH_DIR/id_rsa.pub"
    fi
    if sudo [ -f "$ROOT_SSH_DIR/authorized_keys" ]; then
        sudo chmod 600 "$ROOT_SSH_DIR/authorized_keys"
        echo "✓ Fixed permissions on $ROOT_SSH_DIR/authorized_keys"
    fi
    if sudo [ -f "$ROOT_SSH_DIR/known_hosts" ]; then
        sudo chmod 644 "$ROOT_SSH_DIR/known_hosts"
        echo "✓ Fixed permissions on $ROOT_SSH_DIR/known_hosts"
    fi
else
    echo "⚠ Root SSH directory not found: $ROOT_SSH_DIR"
fi
echo ""

# Determine current machine role
CURRENT_IP=$(hostname -I | awk '{print $1}')
echo "[3/4] Detecting network configuration..."
echo "Current IP: $CURRENT_IP"

# Check if we're on server (10.10.1.1) or client (10.10.1.2)
if [[ "$CURRENT_IP" == *"10.10.1.1"* ]] || ip addr show | grep -q "10.10.1.1"; then
    ROLE="SERVER"
    REMOTE_IP="10.10.1.2"
    REMOTE_USER="gliargko"
elif [[ "$CURRENT_IP" == *"10.10.1.2"* ]] || ip addr show | grep -q "10.10.1.2"; then
    ROLE="CLIENT"
    REMOTE_IP="10.10.1.1"
    REMOTE_USER="gliargko"
else
    echo "⚠ Could not determine role. Assuming SERVER."
    ROLE="SERVER"
    REMOTE_IP="10.10.1.2"
    REMOTE_USER="gliargko"
fi

echo "Detected as: $ROLE"
echo "Remote IP: $REMOTE_IP"
echo ""

# Test SSH connections
echo "[4/4] Testing SSH connections..."
echo ""

# Test user SSH
echo "Testing user SSH ($CURRENT_USER@$REMOTE_IP)..."
if ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE_USER@$REMOTE_IP" 'echo "User SSH OK"' 2>/dev/null; then
    echo "✓ User SSH works"
else
    echo "⚠ User SSH failed (may need password on first connection)"
    echo "  Try: ssh $REMOTE_USER@$REMOTE_IP"
fi

# Test root SSH (using sudo)
echo ""
echo "Testing root SSH (sudo ssh root@$REMOTE_IP)..."
if sudo ssh -o ConnectTimeout=5 -o BatchMode=yes "root@$REMOTE_IP" 'echo "Root SSH OK"' 2>/dev/null; then
    echo "✓ Root SSH works (with sudo)"
else
    echo "⚠ Root SSH failed (may need password on first connection)"
    echo "  Try: sudo ssh root@$REMOTE_IP"
fi

echo ""
echo "=== SSH Setup Complete ==="
echo ""
echo "Summary:"
echo "  ✓ Fixed SSH key permissions"
echo "  ✓ Verified connectivity"
echo ""
echo "Note: For root SSH, always use 'sudo ssh root@...' instead of 'ssh root@...'"
echo "      This ensures the correct SSH key is used."
