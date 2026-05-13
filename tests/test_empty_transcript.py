#!/usr/bin/env python3
"""
Test the audio fix: Ensure empty transcripts don't advance cursor or affect stats
"""

import sys
import asyncio
import json
import websockets
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

async def test_empty_transcript_handling():
    """Test that empty transcripts don't advance cursor or update stats"""
    
    print("\n" + "="*70)
    print("TEST: Empty Transcript Handling")
    print("="*70)
    
    # Create session
    print("\n[1] Creating session...")
    import requests
    
    response = requests.post(
        "http://localhost:8000/api/v1/sessions",
        json={"surah_number": 1}
    )
    
    if response.status_code != 200:
        print(f"❌ Failed to create session: {response.text}")
        return False
    
    session_data = response.json()
    session_id = session_data["session_id"]
    print(f"✅ Session created: {session_id[:8]}...")
    
    # Get initial stats
    print("\n[2] Checking initial stats...")
    stats_response = requests.get(
        f"http://localhost:8000/api/v1/sessions/{session_id}/stats"
    )
    initial_stats = stats_response.json()
    print(f"Initial stats: {initial_stats}")
    
    # Connect WebSocket
    print("\n[3] Connecting to WebSocket...")
    ws_url = f"ws://localhost:8000/ws/session/{session_id}"
    
    async with websockets.connect(ws_url) as ws:
        # Receive initial LISTENING event
        msg = await ws.recv()
        initial_event = json.loads(msg)
        print(f"✅ Connected, initial word: {initial_event['payload']['word']}")
        initial_word = initial_event['payload']['word']
        initial_progress = initial_event['payload']['progress']
        
        # Send an empty audio chunk (simulating silence)
        print("\n[4] Sending empty audio chunk (simulating silence)...")
        await ws.send(json.dumps({
            "type": "audio_chunk",
            "data": []
        }))
        
        # Wait a bit to see if anything is sent back
        print("    Waiting for response...")
        try:
            # Set a short timeout to see if we get any response
            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            event = json.loads(msg)
            print(f"⚠️ UNEXPECTED: Received event: {event['event']}")
            print(f"   This should NOT happen for empty transcripts!")
            return False
        except asyncio.TimeoutError:
            print("✅ No response sent (expected behavior for empty transcripts)")
        
        # Check that cursor didn't advance
        print("\n[5] Checking if cursor advanced...")
        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
        event = json.loads(msg)
        
        if event['event'] == 'listening':
            new_word = event['payload']['word']
            new_progress = event['payload']['progress']
            
            if new_word == initial_word and new_progress == initial_progress:
                print(f"✅ Cursor stayed at same position: {initial_word} (progress: {initial_progress}%)")
            else:
                print(f"❌ Cursor advanced unexpectedly!")
                print(f"   Before: {initial_word} ({initial_progress}%)")
                print(f"   After:  {new_word} ({new_progress}%)")
                return False
        
        # Close connection
        await ws.send(json.dumps({"type": "close"}))
    
    # Check final stats
    print("\n[6] Checking final stats...")
    stats_response = requests.get(
        f"http://localhost:8000/api/v1/sessions/{session_id}/stats"
    )
    final_stats = stats_response.json()
    print(f"Final stats: {final_stats}")
    
    if final_stats['total'] == 0:
        print("✅ Statistics unchanged (still 0 words)")
        return True
    else:
        print(f"❌ Statistics were updated unexpectedly: {final_stats}")
        return False

if __name__ == "__main__":
    try:
        result = asyncio.run(test_empty_transcript_handling())
        if result:
            print("\n" + "="*70)
            print("✅ ALL TESTS PASSED - Empty transcripts handled correctly!")
            print("="*70)
            sys.exit(0)
        else:
            print("\n" + "="*70)
            print("❌ TEST FAILED - Fix not working properly")
            print("="*70)
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
