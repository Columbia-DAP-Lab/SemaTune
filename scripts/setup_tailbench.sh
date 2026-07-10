#!/bin/bash

set -e  # Exit on any error

PROJECT_TOP="$(git rev-parse --show-toplevel)"
TAILBENCH_DIR="$PROJECT_TOP/deps/Tailbench"
TAILBENCH_ROOT="$TAILBENCH_DIR/tailbench"
TAILBENCH_DATA_ROOT="$TAILBENCH_DIR/tailbench.inputs"

echo "Tailbench Setup Script"
echo "======================"

# Check if Tailbench is cloned
if [ ! -d "$TAILBENCH_ROOT" ]; then
    echo "Error: Tailbench not found at $TAILBENCH_ROOT"
    echo "Please run: git submodule update --init --recursive deps/Tailbench"
    exit 1
fi

cd "$TAILBENCH_DIR"

# Check if dataset exists
if [ ! -d "$TAILBENCH_DATA_ROOT" ]; then
    echo "Dataset not found. Downloading Tailbench inputs..."
    echo "This may take a while..."
    
    if command -v wget &> /dev/null; then
        wget http://tailbench.csail.mit.edu/tailbench.inputs.tgz
    elif command -v aria2c &> /dev/null; then
        aria2c http://tailbench.csail.mit.edu/tailbench.inputs.tgz
    else
        echo "Error: Neither wget nor aria2c found. Please install one of them."
        exit 1
    fi
    
    echo "Extracting dataset..."
    tar -xf tailbench.inputs.tgz
    rm tailbench.inputs.tgz
else
    echo "Dataset already exists at $TAILBENCH_DATA_ROOT"
fi

# Update configs.sh with correct paths
cd "$TAILBENCH_ROOT"
if [ -f "configs.sh" ]; then
    echo "Updating configs.sh with correct paths..."
    cat > configs.sh << EOF
# Set this to point to the top level of the TailBench data directory
DATA_ROOT=$TAILBENCH_DATA_ROOT

# Set this to point to the top level installation directory of the Java
# Development Kit. Only needed for Specjbb
JDK_PATH=/usr/lib/jvm/java-8-openjdk-amd64

# This location is used by applications to store scratch data during execution.
SCRATCH_DIR=$TAILBENCH_DIR/scratch
EOF
    echo "✓ Updated configs.sh"
else
    echo "Warning: configs.sh not found"
fi

# Check if binaries are built
echo ""
echo "Checking if Tailbench applications are built..."
cd "$TAILBENCH_ROOT"

# Check for harness
if [ ! -f "harness/client.o" ]; then
    echo "Harness not built. Building harness..."
    cd harness
    ./build.sh
    cd ..
fi

# Build masstree if missing
# Build masstree if missing
if [ ! -f "masstree/mttest_integrated" ]; then
    echo ""
    echo "Masstree binary missing. Building masstree..."
    set +e
    (cd masstree && ./build.sh 2>/dev/null) || (cd masstree && make -j"$(nproc)" 2>/dev/null)
    set -e
    if [ -f "masstree/mttest_integrated" ]; then
        echo "✓ Masstree built"
    else
        echo "Note: Masstree build failed or no build.sh. Try: cd $TAILBENCH_ROOT/masstree && ./build.sh"
    fi
    echo "Masstree binary missing. Building masstree..."
    set +e
    (cd masstree && ./build.sh 2>/dev/null) || (cd masstree && make -j"$(nproc)" 2>/dev/null)
    set -e
    if [ -f "masstree/mttest_integrated" ]; then
        echo "✓ Masstree built"
    else
        echo "Note: Masstree build failed or no build.sh. Try: cd $TAILBENCH_ROOT/masstree && ./build.sh"
    fi
else
    echo "✓ Masstree binary found"
fi

