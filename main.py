import os
import re
import uuid
import time
import threading
import logging
import psycopg2
import requests
import base64
from psycopg2 import pool
from psycopg2.extras import DictCursor
from flask import Flask, request, jsonify, send_file, Response, abort, g
from flask_cors import CORS
from mutagen.oggopus import OggOpus as Opus
import yt_dlp
from functools import wraps
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# ==============================================================================
# --- 1. LOGGING AND CONFIGURATION SETUP ---
# ==============================================================================

# Setup structured logging for the application
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Define and validate all required environment variables using Pydantic
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')
    NEON_CONNECTION_STRING: str
    API_KEY: SecretStr
    SPOTIFY_CLIENT_ID: str
    SPOTIFY_CLIENT_SECRET: SecretStr
    SPOTIFY_REFRESH_TOKEN: SecretStr

try:
    settings = Settings()
    logging.info("Configuration loaded and validated successfully.")
except ValidationError as e:
    logging.critical(f"FATAL: Configuration validation error. Please check your environment variables.\n{e}")
    exit(1)

# ==============================================================================
# --- 2. FLASK APP INITIALIZATION AND CORE SETUP ---
# ==============================================================================
app = Flask(__name__)
CORS(app)

MUSIC_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music')
TOKEN_ENDPOINT = 'https://accounts.spotify.com/api/token'
NOW_PLAYING_ENDPOINT = 'https://api.spotify.com/v1/me/player/currently-playing'

# Constants for cache size management (in bytes)
CACHE_LIMIT_BYTES = 3 * 1024 * 1024 * 1024  # 3 GB
CACHE_TARGET_BYTES = 2.5 * 1024 * 1024 * 1024 # Target 2.5 GB after cleanup

# Global lock to prevent multiple cleanup processes from running simultaneously
cleanup_lock = threading.Lock()

try:
    db_connection_pool = psycopg2.pool.SimpleConnectionPool(minconn=1, maxconn=10, dsn=settings.NEON_CONNECTION_STRING)
    logging.info("Database connection pool created successfully.")
except psycopg2.OperationalError as err:
    logging.critical(f"FATAL: Error creating database connection pool: {err}")
    exit(1)

def get_db_connection():
    return db_connection_pool.getconn()

def return_db_connection(conn):
    db_connection_pool.putconn(conn)

def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key') or request.args.get('apiKey')
        if not api_key or api_key != settings.API_KEY.get_secret_value():
            abort(401, description="Unauthorized: Invalid or missing API Key.")
        return f(*args, **kwargs)
    return decorated_function

# --- Request/Response logging middleware ---
@app.before_request
def start_request_logging():
    request_id = request.headers.get('X-Request-Id') or str(uuid.uuid4())
    g.request_id = request_id
    logging.info(f"[{request_id}] IN  {request.method} {request.path} from={request.remote_addr} args={dict(request.args)}")
    if request.is_json and request.get_data(as_text=True):
        logging.info(f"[{request_id}] IN  payload={request.get_json(silent=True)}")

@app.after_request
def end_request_logging(response):
    request_id = getattr(g, 'request_id', '-')
    content_length = response.headers.get('Content-Length', '-')
    logging.info(f"[{request_id}] OUT {response.status_code} {request.method} {request.path} size={content_length}")
    if request_id:
        response.headers['X-Request-Id'] = request_id
    return response

# ==============================================================================
# --- 3. HELPER AND BACKGROUND FUNCTIONS ---
# ==============================================================================
def get_spotify_access_token():
    client_id = settings.SPOTIFY_CLIENT_ID
    client_secret = settings.SPOTIFY_CLIENT_SECRET.get_secret_value()
    refresh_token = settings.SPOTIFY_REFRESH_TOKEN.get_secret_value()
    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = requests.post(TOKEN_ENDPOINT,
        headers={'Authorization': f'Basic {auth_header}', 'Content-Type': 'application/x-www-form-urlencoded'},
        data={'grant_type': 'refresh_token', 'refresh_token': refresh_token}
    )
    response.raise_for_status()
    return response.json()['access_token']

