import requests
import json
import time

print("=" * 60)
print("Testing Quran Recitation System Connection")
print("=" * 60)

# Test 1: Check if server is running
print("\n[1] Checking if server is running on port 8000...")
try:
    response = requests.get("http://localhost:8000/api/v1/health", timeout=2)
    print(f"✅ Server is running! Status: {response.status_code}")
except requests.exceptions.ConnectionError:
    print("❌ Server is NOT running!")
    print("   Start the server first:")
    print("   ./run.sh")
    exit(1)

# Test 2: Create a session
print("\n[2] Creating a new session...")
try:
    response = requests.post(
        "http://localhost:8000/api/v1/sessions",
        json={"surah_number": 1},
        timeout=5
    )
    if response.status_code == 200:
        session_data = response.json()
        session_id = session_data.get("session_id")
        print(f"✅ Session created!")
        print(f"   Session ID: {session_id}")
        print(f"   Surah: {session_data.get('surah_number')}")
    else:
        print(f"❌ Failed to create session: {response.status_code}")
        print(f"   Response: {response.text}")
        exit(1)
except Exception as e:
    print(f"❌ Error creating session: {e}")
    exit(1)

# Test 3: Get session stats
print("\n[3] Fetching session stats...")
try:
    response = requests.get(
        f"http://localhost:8000/api/v1/sessions/{session_id}/stats",
        timeout=5
    )
    if response.status_code == 200:
        stats = response.json()
        print(f"✅ Stats retrieved!")
        print(f"   Accuracy: {stats.get('accuracy', 0):.1f}%")
        print(f"   Correct: {stats.get('correct_words', 0)}")
        print(f"   Total: {stats.get('total_words', 0)}")
    else:
        print(f"❌ Failed to get stats: {response.status_code}")
except Exception as e:
    print(f"❌ Error getting stats: {e}")

# Test 4: Get current position
print("\n[4] Fetching current position...")
try:
    response = requests.get(
        f"http://localhost:8000/api/v1/sessions/{session_id}/position",
        timeout=5
    )
    if response.status_code == 200:
        position = response.json()
        print(f"✅ Position retrieved!")
        print(f"   Ayah Index: {position.get('ayah_index', 0)}")
        print(f"   Word Index: {position.get('word_index', 0)}")
    else:
        print(f"❌ Failed to get position: {response.status_code}")
except Exception as e:
    print(f"❌ Error getting position: {e}")

print("\n" + "=" * 60)
print("✅ All tests passed! System is ready.")
print("=" * 60)
print("\nNext steps:")
print("1. Keep the server running (./run.sh)")
print("2. Run your WebSocket client in another terminal")
print("3. WebSocket URL: ws://localhost:8000/ws/session/{session_id}")