# --- Silo: build third-party LZ4 (required at runtime for dbtest_integrated) ---
if [ -d "$TAILBENCH_ROOT/silo/third-party/lz4" ]; then
    if [ ! -f "$TAILBENCH_ROOT/silo/third-party/lz4/liblz4.so" ]; then
        echo ""
        echo "Building Silo third-party LZ4 (required for silo/dbtest_integrated)..."
        if ! (cd "$TAILBENCH_ROOT/silo" && make -C third-party/lz4 library); then
            echo "Standard build failed. Retrying with sudo..."
            if (cd "$TAILBENCH_ROOT/silo" && sudo make -C third-party/lz4 library); then
                echo "✓ Silo liblz4.so built (with sudo)"
            else
                echo "Error: Failed to build Silo LZ4. Try manually:"
                echo "  cd $TAILBENCH_ROOT/silo && sudo make -C third-party/lz4 library"
            fi
        else
            echo "✓ Silo liblz4.so built"
        fi
    else
        echo "✓ Silo third-party LZ4 already built"
    fi
    
    # Symlink to /usr/local/lib so LD_LIBRARY_PATH is not needed
    if [ -f "$TAILBENCH_ROOT/silo/third-party/lz4/liblz4.so" ]; then
        if [ ! -e "/usr/local/lib/liblz4.so" ] || [ "$(readlink -f /usr/local/lib/liblz4.so)" != "$(readlink -f "$TAILBENCH_ROOT/silo/third-party/lz4/liblz4.so")" ]; then
            echo "Symlinking Silo LZ4 to /usr/local/lib (to avoid setting LD_LIBRARY_PATH)..."
            if command -v sudo &>/dev/null; then
                sudo ln -sf "$TAILBENCH_ROOT/silo/third-party/lz4/liblz4.so" /usr/local/lib/liblz4.so
                sudo ldconfig
                echo "✓ Silo LZ4 symlinked to /usr/local/lib"
            else
                echo "Warning: sudo not found. You will need to set LD_LIBRARY_PATH for Silo manually."
            fi
        fi
    fi
fi

# --- Silo + Masstree: check and install system dependencies ---
echo ""
echo "Checking system dependencies for Tailbench (Silo, Masstree)..."
INSTALL_DEPS=""
# Check for jemalloc (required for Silo dbtest_integrated - libjemalloc.so.2)
if ! ldconfig -p 2>/dev/null | grep -q "libjemalloc.so.2"; then
    if ! dpkg -s libjemalloc2 &>/dev/null; then
        INSTALL_DEPS="${INSTALL_DEPS}libjemalloc2 "
    fi
fi
# Check for Berkeley DB C++ (required for Silo - libdb_cxx-5.3.so)
if ! ldconfig -p 2>/dev/null | grep -q "libdb_cxx-5.3"; then
    if ! dpkg -s libdb5.3++ &>/dev/null; then
        INSTALL_DEPS="${INSTALL_DEPS}libdb5.3++ "
    fi
fi
# Check for tcmalloc (required for Masstree mttest_integrated - libtcmalloc_minimal.so.4)
if ! ldconfig -p 2>/dev/null | grep -q "libtcmalloc_minimal"; then
    if ! dpkg -s libtcmalloc-minimal4 &>/dev/null; then
        INSTALL_DEPS="${INSTALL_DEPS}libtcmalloc-minimal4 "
    fi
fi

if [ -n "$INSTALL_DEPS" ]; then
    echo "  Missing runtime libs: $INSTALL_DEPS"
    echo "  Attempting to install via sudo apt-get..."
    if command -v sudo &>/dev/null; then
        set +e
        sudo apt-get update -qq && sudo apt-get install -y $INSTALL_DEPS
        RET=$?
        set -e
        if [ $RET -eq 0 ]; then
             echo "✓ Tailbench runtime libs installed"
        else
             echo "Error installing dependencies. Please run manually:"
             echo "  sudo apt-get install -y $INSTALL_DEPS"
             # Don't exit, try to proceed
        fi
    else
        echo "Error: sudo not found. Please install manually:"
        echo "  apt-get install -y $INSTALL_DEPS"
    fi
else
    echo "✓ Silo (jemalloc, db) and Masstree (libtcmalloc-minimal4) runtime libs found"