def cleanup_cache():
    if not cleanup_lock.acquire(blocking=False):
        logging.info("CLEANUP: Cleanup process is already running. Skipping.")
        return

    logging.info("CLEANUP: Starting cache cleanup check.")
    try:
        total_size = sum(os.path.getsize(os.path.join(MUSIC_DIRECTORY, f)) for f in os.listdir(MUSIC_DIRECTORY) if os.path.isfile(os.path.join(MUSIC_DIRECTORY, f)))
        
        if total_size <= CACHE_LIMIT_BYTES:
            logging.info(f"CLEANUP: Cache size is {total_size / (1024**3):.2f} GB. No cleanup needed.")
            return

        logging.warning(f"CLEANUP: Cache size {total_size / (1024**3):.2f} GB exceeds limit of {CACHE_LIMIT_BYTES / (1024**3):.2f} GB. Starting cleanup.")
        
        bytes_to_delete = total_size - CACHE_TARGET_BYTES
        deleted_bytes_total = 0
        deleted_files_count = 0

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT file_name FROM tracks WHERE status = 'cached' ORDER BY last_accessed_at ASC")
        tracks_to_delete = cursor.fetchall()

        for track in tracks_to_delete:
            if deleted_bytes_total >= bytes_to_delete:
                break

            file_name = track['file_name']
            if not file_name: continue

            file_path = os.path.join(MUSIC_DIRECTORY, file_name)
            if os.path.exists(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    
                    delete_cursor = conn.cursor()
                    delete_cursor.execute("DELETE FROM tracks WHERE file_name = %s", (file_name,))
                    conn.commit()
                    delete_cursor.close()

                    deleted_bytes_total += file_size
                    deleted_files_count += 1
                    logging.info(f"CLEANUP: Deleted '{file_name}' ({file_size / (1024**2):.2f} MB)")
                except Exception as e:
                    logging.error(f"CLEANUP: Error deleting file {file_name}: {e}")
        
        cursor.close()
        return_db_connection(conn)
        logging.info(f"CLEANUP: Finished. Deleted {deleted_files_count} files, freeing {deleted_bytes_total / (1024**2):.2f} MB.")
    except Exception as e:
        logging.error(f"CLEANUP: An unexpected error occurred during cleanup: {e}", exc_info=True)
    finally:
        cleanup_lock.release()

def download_and_cache_track(search_query, song_name, artist):
    logging.info(f"BACKGROUND: Starting Opus download for '{search_query}'")
    conn = None
    try:
        conn = psycopg2.connect(dsn=settings.NEON_CONNECTION_STRING)
        cursor = conn.cursor()
        unique_filename = f"{uuid.uuid4()}.opus"
        final_filepath = os.path.join(MUSIC_DIRECTORY, unique_filename)
        temp_output_path = os.path.join(MUSIC_DIRECTORY, os.path.splitext(unique_filename)[0])
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'opus', 'preferredquality': '96'}],
            'outtmpl': temp_output_path, 'quiet': True, 'nocheckcertificate': True, 'cookiefile': '/app/cookies.txt'
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(f"ytsearch1:{artist} {song_name} audio", download=True)
            if not info_dict or 'entries' not in info_dict or not info_dict['entries']:
                 raise RuntimeError("yt-dlp could not find a suitable video to download.")
            original_filepath = info_dict['entries'][0].get('requested_downloads')[0]['filepath']

        if not os.path.exists(original_filepath):
             raise FileNotFoundError(f"yt-dlp reported path {original_filepath}, but file does not exist.")
        
        os.rename(original_filepath, final_filepath)
        logging.info(f"BACKGROUND: Renamed downloaded file to {unique_filename}")

        audio = Opus(final_filepath)
        update_sql = """
            UPDATE tracks SET status = 'cached', file_name = %s, title = %s, artist = %s,
                album = %s, duration = %s, cached_at = NOW(), last_accessed_at = NOW() WHERE search_query = %s;
        """
        values = (unique_filename, audio.get('title', [''])[0] or song_name, audio.get('artist', [''])[0] or artist, audio.get('album', [''])[0], audio.info.length, search_query)
        cursor.execute(update_sql, values)
        conn.commit()
        logging.info(f"BACKGROUND: Successfully cached '{search_query}' as {unique_filename}")

        threading.Thread(target=cleanup_cache).start()
    except Exception as e:
        logging.error(f"BACKGROUND ERROR: Failed to download '{search_query}': {e}", exc_info=True)
        if conn:
            try:
                error_sql = "UPDATE tracks SET status = 'error' WHERE search_query = %s;"
                cursor.execute(error_sql, (search_query,))
                conn.commit()
            except Exception as db_err:
                logging.critical(f"BACKGROUND FATAL: Could not write error status to DB: {db_err}")
    finally:
        if conn:
            cursor.close()
            conn.close()

