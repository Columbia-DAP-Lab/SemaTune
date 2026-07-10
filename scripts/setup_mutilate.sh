#!/bin/bash
# Setup script for mutilate benchmark
# This script:
# - Clones mutilate as a submodule if not already present
# - Installs memcached and build dependencies
# - Fixes SConstruct for Python 3 compatibility
# - Builds mutilate
# - Sets up SSH keys for root/sudo operations
#
# For distributed setup (client-server), provide remote IP addresses:
#   ./setup_mutilate.sh [CLIENT_IP] [SERVER_IP]
#
# Example:
#   ./setup_mutilate.sh 128.105.144.39 128.105.144.33

set -euo pipefail

PROJECT_TOP="$(git rev-parse --show-toplevel)"
MUTILATE_DIR="$PROJECT_TOP/deps/mutilate"

# Optional: remote IP addresses for distributed setup
CLIENT_IP="10.10.1.2"
SERVER_IP="10.10.1.1"

echo "=== Setting up Mutilate ==="
echo ""
if [ -n "$CLIENT_IP" ] && [ -n "$SERVER_IP" ]; then
    echo "Distributed setup mode:"
    echo "  Client IP: $CLIENT_IP"
    echo "  Server IP: $SERVER_IP"
    echo ""
fi

# Check if mutilate is already a submodule
if [ -f "$PROJECT_TOP/.gitmodules" ] && grep -q "deps/mutilate" "$PROJECT_TOP/.gitmodules"; then
    echo "Mutilate is already configured as a submodule"
    echo "Initializing submodule..."
    git submodule update --init --recursive deps/mutilate || {
        echo "Warning: Submodule initialization failed. Using existing directory if present."
    }
elif [ -d "$MUTILATE_DIR" ] && [ -d "$MUTILATE_DIR/.git" ]; then
    echo "Mutilate directory already exists at $MUTILATE_DIR (not a submodule)"
    echo "Using existing directory..."
elif [ -d "$MUTILATE_DIR" ]; then
    echo "Mutilate directory exists but is not a git repo. Removing and adding as submodule..."
    rm -rf "$MUTILATE_DIR"
    cd "$PROJECT_TOP"
    git submodule add https://github.com/leverich/mutilate.git deps/mutilate
else
    echo "Adding mutilate as a git submodule..."
    cd "$PROJECT_TOP"
    git submodule add https://github.com/leverich/mutilate.git deps/mutilate || {
        echo "Warning: Submodule add failed. Checking if directory exists..."
        if [ -d "$MUTILATE_DIR" ]; then
            echo "Using existing directory at $MUTILATE_DIR"
        else
            echo "Error: Could not add submodule and directory does not exist"
            exit 1
        fi
    }
fi

# Verify mutilate directory exists
if [ ! -d "$MUTILATE_DIR" ]; then
    echo "Error: Mutilate directory not found at $MUTILATE_DIR"
    echo "Please ensure the submodule was initialized correctly"
    exit 1
fi

echo "Mutilate directory: $MUTILATE_DIR"
echo ""

# Install memcached
echo "[1/4] Installing memcached..."
sudo apt-get update
sudo apt-get install -y memcached

echo "✓ Memcached installed"
echo ""

# Install build dependencies
echo "[2/4] Installing build dependencies..."
sudo apt-get install -y \
    build-essential \
    g++ \
    gcc \
    scons \
    libevent-dev \
    gengetopt

# Try to install ZMQ dev package (may have different names on different distros)
echo "Installing ZMQ development libraries..."
if sudo apt-get install -y libzmq-dev 2>/dev/null; then
    echo "✓ libzmq-dev installed"
elif sudo apt-get install -y libzmq3-dev 2>/dev/null; then
    echo "✓ libzmq3-dev installed"
elif sudo apt-get install -y zeromq-dev 2>/dev/null; then
    echo "✓ zeromq-dev installed"
else
    echo "⚠ Warning: Could not install ZMQ development package"
    echo "  Tried: libzmq-dev, libzmq3-dev, zeromq-dev"
    echo "  Mutilate may still build without ZMQ if it's not required"
