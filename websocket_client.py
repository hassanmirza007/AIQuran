#!/usr/bin/env python3
"""
🎙️  WebSocket Client for Quran Recitation System
   - Real-time audio streaming
   - Automatic start detection
   - Live validation feedback
   - Statistics tracking
"""

import asyncio
import json
import sys
import sounddevice as sd
import numpy as np
import websockets
import requests
from typing import Optional
from datetime import datetime

# ==================== CONFIGURATION ====================
SERVER_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"
SAMPLE_RATE = 16000
CHUNK_DURATION = 0.1  # seconds
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)


# ==================== COLORS FOR TERMINAL ====================
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'


# ==================== SESSION MANAGER ====================
class SessionManager:
    """Manage session creation and lifecycle"""
    
    def __init__(self, surah_number: int = 1):
        self.surah_number = surah_number
        self.session_id: Optional[str] = None
        self.state = {
            "ayah_number": 1,
            "word_index": 0,
            "current_word": "",
            "progress": 0.0,
            "is_finished": False,
        }
        self.stats = {
            "total_words": 0,
            "correct_words": 0,
            "missed_words": 0,
            "incorrect_words": 0,
            "accuracy": 0.0,
        }
    
    def create_session(self) -> bool:
        """Create a new session via REST API"""
        try:
            print(f"\n{Colors.BLUE}[1] Creating session...{Colors.END}")
            response = requests.post(
                f"{SERVER_URL}/api/v1/sessions",
                json={"surah_number": self.surah_number},
                timeout=5
            )
            
            if response.status_code != 200:
                print(f"{Colors.RED}❌ Failed to create session: {response.status_code}{Colors.END}")
                print(f"   Response: {response.text}")
                return False
            
            data = response.json()
            self.session_id = data.get("session_id")
            
            print(f"{Colors.GREEN}✅ Session created!{Colors.END}")
            print(f"   Session ID: {Colors.BOLD}{self.session_id}{Colors.END}")
            print(f"   Surah: {self.surah_number}")
            return True
            
        except Exception as e:
            print(f"{Colors.RED}❌ Error creating session: {e}{Colors.END}")
            return False
    
    def get_stats(self) -> dict:
        """Fetch session statistics"""
        if not self.session_id:
            return {}
        
        try:
            response = requests.get(
                f"{SERVER_URL}/api/v1/sessions/{self.session_id}/stats",
                timeout=5
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"{Colors.YELLOW}⚠️  Could not fetch stats: {e}{Colors.END}")
        
        return {}
    
    def update_state(self, new_state: dict):
        """Update current session state"""
        self.state.update(new_state)
    
    def print_state(self):
        """Print current position and stats"""
        if self.state["is_finished"]:
            print(f"{Colors.GREEN}{Colors.BOLD}🎉 SURAH COMPLETE!{Colors.END}")
        else:
            ayah = self.state.get("ayah_number", "?")
            word = self.state.get("current_word", "?")
            progress = self.state.get("progress", 0)
            
            print(f"\n{Colors.CYAN}Ayah {ayah} | Word: {Colors.BOLD}{word}{Colors.END} | Progress: {progress:.1f}%")


# ==================== AUDIO RECORDER ====================
class AudioRecorder:
    """Record audio from microphone in real-time"""
    
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.chunk_size = CHUNK_SIZE
        self.is_recording = False
        self.audio_queue = asyncio.Queue()
    
    async def record_async(self) -> np.ndarray:
        """Record audio asynchronously (non-blocking)"""
        print(f"\n{Colors.YELLOW}🎤 Recording... (speak now){Colors.END}")
        
        try:
            # Record 5 seconds of audio
            duration = 5
            frames = int(self.sample_rate * duration)
            
            audio = sd.rec(
                frames,
                samplerate=self.sample_rate,
                channels=1,
                dtype='float32',
                blocking=True
            )
            sd.wait()
            
            print(f"{Colors.GREEN}✅ Recording complete{Colors.END}")
            return audio.flatten()
        
        except Exception as e:
            print(f"{Colors.RED}❌ Recording error: {e}{Colors.END}")
            return np.array([])
    
    async def stream_audio_chunks(self, audio: np.ndarray):
        """Convert audio to chunks and stream via WebSocket"""
        for i in range(0, len(audio), self.chunk_size):
            chunk = audio[i:i + self.chunk_size]
            
            # Convert numpy array to list for JSON serialization
            chunk_data = chunk.tolist()
            
            yield {
                "type": "audio_chunk",
                "data": chunk_data
            }
            
            # Simulate streaming delay
            await asyncio.sleep(CHUNK_DURATION * 0.5)


