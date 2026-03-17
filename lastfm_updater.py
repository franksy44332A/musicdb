import sqlite3
import pylast
import time
import json
from datetime import datetime

# ===== CONFIGURATION =====
API_KEY = "34128edb40451a9342b4c1ea124b7bed"
API_SECRET = "eeb7d38564276e52853581da81b4ab76"
USERNAME = "kattfarfars"
DB_FILE = "music.db"
TOP_TRACKS_LIMIT = 1000
# =========================

network = pylast.LastFMNetwork(api_key=API_KEY, api_secret=API_SECRET)
user = network.get_user(USERNAME)

def ensure_columns():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(songs)")
    columns = [col[1] for col in cursor.fetchall()]
    if "last_played" not in columns:
        cursor.execute("ALTER TABLE songs ADD COLUMN last_played TEXT")
        conn.commit()
    conn.close()

def update_top_tracks():
    print("Fetching top tracks from Last.fm...")
    try:
        top_tracks = user.get_top_tracks(limit=TOP_TRACKS_LIMIT)
    except Exception as e:
        print(f"Error fetching top tracks: {e}")
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    updated = 0
    
    for track in top_tracks:
        artist = track.item.artist.name
        song = track.item.title
        playcount = track.weight
        cursor.execute('''
            UPDATE songs
            SET scrobbles = ?
            WHERE Artist = ? AND Song = ?
        ''', (playcount, artist, song))
        if cursor.rowcount > 0:
            updated += 1
    
    conn.commit()
    conn.close()
    print(f"✅ Updated {updated} songs with accurate play counts")

def get_now_playing():
    """Fetch currently playing track with album name"""
    try:
        now_playing_track = user.get_now_playing()
        if now_playing_track:
            artist = now_playing_track.artist.name
            song = now_playing_track.title
            # Try to get album name
            album = None
            try:
                # pylast Track may have get_album() method
                album_obj = now_playing_track.get_album()
                if album_obj:
                    album = album_obj.get_name()
            except:
                pass
            print(f"🎵 Now Playing: {artist} - {song} [{album or 'no album'}]")
            return {
                'artist': artist,
                'song': song,
                'album': album,
                'timestamp': 'Now',
                'checked_at': datetime.now().isoformat()
            }
    except Exception as e:
        print(f"Error in get_now_playing: {e}")
    return None

def get_recent_tracks_info():
    """Fetch recent tracks, return most recent with album, and update last_played in DB"""
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Fetching recent tracks...")
    
    recent_tracks = user.get_recent_tracks(limit=10)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    most_recent = None
    for track in recent_tracks:
        artist = track.track.artist.name
        song = track.track.title
        played_time = track.playback_date
        # Get album from track if available
        album = None
        try:
            album_obj = track.track.get_album()
            if album_obj:
                album = album_obj.get_name()
        except:
            pass
        
        # Update last_played in DB (store with timestamp)
        cursor.execute('''
            UPDATE songs
            SET last_played = ?
            WHERE Artist = ? AND Song = ?
        ''', (played_time, artist, song))
        
        # Capture the most recent track (first in list)
        if most_recent is None:
            most_recent = {
                'artist': artist,
                'song': song,
                'album': album,
                'timestamp': played_time
            }
    
    conn.commit()
    conn.close()
    return most_recent

def save_status(now_playing, last_played):
    """Save both now_playing and last_played to JSON"""
    data = {
        'now_playing': now_playing,
        'last_played': last_played,
        'checked_at': datetime.now().isoformat()
    }
    with open('now_playing.json', 'w') as f:
        json.dump(data, f)
    print(f"💾 Saved status (now_playing={now_playing is not None}, last_played={last_played['song'] if last_played else None})")

if __name__ == "__main__":
    ensure_columns()
    update_top_tracks()
    
    print("Last.fm Updater Started")
    print("Press Ctrl+C to stop")
    
    try:
        while True:
            try:
                now_playing = get_now_playing()
                last_played = get_recent_tracks_info()
                save_status(now_playing, last_played)
            except Exception as e:
                print(f"Error in main loop: {e}")
            time.sleep(30)  # Check every 30 seconds
    except KeyboardInterrupt:
        print("\nStopped by user")