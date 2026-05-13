#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "================================================"
echo "  🎙️  Quran Recitation Validator - Quick Start"
echo "================================================"
echo -e "${NC}"

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Virtual environment not found. Creating...${NC}"
    python3 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# Export environment variable for Whisper
export KMP_DUPLICATE_LIB_OK=TRUE

echo -e "${GREEN}✓ Environment activated${NC}"
echo ""

# Display menu
echo -e "${BLUE}Choose an option:${NC}"
echo ""
echo "1) Start Server (for WebSocket streaming)"
echo "2) Open Web UI (opens in browser)"
echo "3) Open Audio Debug Tool (test microphone)"
echo "4) Run Simple Test (no UI required)"
echo "5) Exit"
echo ""

read -p "Enter option (1-5): " choice

case $choice in
    1)
        echo -e "${GREEN}Starting server...${NC}"
        echo "Server will run on: http://localhost:8000"
        echo "Press Ctrl+C to stop"
        echo ""
        python -m app.main
        ;;
    2)
        echo -e "${GREEN}Opening Web UI in browser...${NC}"
        
        # Check if Python HTTP server is available
        if command -v python3 &> /dev/null; then
            # Check if port 5000 is available
            if ! nc -z localhost 5000 2>/dev/null; then
                echo "Starting local HTTP server on port 5000..."
                cd /Users/harshad/Documents/AQ_1
                python3 -m http.server 5000 > /dev/null 2>&1 &
                HTTP_SERVER_PID=$!
                sleep 2
                echo -e "${GREEN}✓ Server started (PID: $HTTP_SERVER_PID)${NC}"
            fi
        fi
        
        # Open in browser
        open "http://localhost:5000/index.html"
        echo -e "${GREEN}✓ Opening browser...${NC}"
        echo "Make sure the main server is running: python -m app.main"
        ;;
    3)
        echo -e "${GREEN}Opening Audio Debug Tool in browser...${NC}"
        
        # Check if Python HTTP server is available
        if command -v python3 &> /dev/null; then
            # Check if port 5000 is available
            if ! nc -z localhost 5000 2>/dev/null; then
                echo "Starting local HTTP server on port 5000..."
                python3 -m http.server 5000 > /dev/null 2>&1 &
                HTTP_SERVER_PID=$!
                sleep 2
                echo -e "${GREEN}✓ Server started (PID: $HTTP_SERVER_PID)${NC}"
            fi
        fi
        
        # Open debug tool
        open "http://localhost:5000/audio-debug.html"
        echo -e "${GREEN}✓ Opening audio debug tool...${NC}"
        ;;
    4)
        echo -e "${GREEN}Running simple WebSocket test...${NC}"
        echo ""
        
        # Check if main server is running
        if nc -z localhost 8000 2>/dev/null; then
            python test_websocket_simple.py
        else
            echo -e "${RED}✗ Main server is not running!${NC}"
            echo "Start it first with option 1"
        fi
        ;;
    5)
        echo -e "${BLUE}Goodbye!${NC}"
        exit 0
        ;;
    *)
        echo -e "${RED}Invalid option. Please try again.${NC}"
        exit 1
        ;;
esac