fi

echo "✓ Build dependencies installed"
echo ""

# Fix SConstruct for Python 3 compatibility
echo "[3/4] Fixing SConstruct for Python 3 compatibility..."

SCONSTRUCT_FILE="$MUTILATE_DIR/SConstruct"

if [ ! -f "$SCONSTRUCT_FILE" ]; then
    echo "Error: SConstruct not found at $SCONSTRUCT_FILE"
    exit 1
fi

# Backup original if not already backed up
if [ ! -f "$SCONSTRUCT_FILE.bak" ]; then
    cp "$SCONSTRUCT_FILE" "$SCONSTRUCT_FILE.bak"
    echo "Created backup: $SCONSTRUCT_FILE.bak"
fi

# Use fix script if available, otherwise do manual fix
if [ -f "$PROJECT_TOP/mutilate/fix_sconstruct_python3.sh" ]; then
    "$PROJECT_TOP/mutilate/fix_sconstruct_python3.sh" "$SCONSTRUCT_FILE"
elif [ -f "$MUTILATE_DIR/fix_sconstruct_python3.sh" ]; then
    "$MUTILATE_DIR/fix_sconstruct_python3.sh" "$SCONSTRUCT_FILE"
else
    # Manual fix
    echo "Applying manual Python 3 fixes..."
    # Fix print statements
    if grep -q 'print ".*"' "$SCONSTRUCT_FILE"; then
        sed -i 's/print "\([^"]*\)"/print("\1")/g' "$SCONSTRUCT_FILE"
        sed -i "s/print '\([^']*\)'/print('\1')/g" "$SCONSTRUCT_FILE"
    fi
    # Fix shebang
    sed -i '1s|#!/usr/bin/python$|#!/usr/bin/python3|' "$SCONSTRUCT_FILE"
    sed -i '1s|#!/usr/bin/env python$|#!/usr/bin/env python3|' "$SCONSTRUCT_FILE"
fi

echo "✓ SConstruct fixed for Python 3"
echo ""

# Build mutilate
echo "[4/4] Building mutilate..."
cd "$MUTILATE_DIR"

if scons; then
    echo "✓ Mutilate built successfully"
    if [ -f "$MUTILATE_DIR/mutilate" ]; then
        chmod +x "$MUTILATE_DIR/mutilate"
        echo "  Binary location: $MUTILATE_DIR/mutilate"
    fi
else
    echo "✗ Mutilate build failed"
    echo "Please check the error messages above"
    exit 1
fi

# Setup SSH keys for root (for sudo operations)
echo "[5/5] Setting up SSH keys for root (for sudo operations)..."
CURRENT_USER=$(whoami)
ROOT_SSH_DIR="/root/.ssh"

# Create root SSH directory
sudo mkdir -p "$ROOT_SSH_DIR"
sudo chmod 700 "$ROOT_SSH_DIR"

# Generate root SSH key if it doesn't exist
if [ ! -f "$ROOT_SSH_DIR/id_rsa" ]; then
    echo "Generating SSH key for root..."
    sudo ssh-keygen -t rsa -b 4096 -f "$ROOT_SSH_DIR/id_rsa" -N "" -C "root@$(hostname)"
fi

# Generate user SSH key if it doesn't exist
USER_SSH_DIR="$HOME/.ssh"
mkdir -p "$USER_SSH_DIR"
chmod 700 "$USER_SSH_DIR"

if [ ! -f "$USER_SSH_DIR/id_rsa" ]; then
    echo "Generating SSH key for user $CURRENT_USER..."
    ssh-keygen -t rsa -b 4096 -f "$USER_SSH_DIR/id_rsa" -N "" -C "$CURRENT_USER@$(hostname)"
    # Ensure correct ownership (in case it was created with sudo)
    chown "$CURRENT_USER:$CURRENT_USER" "$USER_SSH_DIR/id_rsa" "$USER_SSH_DIR/id_rsa.pub" 2>/dev/null || true
    chmod 600 "$USER_SSH_DIR/id_rsa"
    chmod 644 "$USER_SSH_DIR/id_rsa.pub"
