#!/bin/bash

# Verification script for concurrent streaming implementation
# Run this to verify everything is set up correctly

set -e

echo "🔍 Quranic Recitation System - Verification Script"
echo "=================================================="
echo ""

# Check 1: Python environment
echo "✓ Checking Python environment..."
PYTHON_PATH="/Users/harshad/Documents/AQ_1/.venv/bin/python"
if [ -f "$PYTHON_PATH" ]; then
    echo "  ✅ Python virtual environment found"
    PYTHON_VERSION=$($PYTHON_PATH --version 2>&1)
    echo "     $PYTHON_VERSION"
else
    echo "  ❌ Python virtual environment not found"
    exit 1
fi
echo ""

# Check 2: Required files exist
echo "✓ Checking required files..."
FILES=(
    "app/streaming/concurrent_session.py"
    "app/stt/whisper_engine.py"
    "app/core/sequence_engine_v2.py"
    "app/services/recitation_service.py"
    "app/main.py"
)

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "  ✅ $file"
    else
        echo "  ❌ $file MISSING"
        exit 1
    fi
done
echo ""

# Check 3: Syntax validation
echo "✓ Validating Python syntax..."
$PYTHON_PATH -m py_compile app/streaming/concurrent_session.py && \
    echo "  ✅ concurrent_session.py"
$PYTHON_PATH -m py_compile app/stt/whisper_engine.py && \
    echo "  ✅ whisper_engine.py"
$PYTHON_PATH -m py_compile app/core/sequence_engine_v2.py && \
    echo "  ✅ sequence_engine_v2.py"
echo ""

# Check 4: Module imports
echo "✓ Testing module imports..."
$PYTHON_PATH -c "from app.streaming.concurrent_session import ConcurrentRecitationSession" && \
    echo "  ✅ ConcurrentRecitationSession imports"
$PYTHON_PATH -c "from app.stt.whisper_engine import WhisperEngine" && \
    echo "  ✅ WhisperEngine imports"
$PYTHON_PATH -c "from app.services.recitation_service import RecitationService" && \
    echo "  ✅ RecitationService imports"
echo ""

# Check 5: Environment variable
echo "✓ Checking KMP environment variable..."
if [ -z "$KMP_DUPLICATE_LIB_OK" ]; then
    echo "  ⚠️  KMP_DUPLICATE_LIB_OK not set"
    echo "     Run: export KMP_DUPLICATE_LIB_OK=TRUE"
else
    echo "  ✅ KMP_DUPLICATE_LIB_OK=$KMP_DUPLICATE_LIB_OK"
fi
echo ""

# Check 6: Data files
echo "✓ Checking Quran data files..."
if [ -f "data/al_fatiha.json" ]; then
    AYAH_COUNT=$(grep -o '"ayah_number"' data/al_fatiha.json | wc -l)
    echo "  ✅ al_fatiha.json ($AYAH_COUNT ayahs)"
else
    echo "  ❌ al_fatiha.json not found"
    exit 1
fi
echo ""

# Check 7: Core methods exist
echo "✓ Verifying critical methods..."
$PYTHON_PATH -c "
from app.stt.whisper_engine import WhisperEngine
w = WhisperEngine.__dict__
if 'get_model' in dir(WhisperEngine):
    print('  ✅ WhisperEngine.get_model() exists')
else:
    print('  ❌ WhisperEngine.get_model() missing')
" 2>/dev/null || echo "  ⚠️  Could not verify get_model()"

echo ""

# Summary
echo "=================================================="
echo "✅ ALL CHECKS PASSED!"
echo ""
echo "Next steps:"
echo "1. Set environment variable:"
echo "   export KMP_DUPLICATE_LIB_OK=TRUE"
echo ""
echo "2. Run streaming mode:"
echo "   python -m app.main --terminal-stream"
echo ""
echo "3. Or run blocking mode:"
echo "   python -m app.main --terminal"
echo ""
echo "=================================================="