fi

# --- Sphinx: fix .pc files and create .so symlinks (for pkg-config and linker) ---
SPHINX_INSTALL="$TAILBENCH_ROOT/sphinx/sphinx-install"
if [ -d "$SPHINX_INSTALL/lib/pkgconfig" ]; then
    # Fix .pc prefix if wrong
    for pc in "$SPHINX_INSTALL/lib/pkgconfig"/*.pc; do
        [ -f "$pc" ] || continue
        CURRENT_PREFIX="$(grep '^prefix=' "$pc" | sed 's/^prefix=//')"
        if [ -n "$CURRENT_PREFIX" ] && [ "$CURRENT_PREFIX" != "$SPHINX_INSTALL" ] && [ ! -d "$CURRENT_PREFIX/include" ]; then
            if sed -i "s|^prefix=.*|prefix=$SPHINX_INSTALL|" "$pc" 2>/dev/null; then
                echo "✓ Fixed $(basename "$pc") prefix for current repo path"
            elif command -v sudo &>/dev/null; then
                sudo sed -i "s|^prefix=.*|prefix=$SPHINX_INSTALL|" "$pc" 2>/dev/null && echo "✓ Fixed $(basename "$pc") prefix for current repo path"
            fi
        fi
    done
fi
# Create .so symlinks (linker needs libXXX.so, not libXXX.so.3)
if [ -d "$SPHINX_INSTALL/lib" ]; then
    cd "$SPHINX_INSTALL/lib"
    for lib in libpocketsphinx.so libsphinxbase.so libsphinxad.so; do
        if [ ! -e "$lib" ] && [ -e "${lib}.3" ]; then
            if ln -sf "${lib}.3" "$lib" 2>/dev/null; then
                echo "✓ Created $lib symlink"
            elif command -v sudo &>/dev/null; then
                sudo ln -sf "${lib}.3" "$lib" 2>/dev/null && echo "✓ Created $lib symlink"
            fi
        fi
    done
    cd - >/dev/null
fi

# --- Sphinx: build if missing or if binary has wrong MODELDIR path (wrong repo path) ---
SPHINX_BIN="$TAILBENCH_ROOT/sphinx/decoder_integrated"
SPHINX_MODEL_NEEDED="$TAILBENCH_ROOT/sphinx/sphinx-install/share/pocketsphinx/model/en-us/en-us"
REBUILD_SPHINX=0
if [ ! -f "$SPHINX_BIN" ]; then
    REBUILD_SPHINX=1
else
    # Binary exists; check if embedded MODELDIR path exists (binary may have been built for different repo path)
    EMBEDDED_PATH="$(strings "$SPHINX_BIN" 2>/dev/null | grep 'share/pocketsphinx/model' | head -1)"
    if [ -n "$EMBEDDED_PATH" ]; then
        # MODELDIR is the dir containing en-us (e.g. .../model from .../model/en-us/en-us)
        EMBEDDED_MODELDIR="$(dirname "$(dirname "$EMBEDDED_PATH")")"
        if [ -n "$EMBEDDED_MODELDIR" ] && [ ! -d "$EMBEDDED_MODELDIR" ]; then
            echo "Sphinx binary was built for a different path (model dir missing). Rebuilding..."
            REBUILD_SPHINX=1
        fi
    fi
fi
if [ "$REBUILD_SPHINX" = "1" ] && [ -d "$TAILBENCH_ROOT/sphinx" ]; then
    echo ""
    # Sphinx build needs pkg-config for pocketsphinx/sphinxbase
    if ! command -v pkg-config &>/dev/null; then
        if command -v apt-get &>/dev/null; then
            echo "Installing pkg-config (required for Sphinx build)..."
            sudo apt-get update -qq 2>/dev/null && sudo apt-get install -y pkg-config 2>/dev/null || true
        fi
    fi
    if ! command -v pkg-config &>/dev/null; then
        echo "Warning: pkg-config not found. Install it for Sphinx build: sudo apt-get install -y pkg-config"
    else
        echo "Building Sphinx (binary missing or wrong model path)..."
        rm -f "$SPHINX_BIN" "$TAILBENCH_ROOT/sphinx/"*.o 2>/dev/null || true
        set +e
        (cd "$TAILBENCH_ROOT/sphinx" && ./build.sh 2>/dev/null) || (cd "$TAILBENCH_ROOT/sphinx" && make -j"$(nproc)" 2>/dev/null)
        set -e
    fi
    if [ -f "$SPHINX_BIN" ]; then
        echo "✓ Sphinx built"
    else
        echo "Note: Sphinx build failed. Try: cd $TAILBENCH_ROOT/sphinx && ./build.sh"
    fi
else
    [ -f "$SPHINX_BIN" ] && echo "✓ Sphinx binary found"
fi

# --- Sphinx: download AN4 corpus and create audio_samples if missing ---
SPHINX_DATA="$TAILBENCH_DATA_ROOT/sphinx"
SPHINX_AUDIO_SAMPLES="$TAILBENCH_ROOT/sphinx/audio_samples"
if [ -f "$SPHINX_BIN" ] && { [ ! -f "$SPHINX_AUDIO_SAMPLES" ] || [ ! -d "$SPHINX_DATA/wav" ] || [ -z "$(find "$SPHINX_DATA" -name '*.raw' 2>/dev/null | head -1)" ]; }; then
    echo ""
    echo "Sphinx AN4 dataset missing or incomplete. Preparing..."
    if ! command -v sox &>/dev/null && ! command -v ffmpeg &>/dev/null && command -v apt-get &>/dev/null; then
        echo "Installing sox for WAV->raw conversion..."
        sudo apt-get update -qq 2>/dev/null && sudo apt-get install -y sox 2>/dev/null || true
    fi
    mkdir -p "$SPHINX_DATA"
    AN4_TMP="$TAILBENCH_DIR/an4_tmp"
    rm -rf "$AN4_TMP"
    if git clone --depth 1 https://github.com/cmusphinx/an4.git "$AN4_TMP" 2>/dev/null; then
        mkdir -p "$SPHINX_DATA/wav"
        cp -r "$AN4_TMP/wav/"* "$SPHINX_DATA/wav/" 2>/dev/null || cp -r "$AN4_TMP/wav" "$SPHINX_DATA/" 2>/dev/null || true
        # Convert .wav to .raw (Sphinx expects raw PCM). Try sox, then ffmpeg, then strip 44-byte WAV header.
        find "$SPHINX_DATA/wav" -name '*.wav' 2>/dev/null > "$AN4_TMP/wav_files.txt"
        while IFS= read -r wav; do
            raw="${wav%.wav}.raw"
            if command -v sox &>/dev/null; then sox "$wav" -t raw -r 16000 -e signed-integer -b 16 "$raw" 2>/dev/null && rm -f "$wav"
            elif command -v ffmpeg &>/dev/null; then ffmpeg -y -i "$wav" -f s16le -ar 16000 -ac 1 "$raw" 2>/dev/null && rm -f "$wav"
            else tail -c +45 "$wav" > "$raw" 2>/dev/null && rm -f "$wav"; fi
        done < "$AN4_TMP/wav_files.txt"
        # Create audio_samples (paths relative to TBENCH_AN4_CORPUS = SPHINX_DATA)
        AUDIO_LIST="$(find "$SPHINX_DATA" -name '*.raw' 2>/dev/null | sed "s|^$SPHINX_DATA/||" | sort)"
        if [ -n "$AUDIO_LIST" ]; then
            echo "$AUDIO_LIST" > "$SPHINX_AUDIO_SAMPLES"
            echo "✓ Sphinx AN4 dataset prepared ($(echo "$AUDIO_LIST" | wc -l) samples), audio_samples created"
        else
            echo "Note: No .raw files produced. Install sox or ffmpeg: sudo apt-get install -y sox ffmpeg"
        fi
        rm -rf "$AN4_TMP"
    else
        echo "Note: Could not clone AN4. Install Sphinx data manually: git clone https://github.com/cmusphinx/an4.git; convert wav to raw; create $TAILBENCH_ROOT/sphinx/audio_samples"
    fi
fi
[ -f "$SPHINX_AUDIO_SAMPLES" ] && [ -s "$SPHINX_AUDIO_SAMPLES" ] && echo "✓ Sphinx audio_samples found"

# --- Xapian: build if missing ---
if [ -d "$TAILBENCH_ROOT/xapian" ]; then
    if [ ! -f "$TAILBENCH_ROOT/xapian/xapian_integrated" ]; then
        echo ""
        echo "Xapian binary missing. Building xapian..."
        set +e
        (cd "$TAILBENCH_ROOT/xapian" && ./build.sh 2>/dev/null) || (cd "$TAILBENCH_ROOT/xapian" && make -j"$(nproc)" 2>/dev/null)
        set -e
        if [ -f "$TAILBENCH_ROOT/xapian/xapian_integrated" ]; then
            echo "✓ Xapian built"
        else
            echo "Note: Xapian build failed. Try: cd $TAILBENCH_ROOT/xapian && ./build.sh"
        fi
    else
        echo "✓ Xapian binary found"
    fi
    if [ ! -d "$TAILBENCH_DATA_ROOT/xapian/wiki" ] || [ ! -f "$TAILBENCH_DATA_ROOT/xapian/terms.in" ]; then
        echo "Note: Xapian data (wiki, terms.in) should be under $TAILBENCH_DATA_ROOT/xapian/"
    fi
fi

# --- Silo: build dbtest_integrated if missing ---
if [ -d "$TAILBENCH_ROOT/silo" ] && [ ! -f "$TAILBENCH_ROOT/silo/out-perf.masstree/benchmarks/dbtest_integrated" ]; then
    echo ""
    echo "Silo binary missing. Building silo..."
    set +e
    (cd "$TAILBENCH_ROOT/silo" && make -j"$(nproc)" 2>/dev/null)
    set -e
    if [ -f "$TAILBENCH_ROOT/silo/out-perf.masstree/benchmarks/dbtest_integrated" ]; then
        echo "✓ Silo built"
    else
        echo "Note: Silo build failed. Try: cd $TAILBENCH_ROOT/silo && make -j\$(nproc)"
    fi
else
    [ -f "$TAILBENCH_ROOT/silo/out-perf.masstree/benchmarks/dbtest_integrated" ] && echo "✓ Silo binary found"
fi


echo ""
echo "Tailbench setup complete!"
echo ""
echo "Dependencies and libraries have been installed globally."
echo "You can now run benchmarks directly (no LD_LIBRARY_PATH needed)."
echo ""
echo "Runtime notes (manual runs):"
echo "  - Reporting per second: set TBENCH_METRICS_INTERVAL_SEC=1"
echo "  - Sphinx: needs DATA_ROOT/sphinx environment variable if running outside the wrapper."

echo ""
echo "To use Tailbench with the optimizer, create a config file like:"
echo "  config/single_param/<scenario>/tailbench_<app>_config_fixed.json"
echo ""
echo "Example config structure:"
echo '  {'
echo '    "benchmark": "tailbench",'
echo '    "tailbench_app": "masstree",'
echo '    "tailbench_qps": 2000,'
echo '    "tailbench_threads": 32,'
echo '    "tailbench_metrics_interval_sec": 1,'
echo '    "pin_to_cores": "0-9",'
echo '    "tuner_type": "fixed",'
echo '    "parameter_ranges": { "min_granularity_ns": [100000, 50000000] },'
echo '    "parameters_to_tune": ["min_granularity_ns"],'
echo '    "optimization_metric": "latency_p95",'
echo '    "optimization_goal": "minimize",'
echo '    "max_iterations": 60,'
echo '    "window_duration": 10,'
echo '    "results_dir": "results/tailbench_masstree_1param_fixed"'
echo '  }'

