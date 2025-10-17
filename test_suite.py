import os
import time
import requests
import pytest

# --- Test Configuration ---
BASE_URL = "http://localhost:4000"
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise ValueError("API_KEY environment variable not set for testing.")

HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

# --- Fixtures for Setup ---

@pytest.fixture(scope="session", autouse=True)
def wait_for_api():
    """
    This is a session-scoped, autouse fixture. It runs once before any tests.
    It repeatedly pings the /health endpoint until the API is responsive.
    """
    print("\n--- SETUP: Waiting for API to be ready ---")
    start_time = time.time()
    while time.time() - start_time < 30: # 30-second timeout
        try:
            response = requests.get(f"{BASE_URL}/health", timeout=2)
            if response.status_code == 200:
                print("--- SETUP: API is ready! ---")
                return
        except requests.exceptions.ConnectionError:
            pass # Ignore connection errors while waiting
        time.sleep(1)
    pytest.fail("SETUP FAILED: API was not ready within 30 seconds.")


@pytest.fixture(scope="module")
def cached_song_filename():
    """
    Runs after the API is confirmed to be ready. Caches one song for other tests.
    """
    print("\n--- SETUP: Caching a song for tests ---")
    payload = {"song_name": "Comfortably Numb", "artist": "Pink Floyd"}
    try:
        response = requests.post(f"{BASE_URL}/api/play", headers=HEADERS, json=payload, timeout=20)
        response.raise_for_status() # Raise an exception for bad status codes
        data = response.json()
        filename = data['stream_url'].split('/')[-1]
        print(f"--- SETUP: Successfully cached song: {filename} ---")
        return filename
    except requests.exceptions.RequestException as e:
        pytest.fail(f"SETUP FAILED: Could not cache a song. Error: {e}")

# --- Test Cases ---

def test_health_check():
    """Tests the unauthenticated /health endpoint."""
    print("\n>>> Testing /health endpoint...")
    response = requests.get(f"{BASE_URL}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_tracks_endpoint_unauthorized():
    """Tests that protected endpoints fail without an API key."""
    print("\n>>> Testing /api/tracks for unauthorized access...")
    response = requests.get(f"{BASE_URL}/api/tracks")
    assert response.status_code == 401

def test_tracks_endpoint_authorized(cached_song_filename):
    """Tests that the /api/tracks endpoint returns a list of tracks."""
    print("\n>>> Testing /api/tracks for authorized access...")
    response = requests.get(f"{BASE_URL}/api/tracks", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert any(track['fileName'] == cached_song_filename for track in data)

def test_stream_endpoint_range_request(cached_song_filename):
    """Tests that the streaming endpoint correctly handles partial content requests."""
    print(f"\n>>> Testing /api/stream/{cached_song_filename} for range requests...")
    url = f"{BASE_URL}/api/stream/{cached_song_filename}"
    range_headers = {"X-API-Key": API_KEY, "Range": "bytes=0-1023"}
    response = requests.get(url, headers=range_headers)
    
    assert response.status_code == 206
    assert int(response.headers['Content-Length']) == 1024
    assert response.headers['Content-Range'].startswith("bytes 0-1023/")

# ... (You can add more tests like test_stream_full_file if you wish)