fi

# Fix ownership if keys exist but are owned by root
if [ -f "$USER_SSH_DIR/id_rsa" ] && [ "$(stat -c '%U' "$USER_SSH_DIR/id_rsa" 2>/dev/null)" != "$CURRENT_USER" ]; then
    echo "Fixing ownership of user SSH keys..."
    sudo chown "$CURRENT_USER:$CURRENT_USER" "$USER_SSH_DIR/id_rsa" "$USER_SSH_DIR/id_rsa.pub" 2>/dev/null || true
    chmod 600 "$USER_SSH_DIR/id_rsa" 2>/dev/null || sudo chmod 600 "$USER_SSH_DIR/id_rsa"
    chmod 644 "$USER_SSH_DIR/id_rsa.pub" 2>/dev/null || sudo chmod 644 "$USER_SSH_DIR/id_rsa.pub"
fi

echo "✓ SSH keys generated"
echo ""

# If distributed setup mode, set up SSH between machines
if [ -n "$CLIENT_IP" ] && [ -n "$SERVER_IP" ]; then
    echo "[6/6] Setting up SSH between client and server..."
    
    # Determine if this is the client or server
    CURRENT_IP=$(hostname -I | awk '{print $1}')
    
    if [ "$CURRENT_IP" = "$CLIENT_IP" ]; then
        echo "Detected as CLIENT machine"
        echo "Setting up SSH keys to server ($SERVER_IP)..."
        
        # Copy user's public key to server
        echo "Copying user's SSH public key to server..."
        echo "You may be prompted for the server password (one-time setup)"
        ssh-copy-id -i "$USER_SSH_DIR/id_rsa.pub" "$CURRENT_USER@$SERVER_IP" || {
            echo "Note: If automatic copy failed, manually copy this key to server:"
            echo "  cat $USER_SSH_DIR/id_rsa.pub | ssh $CURRENT_USER@$SERVER_IP 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys'"
        }
        
        # Copy root's public key to server
        echo "Copying root's SSH public key to server..."
        echo "You may be prompted for the server root password (one-time setup)"
        sudo ssh-copy-id -i "$ROOT_SSH_DIR/id_rsa.pub" "root@$SERVER_IP" || {
            echo "Note: If automatic copy failed, manually copy this key to server:"
            echo "  sudo cat $ROOT_SSH_DIR/id_rsa.pub | sudo ssh root@$SERVER_IP 'mkdir -p /root/.ssh && cat >> /root/.ssh/authorized_keys'"
        }
        
    elif [ "$CURRENT_IP" = "$SERVER_IP" ]; then
        echo "Detected as SERVER machine"
        echo "Setting up SSH keys to client ($CLIENT_IP)..."
        
        # Copy user's public key to client
        echo "Copying user's SSH public key to client..."
        echo "You may be prompted for the client password (one-time setup)"
        ssh-copy-id -i "$USER_SSH_DIR/id_rsa.pub" "$CURRENT_USER@$CLIENT_IP" || {
            echo "Note: If automatic copy failed, manually copy this key to client:"
            echo "  cat $USER_SSH_DIR/id_rsa.pub | ssh $CURRENT_USER@$CLIENT_IP 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys'"
        }
        
        # Copy root's public key to client
        echo "Copying root's SSH public key to client..."
        echo "You may be prompted for the client root password (one-time setup)"
        sudo ssh-copy-id -i "$ROOT_SSH_DIR/id_rsa.pub" "root@$CLIENT_IP" || {
            echo "Note: If automatic copy failed, manually copy this key to client:"
            echo "  sudo cat $ROOT_SSH_DIR/id_rsa.pub | sudo ssh root@$CLIENT_IP 'mkdir -p /root/.ssh && cat >> /root/.ssh/authorized_keys'"
        }
    else
        echo "Warning: Current IP ($CURRENT_IP) doesn't match CLIENT ($CLIENT_IP) or SERVER ($SERVER_IP)"
        echo "Skipping automatic SSH key exchange"
        echo ""
        echo "To manually set up SSH:"
        echo "  1. On CLIENT ($CLIENT_IP), run:"
        echo "     ssh-copy-id $CURRENT_USER@$SERVER_IP"
        echo "     sudo ssh-copy-id root@$SERVER_IP"
        echo ""
        echo "  2. On SERVER ($SERVER_IP), run:"
        echo "     ssh-copy-id $CURRENT_USER@$CLIENT_IP"
        echo "     sudo ssh-copy-id root@$CLIENT_IP"
    fi
    
    echo "✓ SSH keys configured"
    echo ""
    
    # Test SSH connections
    echo "Testing SSH connections..."
    if [ "$CURRENT_IP" = "$CLIENT_IP" ]; then
        if ssh -o ConnectTimeout=5 "$CURRENT_USER@$SERVER_IP" 'echo "User SSH OK"' 2>/dev/null; then
            echo "✓ User SSH to server works"
        else
            echo "⚠ User SSH to server failed (may need manual setup)"
        fi
        
        if sudo ssh -o ConnectTimeout=5 "root@$SERVER_IP" 'echo "Root SSH OK"' 2>/dev/null; then
            echo "✓ Root SSH to server works"
        else
            echo "⚠ Root SSH to server failed (may need manual setup)"
        fi
    elif [ "$CURRENT_IP" = "$SERVER_IP" ]; then
        if ssh -o ConnectTimeout=5 "$CURRENT_USER@$CLIENT_IP" 'echo "User SSH OK"' 2>/dev/null; then
            echo "✓ User SSH to client works"
        else
            echo "⚠ User SSH to client failed (may need manual setup)"
        fi
        
        if sudo ssh -o ConnectTimeout=5 "root@$CLIENT_IP" 'echo "Root SSH OK"' 2>/dev/null; then
            echo "✓ Root SSH to client works"
        else
            echo "⚠ Root SSH to client failed (may need manual setup)"
        fi
    fi
    echo ""