# ==============================================================================
# --- 4. API ENDPOINTS ---
# ==============================================================================
@app.route('/api/now-playing', methods=['GET'])
@require_api_key
def get_now_playing():
    conn = None
    try:
        access_token = get_spotify_access_token()
        response = requests.get(NOW_PLAYING_ENDPOINT, headers={'Authorization': f'Bearer {access_token}'})
        response_timestamp = time.time()
        if response.status_code == 204 or not response.content:
            return jsonify({"status": "not_playing"})
        response.raise_for_status()
        data = response.json()
        if not data or not data.get('item'):
            return jsonify({"status": "not_playing"})

        artist_str = ", ".join([artist['name'] for artist in data['item']['artists']])
        title_str = data['item']['name']
        search_query = f"{artist_str.lower().strip()} - {title_str.lower().strip()}"
        track_info = {
            "id": data['item']['id'], "title": title_str, "artist": artist_str,
            "albumImageUrl": data['item']['album']['images'][0]['url'] if data['item']['album']['images'] else None,
            "isPlaying": data['is_playing'], "timePlayed": data['progress_ms'], "timeTotal": data['item']['duration_ms'],
            "timestamp": response_timestamp
        }

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT file_name, status FROM tracks WHERE search_query = %s", (search_query,))
        result = cursor.fetchone()

        file_is_missing = False
        if result and result['status'] == 'cached':
            if not result['file_name'] or not os.path.exists(os.path.join(MUSIC_DIRECTORY, result['file_name'])):
                logging.warning(f"File for cached track '{search_query}' is missing. Triggering re-download.")
                file_is_missing = True

        if not result or file_is_missing:
            track_info["status"] = "caching"
            if file_is_missing:
                cursor.execute("UPDATE tracks SET status = 'caching', file_name = NULL WHERE search_query = %s", (search_query,))
            else:
                insert_sql = "INSERT INTO tracks (search_query, status) VALUES (%s, 'caching') ON CONFLICT (search_query) DO NOTHING;"
                cursor.execute(insert_sql, (search_query,))
            conn.commit()
            download_thread = threading.Thread(target=download_and_cache_track, args=(search_query, title_str, artist_str))
            download_thread.start()
        else:
            track_info["status"] = result['status']
            
        return jsonify(track_info)
    except requests.exceptions.RequestException as e:
        logging.error(f"Error communicating with Spotify: {e}")
        return jsonify({"error": "Failed to fetch data from Spotify."}), 502
    except psycopg2.Error as err:
        logging.error(f"Database error in now-playing: {err}")
        return jsonify({"error": "A database error occurred."}), 500
    except Exception as e:
        logging.error(f"An unexpected error occurred in get_now_playing: {e}", exc_info=True)
        return jsonify({"error": "An internal server error occurred."}), 500
    finally:
        if conn: return_db_connection(conn)

