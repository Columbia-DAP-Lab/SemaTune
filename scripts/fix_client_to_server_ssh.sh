#!/bin/bash
# Fix SSH from client to server
# Run this on the CLIENT machine (node1, 10.10.1.2)

set -euo pipefail

CURRENT_USER=$(whoami)
USER_SSH_DIR="$HOME/.ssh"
SERVER_IP="10.10.1.1"
SERVER_USER="gliargko"

echo "=== Fixing SSH from CLIENT to SERVER ==="
echo ""

# Check if we have a public key
if [ ! -f "$USER_SSH_DIR/id_rsa.pub" ]; then
    echo "Error: Public key not found at $USER_SSH_DIR/id_rsa.pub"
    echo "Generating SSH key..."
    ssh-keygen -t rsa -b 4096 -f "$USER_SSH_DIR/id_rsa" -N "" -C "$CURRENT_USER@$(hostname)"
    chmod 600 "$USER_SSH_DIR/id_rsa"
    chmod 644 "$USER_SSH_DIR/id_rsa.pub"
fi

# Fix ownership if needed
if [ "$(stat -c '%U' "$USER_SSH_DIR/id_rsa" 2>/dev/null)" != "$CURRENT_USER" ]; then
    echo "Fixing SSH key ownership..."
    sudo chown "$CURRENT_USER:$CURRENT_USER" "$USER_SSH_DIR/id_rsa" "$USER_SSH_DIR/id_rsa.pub" 2>/dev/null || true
    chmod 600 "$USER_SSH_DIR/id_rsa" 2>/dev/null || sudo chmod 600 "$USER_SSH_DIR/id_rsa"
    chmod 644 "$USER_SSH_DIR/id_rsa.pub" 2>/dev/null || sudo chmod 644 "$USER_SSH_DIR/id_rsa.pub"
fi

echo "Your public key:"
cat "$USER_SSH_DIR/id_rsa.pub"
echo ""

# Try using root SSH to add the key
echo "Attempting to copy key to server using root SSH..."
if sudo ssh -o ConnectTimeout=5 -o BatchMode=yes "root@$SERVER_IP" "echo 'test'" >/dev/null 2>&1; then
    echo "Root SSH works, copying key..."
    PUB_KEY=$(cat "$USER_SSH_DIR/id_rsa.pub")
    echo "$PUB_KEY" | sudo ssh "root@$SERVER_IP" "mkdir -p $USER_SSH_DIR && chmod 700 $USER_SSH_DIR && grep -qF '$PUB_KEY' $USER_SSH_DIR/authorized_keys 2>/dev/null || echo '$PUB_KEY' >> $USER_SSH_DIR/authorized_keys && chmod 600 $USER_SSH_DIR/authorized_keys && chown $SERVER_USER:$SERVER_USER $USER_SSH_DIR/authorized_keys 2>/dev/null || chown $SERVER_USER:$SERVER_USER $USER_SSH_DIR/authorized_keys"
    echo "✓ Key copied successfully"
else
    echo "Root SSH not available. Trying password-based method..."
    echo "You will be prompted for the server password:"
    ssh-copy-id -i "$USER_SSH_DIR/id_rsa.pub" "$SERVER_USER@$SERVER_IP" || {
        echo ""
        echo "⚠ Automatic copy failed. Manual steps:"
        echo ""
        echo "1. Copy your public key:"
        echo "   cat $USER_SSH_DIR/id_rsa.pub"
        echo ""
        echo "2. On the SERVER (10.10.1.1), run:"
        echo "   mkdir -p $USER_SSH_DIR"
        echo "   chmod 700 $USER_SSH_DIR"
        echo "   echo 'YOUR_PUBLIC_KEY_HERE' >> $USER_SSH_DIR/authorized_keys"
        echo "   chmod 600 $USER_SSH_DIR/authorized_keys"
        exit 1
    }
fi

echo ""
echo "Testing SSH connection..."
if ssh -o ConnectTimeout=5 -o BatchMode=yes "$SERVER_USER@$SERVER_IP" 'echo "SSH OK"' >/dev/null 2>&1; then
    echo "✓ SSH from client to server works!"
else
    echo "⚠ SSH test failed. Please check the connection manually:"
    echo "   ssh $SERVER_USER@$SERVER_IP"
fi