else
    echo "[6/6] Skipping distributed SSH setup (no IPs provided)"
    echo ""
    echo "To set up SSH for distributed mutilate, run:"
    echo "  ./setup_mutilate.sh <CLIENT_IP> <SERVER_IP>"
    echo ""
fi

echo ""
echo "=== Mutilate Setup Complete! ==="
echo ""
echo "Summary:"
echo "  ✓ Mutilate submodule initialized at: $MUTILATE_DIR"
echo "  ✓ Memcached installed"
echo "  ✓ Build dependencies installed"
echo "  ✓ SConstruct fixed for Python 3"
echo "  ✓ Mutilate built successfully"
echo "  ✓ SSH keys generated for user and root"
if [ -n "$CLIENT_IP" ] && [ -n "$SERVER_IP" ]; then
    echo "  ✓ SSH configured between client and server"
fi
echo ""
echo "Next steps:"
echo "  1. Start memcached (if needed):"
echo "     sudo memcached -u root -t 4 -m 1024 -l 127.0.0.1 -p 11211"
echo ""
echo "  2. Test mutilate:"
echo "     cd $MUTILATE_DIR"
echo "     ./mutilate --help"
echo ""
if [ -n "$CLIENT_IP" ] && [ -n "$SERVER_IP" ]; then
    echo "  3. Verify SSH connections:"
    CURRENT_IP=$(hostname -I | awk '{print $1}')
    if [ "$CURRENT_IP" = "$CLIENT_IP" ]; then
        echo "     ssh $CURRENT_USER@$SERVER_IP 'echo Connection OK'"
        echo "     sudo ssh root@$SERVER_IP 'echo Connection OK'"
    elif [ "$CURRENT_IP" = "$SERVER_IP" ]; then
        echo "     ssh $CURRENT_USER@$CLIENT_IP 'echo Connection OK'"
        echo "     sudo ssh root@$CLIENT_IP 'echo Connection OK'"
    fi
    echo ""
fi

