#!/bin/bash

# ==============================================================================
# DCPerf MediaWiki + Memcached Setup Script with Core Pinning
# ==============================================================================
# System: 40 cores, 2 NUMA nodes (Intel Xeon Silver 4114)
# Based on conversation history from cursor_run_mediawiki_benchmark_and_para.md

set -e  # Exit on any error

echo "🚀 DCPerf MediaWiki + Memcached Setup with Core Pinning"
echo "========================================================"
echo "System: $(nproc) cores detected"
echo "NUMA nodes: $(lscpu | grep "NUMA node(s)" | awk '{print $3}')"
echo ""

# ==============================================================================
# 1. INSTALL PREREQUISITES
# ==============================================================================

echo "📦 Installing system prerequisites..."

# Update package lists
sudo apt update -y

# Install basic packages
sudo apt install -y git wget curl build-essential

# Python dependencies should already be installed, but verify
echo "✅ Verifying Python dependencies..."
python3 -c "import click, yaml, tabulate, pandas" 2>/dev/null || {
    echo "Installing Python dependencies..."
    sudo pip3 install click pyyaml tabulate pandas
}

echo "✅ Prerequisites verified"

# ==============================================================================
# 2. INSTALL HHVM 3.30
# ==============================================================================

echo "🔧 Installing HHVM 3.30..."

if [ ! -d "/opt/local/hhvm-3.30" ]; then
    cd /tmp
    if [ ! -f "hhvm-3.30-multplatform-binary-ubuntu.tar.xz" ]; then
        echo "Downloading HHVM 3.30..."
        wget https://github.com/facebookresearch/DCPerf/releases/download/hhvm/hhvm-3.30-multplatform-binary-ubuntu.tar.xz
    fi
    
    echo "Extracting and installing HHVM..."
    tar -Jxf hhvm-3.30-multplatform-binary-ubuntu.tar.xz
    cd hhvm
    sudo ./pour-hhvm.sh
    
    echo "✅ HHVM 3.30 installed"
else
    echo "✅ HHVM 3.30 already installed"
fi

# ==============================================================================
# 3. SYSTEM CONFIGURATION
# ==============================================================================

echo "⚙️ Configuring system settings..."

# Set CPU governor to performance mode
echo "Setting CPU governor to performance..."
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor > /dev/null

# Set file descriptor limits
echo "Setting file descriptor limits..."
if ! grep -q "* soft nofile 65536" /etc/security/limits.conf; then
    echo "* soft nofile 65536" | sudo tee -a /etc/security/limits.conf > /dev/null
    echo "* hard nofile 65536" | sudo tee -a /etc/security/limits.conf > /dev/null
fi

# Enable TCP TIME_WAIT reuse
echo 1 | sudo tee /proc/sys/net/ipv4/tcp_tw_reuse > /dev/null

echo "✅ System configured"

# ==============================================================================
# 4. INSTALL MEDIAWIKI BENCHMARKS
# ==============================================================================

echo "📥 Installing MediaWiki benchmarks..."

# Return to DCPerf directory
cd /users/gliargko/os-param-tuning/deps/DCPerf

# Install the MediaWiki benchmarks
echo "Installing oss_performance_mediawiki_mlp..."
sudo ./benchpress_cli.py install oss_performance_mediawiki_mlp

echo "Installing oss_performance_mediawiki_mem (with memcached)..."
sudo ./benchpress_cli.py install oss_performance_mediawiki_mem

echo "✅ MediaWiki benchmarks installed"

# ==============================================================================
# 5. FIX PERMISSIONS
# ==============================================================================

echo "🔐 Fixing permissions..."

# Fix HHVM cache permissions
sudo chown -R $USER:$USER ~/.hhvm* 2>/dev/null || true
sudo rm -rf ~/.hhvm.hhbc 2>/dev/null || true

echo "✅ Permissions fixed"

# ==============================================================================
# 6. SYSTEM CHECK
# ==============================================================================

echo "🔍 Running system check..."

./benchpress_cli.py system_check

echo "✅ System check complete"


# ==============================================================================
# 7. QUICK VERIFICATION RUN
# ==============================================================================

echo "🧪 Running quick verification test..."

echo "Testing MediaWiki with memcached (1 minute test)..."
sudo ./benchpress_cli.py run oss_performance_mediawiki_mem -i '{
    "duration": "1m",
    "timeout": "2m",
    "extra_args": "--skip-warmup --memcached-threads 4"
}'

echo ""
echo "🎉 Setup Complete!"
echo ""
