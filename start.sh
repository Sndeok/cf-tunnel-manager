#!/bin/bash
# Cloudflare Tunnel Manager - Start Script
cd "$(dirname "$0")"

echo "=== Cloudflare Tunnel Manager ==="

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found"
    exit 1
fi

# Install deps
echo "📦 Installing dependencies..."
python3 -m pip install -q flask pyyaml

# Start
echo "🚀 Starting..."
echo "   Web UI: http://127.0.0.1:5000"
echo "   Press Ctrl+C to stop"
echo ""
python3 app.py
