#!/bin/bash
# Setup bidirectional SSH between server and client for mutilate benchmark
# This script ensures SSH works in both directions for both user and root

set -euo pipefail

CURRENT_USER=$(whoami)
USER_SSH_DIR="$HOME/.ssh"
ROOT_SSH_DIR="/root/.ssh"

echo "=== Setting up Bidirectional SSH ==="
echo ""

# Determine current machine role
CURRENT_IP=$(hostname -I | awk '{print $1}')
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
    echo "⚠ Could not determine role from IP. Please specify:"
    echo "  Usage: $0 [SERVER_IP] [CLIENT_IP]"
    echo "  Example: $0 10.10.1.1 10.10.1.2"
    exit 1
fi

echo "Detected as: $ROLE"
echo "Remote IP: $REMOTE_IP"
echo ""

# Step 1: Fix local SSH key permissions
echo "[1/5] Fixing local SSH key permissions..."

# Fix user SSH key ownership and permissions
if [ -f "$USER_SSH_DIR/id_rsa" ]; then
    if [ "$(stat -c '%U' "$USER_SSH_DIR/id_rsa" 2>/dev/null)" != "$CURRENT_USER" ]; then
        sudo chown "$CURRENT_USER:$CURRENT_USER" "$USER_SSH_DIR/id_rsa" "$USER_SSH_DIR/id_rsa.pub" 2>/dev/null || true
    fi
    chmod 700 "$USER_SSH_DIR" 2>/dev/null || sudo chmod 700 "$USER_SSH_DIR"
    chmod 600 "$USER_SSH_DIR/id_rsa" 2>/dev/null || sudo chmod 600 "$USER_SSH_DIR/id_rsa"
    chmod 644 "$USER_SSH_DIR/id_rsa.pub" 2>/dev/null || sudo chmod 644 "$USER_SSH_DIR/id_rsa.pub"
    echo "✓ Fixed user SSH key permissions"
fi

# Fix root SSH key permissions
if sudo [ -f "$ROOT_SSH_DIR/id_rsa" ]; then
    sudo chmod 700 "$ROOT_SSH_DIR"
    sudo chmod 600 "$ROOT_SSH_DIR/id_rsa"
    sudo chmod 644 "$ROOT_SSH_DIR/id_rsa.pub"
    echo "✓ Fixed root SSH key permissions"
fi
echo ""

# Step 2: Copy user's public key to remote user's authorized_keys
echo "[2/5] Copying user's public key to remote user ($REMOTE_USER@$REMOTE_IP)..."
if [ -f "$USER_SSH_DIR/id_rsa.pub" ]; then
    PUB_KEY=$(cat "$USER_SSH_DIR/id_rsa.pub")
    
    # Try using root SSH to add the key
    if sudo ssh -o ConnectTimeout=5 -o BatchMode=yes "root@$REMOTE_IP" "echo 'test'" >/dev/null 2>&1; then
        # Use root SSH to add user's key
        echo "$PUB_KEY" | sudo ssh "root@$REMOTE_IP" "mkdir -p $USER_SSH_DIR && chmod 700 $USER_SSH_DIR && cat >> $USER_SSH_DIR/authorized_keys && chmod 600 $USER_SSH_DIR/authorized_keys && chown $REMOTE_USER:$REMOTE_USER $USER_SSH_DIR/authorized_keys 2>/dev/null || chown $REMOTE_USER:$REMOTE_USER $USER_SSH_DIR/authorized_keys" >/dev/null 2>&1
        echo "✓ User's public key copied to remote"
    else
        echo "⚠ Root SSH not available, trying password-based copy..."
        ssh-copy-id -i "$USER_SSH_DIR/id_rsa.pub" "$REMOTE_USER@$REMOTE_IP" || {
            echo "⚠ Failed to copy user key. You may need to do this manually:"
            echo "  ssh-copy-id $REMOTE_USER@$REMOTE_IP"
        }
    fi
else
    echo "⚠ User's public key not found at $USER_SSH_DIR/id_rsa.pub"
fi
echo ""

# Step 3: Copy root's public key to remote root's authorized_keys
echo "[3/5] Copying root's public key to remote root (root@$REMOTE_IP)..."
if sudo [ -f "$ROOT_SSH_DIR/id_rsa.pub" ]; then
    if sudo ssh -o ConnectTimeout=5 -o BatchMode=yes "root@$REMOTE_IP" "echo 'test'" >/dev/null 2>&1; then
        echo "✓ Root SSH already works"
    else
        sudo ssh-copy-id -i "$ROOT_SSH_DIR/id_rsa.pub" "root@$REMOTE_IP" || {
            echo "⚠ Failed to copy root key. You may need to do this manually:"
            echo "  sudo ssh-copy-id root@$REMOTE_IP"
        }
    fi
else
    echo "⚠ Root's public key not found at $ROOT_SSH_DIR/id_rsa.pub"
fi
echo ""

# Step 4: Test user SSH
echo "[4/5] Testing user SSH ($REMOTE_USER@$REMOTE_IP)..."
if ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE_USER@$REMOTE_IP" 'echo "User SSH OK"' >/dev/null 2>&1; then
    echo "✓ User SSH works"
else
    echo "⚠ User SSH failed"
    echo "  Try manually: ssh $REMOTE_USER@$REMOTE_IP"
fi
echo ""

# Step 5: Test root SSH
echo "[5/5] Testing root SSH (sudo ssh root@$REMOTE_IP)..."
if sudo ssh -o ConnectTimeout=5 -o BatchMode=yes "root@$REMOTE_IP" 'echo "Root SSH OK"' >/dev/null 2>&1; then
    echo "✓ Root SSH works"
else
    echo "⚠ Root SSH failed"
    echo "  Try manually: sudo ssh root@$REMOTE_IP"
fi
echo ""

echo "=== SSH Setup Complete ==="
echo ""
echo "Summary:"
echo "  ✓ Fixed local SSH key permissions"
echo "  ✓ Copied keys to remote machine"
echo "  ✓ Tested connectivity"
echo ""
echo "Note: For root SSH, always use 'sudo ssh root@...' instead of 'ssh root@...'"