# ==================== WEBSOCKET HANDLER ====================
class WebSocketHandler:
    """Handle WebSocket connection and messages"""
    
    def __init__(self, session_manager: SessionManager):
        self.session = session_manager
        self.ws = None
        self.is_connected = False
    
    async def connect(self) -> bool:
        """Connect to WebSocket server"""
        if not self.session.session_id:
            print(f"{Colors.RED}❌ No session ID. Create session first.{Colors.END}")
            return False
        
        try:
            print(f"\n{Colors.BLUE}[2] Connecting WebSocket...{Colors.END}")
            uri = f"{WS_URL}/ws/session/{self.session.session_id}"
            self.ws = await websockets.connect(uri)
            self.is_connected = True
            
            print(f"{Colors.GREEN}✅ Connected to {uri}{Colors.END}")
            return True
        
        except Exception as e:
            print(f"{Colors.RED}❌ WebSocket connection failed: {e}{Colors.END}")
            return False
    
    async def send_audio_chunk(self, chunk_data: dict):
        """Send audio chunk to server"""
        if not self.ws:
            return False
        
        try:
            await self.ws.send(json.dumps(chunk_data))
            return True
        except Exception as e:
            print(f"{Colors.RED}❌ Error sending chunk: {e}{Colors.END}")
            return False
    
    async def receive_message(self) -> Optional[dict]:
        """Receive message from server"""
        if not self.ws:
            return None
        
        try:
            msg = await asyncio.wait_for(self.ws.recv(), timeout=10)
            return json.loads(msg)
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            print(f"{Colors.RED}❌ Error receiving message: {e}{Colors.END}")
            return None
    
    async def send_close(self):
        """Send close signal to server"""
        if not self.ws:
            return
        
        try:
            await self.ws.send(json.dumps({"type": "close"}))
            await self.ws.close()
            self.is_connected = False
        except Exception as e:
            print(f"{Colors.YELLOW}⚠️  Error closing WebSocket: {e}{Colors.END}")
    
    async def receive_messages(self):
        """Continuously receive messages from server"""
        while self.is_connected and self.ws:
            msg = await self.receive_message()
            
            if not msg:
                break
            
            yield msg


# ==================== MESSAGE PROCESSOR ====================
class MessageProcessor:
    """Process messages from server"""
    
    @staticmethod
    def process_listening_event(payload: dict, session: SessionManager):
        """Process LISTENING event"""
        word = payload.get("word", "?")
        ayah = payload.get("ayah", "?")
        word_index = payload.get("word_index", 0)
        progress = payload.get("progress", 0)
        
        session.update_state({
            "ayah_number": ayah,
            "word_index": word_index,
            "current_word": word,
            "progress": progress,
            "is_finished": False,
        })
        
        print(f"\n{Colors.BOLD}{Colors.CYAN}▶ Ayah {ayah}{Colors.END} | "
              f"Word {word_index}: {Colors.BOLD}{word}{Colors.END} | "
              f"Progress: {progress:.1f}%")
    
    @staticmethod
    def process_result_event(payload: dict, session: SessionManager):
        """Process RESULT event"""
        results = payload.get("results", [])
        state = payload.get("state", {})
        stats = payload.get("stats", {})
        
        # Update session state
        session.update_state(state)
        session.stats.update(stats)
        
        # Display results
        print(f"\n{Colors.BOLD}═════════════════════════════════════════{Colors.END}")
        print(f"{'Spoken':<20} {'Expected':<20} {'Status':<15}")
        print(f"{Colors.BOLD}─────────────────────────────────────────{Colors.END}")
        
        for result in results:
            spoken = result.get("spoken", "-")
            expected = result.get("expected", "-")
            status = result.get("status", "unknown")
            
            # Color code based on status
            if status == "correct":
                status_display = f"{Colors.GREEN}✅ {status}{Colors.END}"
            elif status == "missed":
                status_display = f"{Colors.YELLOW}⚠️  {status}{Colors.END}"
            elif status == "extra":
                status_display = f"{Colors.YELLOW}📝 {status}{Colors.END}"
            elif status == "incorrect":
                status_display = f"{Colors.RED}❌ {status}{Colors.END}"
            else:
                status_display = status
            
            print(f"{str(spoken):<20} {str(expected):<20} {status_display}")
        
        print(f"{Colors.BOLD}═════════════════════════════════════════{Colors.END}")
        
        # Display statistics
        print(f"\n{Colors.CYAN}📊 Statistics:{Colors.END}")
        print(f"   Accuracy: {Colors.BOLD}{stats.get('accuracy', 0):.1f}%{Colors.END}")
        print(f"   Correct: {Colors.GREEN}{stats.get('correct_words', 0)}{Colors.END} | "
              f"Missed: {Colors.YELLOW}{stats.get('missed_words', 0)}{Colors.END} | "
              f"Incorrect: {Colors.RED}{stats.get('incorrect_words', 0)}{Colors.END}")
    
    @staticmethod
    def process_finished_event(payload: dict, session: SessionManager):
        """Process FINISHED event"""
        state = payload.get("state", {})
        stats = payload.get("stats", {})
        
        session.update_state(state)
        session.stats.update(stats)
        session.state["is_finished"] = True
        
        print(f"\n{Colors.GREEN}{Colors.BOLD}🎉 SURAH COMPLETE!{Colors.END}")
        print(f"\n{Colors.CYAN}Final Statistics:{Colors.END}")
        print(f"   Total Words: {stats.get('total_words', 0)}")
        print(f"   Correct: {Colors.GREEN}{stats.get('correct_words', 0)}{Colors.END}")
        print(f"   Accuracy: {Colors.BOLD}{stats.get('accuracy', 0):.1f}%{Colors.END}")
    
    @staticmethod
    def process_error_event(payload: dict):
        """Process ERROR event"""
        message = payload.get("message", "Unknown error")
        print(f"\n{Colors.RED}❌ Server Error: {message}{Colors.END}")
    
    @staticmethod
    def process_message(msg: dict, session: SessionManager):
        """Process incoming message based on event type"""
        event = msg.get("event")
        payload = msg.get("payload", {})
        
        if event == "listening":
            MessageProcessor.process_listening_event(payload, session)
        elif event == "result":
            MessageProcessor.process_result_event(payload, session)
        elif event == "finished":
            MessageProcessor.process_finished_event(payload, session)
        elif event == "error":
            MessageProcessor.process_error_event(payload)
        else:
            print(f"Received unknown event: {event}")


