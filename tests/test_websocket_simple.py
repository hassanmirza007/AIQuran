#!/usr/bin/env python3
"""
🎙️  WebSocket Client - SIMPLE TEST VERSION (No Microphone Required)
   - Test WebSocket connection
   - Send pre-recorded text
   - Receive live validation feedback
"""

import asyncio
import json
import requests
import websockets
from datetime import datetime

# ==================== CONFIGURATION ====================
SERVER_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"

# ==================== COLORS ====================
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'


async def test_websocket_simple():
    """Simple WebSocket test without microphone"""
    
    print(f"\n{Colors.HEADER}{Colors.BOLD}🕌 Quran Recitation WebSocket Test{Colors.END}")
    print(f"{Colors.HEADER}════════════════════════════════════════{Colors.END}\n")
    
    # Step 1: Create session
    print(f"{Colors.BLUE}[1] Creating session...{Colors.END}")
    response = requests.post(
        f"{SERVER_URL}/api/v1/sessions",
        json={"surah_number": 1},
        timeout=5
    )
    
    if response.status_code != 200:
        print(f"{Colors.RED}❌ Failed to create session{Colors.END}")
        return
    
    session_id = response.json()["session_id"]
    print(f"{Colors.GREEN}✅ Session created: {Colors.BOLD}{session_id}{Colors.END}")
    
    # Step 2: Connect WebSocket
    print(f"\n{Colors.BLUE}[2] Connecting WebSocket...{Colors.END}")
    uri = f"{WS_URL}/ws/session/{session_id}"
    
    try:
        async with websockets.connect(uri) as ws:
            print(f"{Colors.GREEN}✅ Connected!{Colors.END}\n")
            
            # Step 3: Receive initial listening event
            print(f"{Colors.BLUE}[3] Waiting for initial message...{Colors.END}")
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            
            print(f"{Colors.GREEN}✅ Received: {data['event']}{Colors.END}")
            print(f"   Next word: {Colors.BOLD}{data['payload']['word']}{Colors.END}")
            print(f"   Ayah: {data['payload']['ayah']}")
            print(f"   Progress: {data['payload']['progress']:.1f}%\n")
            
            # Step 4: Send sample audio chunk
            print(f"{Colors.BLUE}[4] Sending test audio chunk...{Colors.END}")
            
            # Create synthetic audio data (small values representing silence/noise)
            test_audio = {
                "type": "audio_chunk",
                "data": [0.001 * (i % 10 - 5) for i in range(100)]
            }
            
            await ws.send(json.dumps(test_audio))
            print(f"{Colors.GREEN}✅ Audio chunk sent{Colors.END}\n")
            
            # Step 5: Receive response
            print(f"{Colors.BLUE}[5] Waiting for response...{Colors.END}")
            
            responses_received = 0
            timeout_counter = 0
            
            while responses_received < 3 and timeout_counter < 5:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3)
                    data = json.loads(msg)
                    event = data.get("event")
                    payload = data.get("payload", {})
                    
                    if event == "listening":
                        print(f"\n{Colors.CYAN}▶ LISTENING Event:{Colors.END}")
                        print(f"   Next word: {Colors.BOLD}{payload.get('word')}{Colors.END}")
                        print(f"   Progress: {payload.get('progress'):.1f}%")
                        responses_received += 1
                    
                    elif event == "result":
                        print(f"\n{Colors.CYAN}▶ RESULT Event:{Colors.END}")
                        results = payload.get("results", [])
                        for r in results:
                            status_icon = "✅" if r["status"] == "correct" else "❌"
                            print(f"   {status_icon} {r.get('spoken', '-')} → {r.get('expected', '-')} ({r['status']})")
                        
                        stats = payload.get("stats", {})
                        print(f"   Accuracy: {stats.get('accuracy', 0):.1f}%")
                        responses_received += 1
                    
                    elif event == "finished":
                        print(f"\n{Colors.GREEN}{Colors.BOLD}🎉 FINISHED Event:{Colors.END}")
                        stats = payload.get("stats", {})
                        print(f"   Final Accuracy: {stats.get('accuracy', 0):.1f}%")
                        responses_received += 3  # Break loop
                    
                    elif event == "error":
                        print(f"\n{Colors.RED}❌ ERROR: {payload.get('message')}{Colors.END}")
                        responses_received += 3  # Break loop
                    
                    else:
                        print(f"   Event: {event}")
                
                except asyncio.TimeoutError:
                    timeout_counter += 1
                    if timeout_counter < 5:
                        print(f"   ⏱️  Waiting ({timeout_counter}/5)...")
            
            # Step 6: Send close message
            print(f"\n{Colors.BLUE}[6] Closing connection...{Colors.END}")
            await ws.send(json.dumps({"type": "close"}))
            print(f"{Colors.GREEN}✅ Closed{Colors.END}\n")
            
            print(f"{Colors.GREEN}{Colors.BOLD}✅ WebSocket test completed successfully!{Colors.END}\n")
    
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")


if __name__ == "__main__":
    asyncio.run(test_websocket_simple())
