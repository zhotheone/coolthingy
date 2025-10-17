import os
import time
import requests
import pytest

# --- Test Configuration ---
# Ensure the API is running before these tests are executed.
BASE_URL = "http://localhost:4000"
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise ValueError("API_KEY environment variable not set for testing.")

HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

# Use pytest's 'fixture' to share data between tests in a clean way.
@pytest.fixture(scope="module")
def cached_song_filename():
    """
    This function runs once before all tests. It ensures at least one song
    is cached and provides its filename to other tests.
    """
    print("\n--- SETUP: Caching a song for tests ---")
    payload = {"song_name": "Comfortably Numb", "artist": "Pink Floyd"}
    
    # It might take a moment for the service to be fully ready.
    # Retry a few times if the first attempt fails.
    for i in range(3):
        try:
            response = requests.post(f"{BASE_URL}/api/play", headers=HEADERS, json=payload, timeout=20)
            if response.status_code == 200:
                data = response.json()
                filename = data['stream_url'].split('/')[-1]
                print(f"--- SETUP: Successfully cached song with filename: {filename} ---")
                return filename
            time.sleep(5)
        except requests.exceptions.RequestException as e:
            print(f"--- SETUP: Attempt {i+1} failed to connect: {e} ---")
            time.sleep(5)
            
    pytest.fail("SETUP FAILED: Could not cache a song after several retries.")


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
    # Check if our cached song is in the list
    assert any(track['fileName'] == cached_song_filename for track in data)

def test_stream_endpoint_range_request(cached_song_filename):
    """Tests that the streaming endpoint correctly handles partial content (Range) requests."""
    print(f"\n>>> Testing /api/stream/{cached_song_filename} for range requests...")
    url = f"{BASE_URL}/api/stream/{cached_song_filename}"
    range_headers = {"X-API-Key": API_KEY, "Range": "bytes=0-1023"}
    response = requests.get(url, headers=range_headers)
    
    assert response.status_code == 206, "Status code must be 206 Partial Content for a range request"
    assert response.headers['Content-Type'] == 'audio/opus'
    assert int(response.headers['Content-Length']) == 1024
    assert "Content-Range" in response.headers
    assert response.headers['Content-Range'].startswith("bytes 0-1023/")

def test_stream_full_file(cached_song_filename):
    """Tests that the streaming endpoint can serve a full file."""
    print(f"\n>>> Testing /api/stream/{cached_song_filename} for full file download...")
    url = f"{BASE_URL}/api/stream/{cached_song_filename}"
    response = requests.get(url, headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'audio/opus'
    assert int(response.headers['Content-Length']) > 1024 # Should be a full file, so larger than our range test