# ==================== MAIN APPLICATION ====================
class QuranRecitationClient:
    """Main client application"""
    
    def __init__(self, surah_number: int = 1):
        self.session = SessionManager(surah_number)
        self.ws_handler = WebSocketHandler(self.session)
        self.recorder = AudioRecorder()
    
    async def run(self):
        """Main application loop"""
        print(f"\n{Colors.HEADER}{Colors.BOLD}🕌 Quran Recitation WebSocket Client{Colors.END}")
        print(f"{Colors.HEADER}════════════════════════════════════════{Colors.END}\n")
        
        # Step 1: Create session
        if not self.session.create_session():
            return
        
        # Step 2: Connect WebSocket
        if not await self.ws_handler.connect():
            return
        
        try:
            # Step 3: Start message receiver task
            receiver_task = asyncio.create_task(self.message_receiver())
            
            # Step 4: Record and stream audio
            while True:
                # Record audio
                audio = await self.recorder.record_async()
                
                if len(audio) == 0:
                    print(f"{Colors.RED}No audio recorded, skipping...{Colors.END}")
                    continue
                
                # Stream audio chunks
                print(f"\n{Colors.BLUE}[3] Streaming audio...{Colors.END}")
                async for chunk in self.recorder.stream_audio_chunks(audio):
                    await self.ws_handler.send_audio_chunk(chunk)
                
                # Wait for response
                await asyncio.sleep(1)
                
                # Check if finished
                if self.session.state.get("is_finished"):
                    print(f"\n{Colors.GREEN}Session complete!{Colors.END}")
                    break
                
                # Ask if user wants to continue
                response = input(f"\n{Colors.BOLD}Continue? (y/n): {Colors.END}").strip().lower()
                if response != "y":
                    break
        
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Interrupted by user{Colors.END}")
        
        finally:
            # Close WebSocket
            await self.ws_handler.send_close()
            print(f"\n{Colors.BLUE}Closing connection...{Colors.END}")
    
    async def message_receiver(self):
        """Receive messages from server in background"""
        async for msg in self.ws_handler.receive_messages():
            MessageProcessor.process_message(msg, self.session)


# ==================== ENTRY POINT ====================
async def main():
    """Application entry point"""
    try:
        client = QuranRecitationClient(surah_number=1)
        await client.run()
    except Exception as e:
        print(f"{Colors.RED}Fatal error: {e}{Colors.END}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