@app.route('/api/tracks', methods=['GET'])
@require_api_key
def list_tracks():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT file_name, title, artist, album, duration FROM tracks WHERE status = 'cached' ORDER BY cached_at DESC")
        tracks = [dict(row) for row in cursor.fetchall()]
        for track in tracks:
            track['fileName'] = track.pop('file_name')
        return jsonify(tracks)
    except psycopg2.Error as err:
        logging.error(f"Database error: {err}")
        return jsonify({"error": "Could not retrieve tracks."}), 500
    finally:
        if conn: return_db_connection(conn)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

@app.route('/api/play', methods=['POST'])
@require_api_key
def get_streamable_track():
    if not request.is_json: return jsonify({"error": "Content-Type must be application/json."}), 415
    data = request.get_json()
    song_name, artist = data.get('song_name'), data.get('artist')
    if not all([isinstance(song_name, str), isinstance(artist, str), song_name.strip(), artist.strip()]):
        return jsonify({"error": "'song_name' and 'artist' must be non-empty strings."}), 400
    search_query = f"{artist.lower().strip()} - {song_name.lower().strip()}"
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT file_name FROM tracks WHERE search_query = %s AND status = 'cached'", (search_query,))
        result = cursor.fetchone()
        if result and os.path.exists(os.path.join(MUSIC_DIRECTORY, result['file_name'])):
            return jsonify({"message": "Track is ready.", "stream_url": f"/api/stream/{result['file_name']}"})
        else:
            return jsonify({"error": "Track not found in cache. It may still be downloading."}), 404
    except psycopg2.Error as err:
        logging.error(f"Database error in play: {err}")
        return jsonify({"error": "A database error occurred."}), 500
    finally:
        if conn: return_db_connection(conn)

@app.route('/api/stream/<string:file_name>')
@require_api_key
def stream_track(file_name):
    music_file_path = os.path.join(MUSIC_DIRECTORY, file_name)
    if not os.path.abspath(music_file_path).startswith(os.path.abspath(MUSIC_DIRECTORY)):
        abort(403, "Access denied.")
    if not os.path.exists(music_file_path):
        abort(404, "Track not found in cache.")
    
    def update_access_time():
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE tracks SET last_accessed_at = NOW() WHERE file_name = %s", (file_name,))
            conn.commit()
            cursor.close()
        except Exception as e:
            logging.error(f"Error updating access time for {file_name}: {e}")
        finally:
            if conn: return_db_connection(conn)
    threading.Thread(target=update_access_time).start()
    
    range_header, file_size = request.headers.get('Range', None), os.path.getsize(music_file_path)
    if not range_header:
        return send_file(music_file_path, mimetype='audio/opus')
    start, end = 0, file_size - 1
    match = re.search(r'bytes=(\d+)-(\d*)', range_header)
    if match:
        start = int(match.groups()[0])
        if match.groups()[1]: end = int(match.groups()[1])
    if start >= file_size or end >= file_size:
        abort(416, "Requested Range Not Satisfiable")
    length = (end - start) + 1
    def generate_chunks():
        with open(music_file_path, 'rb') as f:
            f.seek(start)
            yield f.read(length)
    resp = Response(generate_chunks(), 206, mimetype='audio/opus', direct_passthrough=True)
    resp.headers.add('Content-Range', f'bytes {start}-{end}/{file_size}')
    resp.headers.add('Accept-Ranges', 'bytes')
    resp.headers.add('Content-Length', str(length))
    return resp

# ==============================================================================
# --- 5. MAIN EXECUTION BLOCK ---
# ==============================================================================
if __name__ == '__main__':
    if not os.path.exists(MUSIC_DIRECTORY):
        os.makedirs(MUSIC_DIRECTORY)
        logging.info(f"Music directory created at: {MUSIC_DIRECTORY}")
    app.run(debug=True, port=4000)