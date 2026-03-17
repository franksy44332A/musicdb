from flask import Flask, render_template, jsonify, request, redirect, url_for
import sqlite3
import json
import os
import re
import requests
from urllib.parse import quote
import time
import pylast
from datetime import datetime
import time

# Simple in-memory cache for now playing
now_playing_cache = {'data': None, 'timestamp': 0}
CACHE_DURATION = 30  # seconds

app = Flask(__name__)

# ===== CONFIGURATION =====
LASTFM_API_KEY = os.environ.get('LASTFM_API_KEY', '')
API_SECRET = os.environ.get('API_SECRET', '')
USERNAME = os.environ.get('LASTFM_USERNAME', '')
DB_FILE = "music.db"
# =========================

network = pylast.LastFMNetwork(api_key=LASTFM_API_KEY, api_secret=API_SECRET)
user = network.get_user(USERNAME)

# Load curated lists from JSON file
with open('curated_lists.json', 'r', encoding='utf-8') as f:
    curated_lists = json.load(f)

# ----- Database helpers -----
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def get_status():
    """Read the now_playing.json file and return the status dict"""
    try:
        with open('now_playing.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            'now_playing': None,
            'last_played': None,
            'checked_at': None
        }

# ----- Album image cache -----
def get_cached_image(artist, album):
    conn = get_db_connection()
    cursor = conn.execute('''
        SELECT image_url FROM album_images
        WHERE artist = ? AND album = ?
        AND julianday('now') - julianday(last_updated) < 30
    ''', (artist, album))
    row = cursor.fetchone()
    conn.close()
    return row['image_url'] if row else None

def save_image_to_cache(artist, album, image_url):
    max_retries = 3
    retry_delay = 0.1
    for attempt in range(max_retries):
        try:
            conn = get_db_connection()
            conn.execute('''
                INSERT OR REPLACE INTO album_images (artist, album, image_url, last_updated)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (artist, album, image_url))
            conn.commit()
            conn.close()
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < max_retries - 1:
                time.sleep(retry_delay * (2 ** attempt))
                continue
            else:
                print(f"Failed to save image after {max_retries} attempts: {e}")
                raise
        finally:
            try:
                conn.close()
            except:
                pass

def fetch_image_from_lastfm(artist, album):
    if not artist or not album:
        return None
    artist_encoded = quote(artist)
    album_encoded = quote(album)
    url = f"http://ws.audioscrobbler.com/2.0/?method=album.getinfo&api_key={LASTFM_API_KEY}&artist={artist_encoded}&album={album_encoded}&format=json"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if 'album' in data and 'image' in data['album']:
            images = data['album']['image']
            for img in images:
                if img['size'] == 'large' and img['#text']:
                    return img['#text']
            for img in images:
                if img['#text']:
                    return img['#text']
    except Exception as e:
        print(f"Error fetching image for {artist} - {album}: {e}")
    return None

def get_album_image(artist, album):
    if not album:
        return None
    cached = get_cached_image(artist, album)
    if cached:
        return cached
    image_url = fetch_image_from_lastfm(artist, album)
    if image_url:
        save_image_to_cache(artist, album, image_url)
    return image_url
    
@app.context_processor
def utility_processor():
    return dict(get_album_image=get_album_image)
    
def fetch_live_track_playcount(artist, track):
    """Get user's playcount for a specific track from Last.fm (live, no cache)"""
    if not artist or not track:
        return None
    artist_encoded = quote(artist)
    track_encoded = quote(track)
    url = f"http://ws.audioscrobbler.com/2.0/?method=track.getInfo&api_key={LASTFM_API_KEY}&artist={artist_encoded}&track={track_encoded}&username={USERNAME}&format=json"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if 'track' in data and 'userplaycount' in data['track']:
            return int(data['track']['userplaycount'])
    except Exception as e:
        print(f"Error fetching live track playcount: {e}")
    return None

# ----- Normalization helpers -----
def normalize_track_name(name):
    """Normalize track name for matching."""
    if not name:
        return ''
    # Replace & with 'and'
    name = name.replace('&', 'and')
    # Remove (Remastered) etc.
    name = re.sub(r'\s*\([^)]*remaster[^)]*\)', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*[-–—]\s*remaster(ed)?', '', name, flags=re.IGNORECASE)
    # Remove punctuation except spaces
    name = re.sub(r'[^\w\s]', '', name)
    # Collapse spaces and lowercase
    name = re.sub(r'\s+', ' ', name).strip().lower()
    return name

def clean_track_name_for_display(name):
    """Return a cleaner version of the track name for display."""
    if not name:
        return name
    cleaned = re.sub(r'\s*\([^)]*remaster[^)]*\)', '', name, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*[-–—]\s*remaster(ed)?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def find_song_rating(artist, track_name):
    """Find rating for a song using flexible artist matching."""
    conn = get_db_connection()
    # First try exact artist
    cursor = conn.execute('SELECT Song, rating FROM songs WHERE Artist = ?', (artist,))
    rows = cursor.fetchall()
    norm_input = normalize_track_name(track_name)
    for row in rows:
        if normalize_track_name(row['Song']) == norm_input:
            print(f"✅ Matched '{track_name}' to DB '{row['Song']}' with rating {row['rating']}")
            conn.close()
            return row['rating']
    
    # If not found, try normalized artist
    norm_artist = normalize_artist_for_db(artist)
    if norm_artist != artist:
        cursor = conn.execute('SELECT Song, rating FROM songs WHERE Artist = ?', (norm_artist,))
        rows = cursor.fetchall()
        for row in rows:
            if normalize_track_name(row['Song']) == norm_input:
                print(f"✅ Matched (normalized artist) '{track_name}' to DB '{row['Song']}' with rating {row['rating']}")
                conn.close()
                return row['rating']
    
    conn.close()
    print(f"❌ No match for '{track_name}' (normalized '{norm_input}') in artist {artist} or {norm_artist}")
    return None

def normalize_artist_for_db(artist):
    """Remove common ensemble suffixes to match database variations."""
    # List of suffixes to remove (case-insensitive)
    suffixes = [' sextet', ' quartet', ' trio', ' quintet', ' orchestra', ' ensemble', ' band']
    artist_lower = artist.lower()
    for suffix in suffixes:
        if artist_lower.endswith(suffix):
            return artist[:-len(suffix)]  # remove suffix
    return artist

def get_album_info(artist, album):
    print(f"get_album_info called with artist={artist}, album={album}")
    conn = get_db_connection()
    # Try exact match first
    cursor = conn.execute('''
        SELECT Year, Release, HeadGen, Gen1, Gen2, Gen3, Gen4, albumReview
        FROM songs
        WHERE Artist = ? AND Album = ?
        LIMIT 1
    ''', (artist, album))
    row = cursor.fetchone()
    if not row:
        # Try with normalized artist
        norm_artist = normalize_artist_for_db(artist)
        if norm_artist != artist:
            print(f"Trying normalized artist: {norm_artist}")
            cursor = conn.execute('''
                SELECT Year, Release, HeadGen, Gen1, Gen2, Gen3, Gen4, albumReview
                FROM songs
                WHERE Artist = ? AND Album = ?
                LIMIT 1
            ''', (norm_artist, album))
            row = cursor.fetchone()
    conn.close()
    if row:
        result = dict(row)
        print(f"Found album info: {result}")
        return result
    print("No album info found")
    return {}

def find_best_album_for_song(artist, song):
    """Find the most appropriate album for a given artist and song, with flexible matching."""
    conn = get_db_connection()
    norm_song = normalize_track_name(song)
    
    # Try exact artist first
    cursor = conn.execute('''
        SELECT Album FROM songs
        WHERE Artist = ? AND Song = ?
        ORDER BY scrobbles DESC
        LIMIT 1
    ''', (artist, song))
    row = cursor.fetchone()
    if row:
        conn.close()
        return row['Album']
    
    # Try normalized artist
    norm_artist = normalize_artist_for_db(artist)
    if norm_artist != artist:
        cursor = conn.execute('''
            SELECT Album FROM songs
            WHERE Artist = ? AND Song = ?
            ORDER BY scrobbles DESC
            LIMIT 1
        ''', (norm_artist, song))
        row = cursor.fetchone()
        if row:
            conn.close()
            return row['Album']
    
    # If still not found, try matching by normalized song name across all artists
    cursor = conn.execute('SELECT Artist, Album, Song FROM songs')
    rows = cursor.fetchall()
    conn.close()
    for row in rows:
        if normalize_track_name(row['Song']) == norm_song:
            return row['Album']
    return None

# ----- Album tracklist cache -----
def get_cached_tracks(artist, album):
    """Retrieve cached tracklist if exists and not too old (30 days)"""
    conn = get_db_connection()
    cursor = conn.execute('''
        SELECT tracks FROM album_tracks
        WHERE artist = ? AND album = ?
        AND julianday('now') - julianday(last_updated) < 30
    ''', (artist, album))
    row = cursor.fetchone()
    conn.close()
    if row:
        return json.loads(row['tracks'])
    return None

def save_tracks_to_cache(artist, album, tracks):
    """Store tracklist in cache"""
    conn = get_db_connection()
    conn.execute('''
        INSERT OR REPLACE INTO album_tracks (artist, album, tracks, last_updated)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (artist, album, json.dumps(tracks)))
    conn.commit()
    conn.close()

def fetch_album_tracks_from_lastfm(artist, album):
    """Fetch tracklist from Last.fm album.getinfo"""
    if not artist or not album:
        return None
    artist_encoded = quote(artist)
    album_encoded = quote(album)
    url = f"http://ws.audioscrobbler.com/2.0/?method=album.getinfo&api_key={LASTFM_API_KEY}&artist={artist_encoded}&album={album_encoded}&format=json"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if 'album' in data and 'tracks' in data['album'] and 'track' in data['album']['tracks']:
            tracks_data = data['album']['tracks']['track']
            if isinstance(tracks_data, dict):
                tracks_data = [tracks_data]
            track_names = [t['name'] for t in tracks_data]
            return track_names
    except Exception as e:
        print(f"Error fetching tracks for {artist} - {album}: {e}")
    return None

def get_album_tracks(artist, album):
    """Get tracklist from cache or fetch from Last.fm"""
    cached = get_cached_tracks(artist, album)
    if cached:
        return cached
    tracks = fetch_album_tracks_from_lastfm(artist, album)
    if tracks:
        save_tracks_to_cache(artist, album, tracks)
    return tracks

# ----- Album details (wiki, playcounts) cache -----
def get_cached_album_details(artist, album):
    """Retrieve cached album details if exists and not too old (30 days)"""
    conn = get_db_connection()
    cursor = conn.execute('''
        SELECT wiki_summary, wiki_content, user_playcount, album_playcount, listeners, release_date, tags
        FROM album_details
        WHERE artist = ? AND album = ?
        AND julianday('now') - julianday(last_updated) < 30
    ''', (artist, album))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'wiki_summary': row['wiki_summary'],
            'wiki_content': row['wiki_content'],
            'user_playcount': row['user_playcount'],
            'album_playcount': row['album_playcount'],
            'listeners': row['listeners'],
            'release_date': row['release_date'],
            'tags': json.loads(row['tags']) if row['tags'] else []
        }
    return None

def save_album_details_to_cache(artist, album, details):
    """Store album details in cache"""
    conn = get_db_connection()
    conn.execute('''
        INSERT OR REPLACE INTO album_details
        (artist, album, wiki_summary, wiki_content, user_playcount, album_playcount, listeners, release_date, tags, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (
        artist, album,
        details.get('wiki_summary'),
        details.get('wiki_content'),
        details.get('user_playcount'),
        details.get('album_playcount'),
        details.get('listeners'),
        details.get('release_date'),
        json.dumps(details.get('tags', []))
    ))
    conn.commit()
    conn.close()

def fetch_album_details_from_lastfm(artist, album):
    """Fetch album wiki, playcounts, tags from Last.fm album.getInfo with username"""
    if not artist or not album:
        return None
    artist_encoded = quote(artist)
    album_encoded = quote(album)
    url = f"http://ws.audioscrobbler.com/2.0/?method=album.getinfo&api_key={LASTFM_API_KEY}&artist={artist_encoded}&album={album_encoded}&username={USERNAME}&format=json"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if 'album' not in data:
            return None
        album_data = data['album']
        wiki_summary = None
        wiki_content = None
        if 'wiki' in album_data:
            wiki_summary = album_data['wiki'].get('summary')
            wiki_content = album_data['wiki'].get('content')
        user_playcount = None
        if 'userplaycount' in album_data:
            user_playcount = int(album_data['userplaycount'])
        album_playcount = int(album_data.get('playcount', 0))
        listeners = int(album_data.get('listeners', 0))
        release_date = None
        tags = []
        if 'tags' in album_data and 'tag' in album_data['tags']:
            tag_list = album_data['tags']['tag']
            if isinstance(tag_list, dict):
                tag_list = [tag_list]
            tags = [t['name'] for t in tag_list]
        return {
            'wiki_summary': wiki_summary,
            'wiki_content': wiki_content,
            'user_playcount': user_playcount,
            'album_playcount': album_playcount,
            'listeners': listeners,
            'release_date': release_date,
            'tags': tags
        }
    except Exception as e:
        print(f"Error fetching album details for {artist} - {album}: {e}")
        return None
        
def fetch_recent_tracks(limit=50):
    """Fetch user's most recent scrobbles"""
    try:
        recent_tracks = user.get_recent_tracks(limit=limit)
        tracks = []
        for track in recent_tracks:
            played_time = track.playback_date
            tracks.append({
                'artist': track.track.artist.name,
                'name': track.track.title,
                'played_at': played_time,
                'now_playing': hasattr(track, 'now_playing') and track.now_playing
            })
        return tracks
    except Exception as e:
        print(f"Error fetching recent tracks: {e}")
        return []

def get_album_details(artist, album):
    """Get album details from cache or fetch from Last.fm"""
    cached = get_cached_album_details(artist, album)
    if cached:
        return cached
    details = fetch_album_details_from_lastfm(artist, album)
    if details:
        save_album_details_to_cache(artist, album, details)
    return details

def fetch_user_top_artists(period='overall', limit=50):
    """Fetch user's top artists from Last.fm"""
    try:
        top_artists = user.get_top_artists(period=period, limit=limit)
        artists = []
        for artist in top_artists:
            artists.append({
                'rank': artist.rank if hasattr(artist, 'rank') else len(artists)+1,
                'name': artist.item.name,
                'playcount': artist.weight
            })
        return artists
    except Exception as e:
        print(f"Error fetching top artists: {e}")
        return []

def fetch_user_top_albums(period='overall', limit=50):
    try:
        print(f"Fetching top albums with period={period}, limit={limit}")
        top_albums = user.get_top_albums(period=period, limit=limit)
        print(f"Raw top_albums type: {type(top_albums)}")
        print(f"Length: {len(top_albums)}")
        if len(top_albums) > 0:
            print(f"First item: {top_albums[0]}")
            # Check attributes
            first = top_albums[0]
            print(f"Has rank? {hasattr(first, 'rank')}")
            print(f"Has weight? {hasattr(first, 'weight')}")
            print(f"Has item? {hasattr(first, 'item')}")
            if hasattr(first, 'item'):
                print(f"Item type: {type(first.item)}")
                print(f"Item attributes: {dir(first.item)}")
        albums = []
        for album in top_albums:
            albums.append({
                'rank': album.rank if hasattr(album, 'rank') else len(albums)+1,
                'artist': album.item.artist.name,
                'name': album.item.name,
                'playcount': album.weight
            })
        return albums
    except Exception as e:
        print(f"Error fetching top albums: {e}")
        import traceback
        traceback.print_exc()
        return []

def fetch_user_top_albums(period='overall', limit=50):
    """Fetch user's top albums from Last.fm using pylast"""
    try:
        top_albums = user.get_top_albums(period=period, limit=limit)
        albums = []
        for album in top_albums:
            # album is a TopItem with .item being a pylast.Album
            album_obj = album.item
            artist_name = album_obj.get_artist().get_name()
            album_name = album_obj.get_name()
            playcount = album.weight
            albums.append({
                'rank': album.rank if hasattr(album, 'rank') else len(albums)+1,
                'artist': artist_name,
                'name': album_name,
                'playcount': playcount
            })
        return albums
    except Exception as e:
        print(f"Error fetching top albums: {e}")
        return []

def fetch_user_top_tracks(period='overall', limit=50):
    """Fetch user's top tracks from Last.fm using pylast"""
    try:
        top_tracks = user.get_top_tracks(period=period, limit=limit)
        tracks = []
        for track in top_tracks:
            track_obj = track.item
            artist_name = track_obj.get_artist().get_name()
            track_name = track_obj.get_name()
            playcount = track.weight
            tracks.append({
                'rank': track.rank if hasattr(track, 'rank') else len(tracks)+1,
                'artist': artist_name,
                'name': track_name,
                'playcount': playcount
            })
        return tracks
    except Exception as e:
        print(f"Error fetching top tracks: {e}")
        return []

# ----- Context processor to make 'request' available in all templates -----
@app.context_processor
def inject_request():
    return dict(request=request)

# ----- Routes -----
@app.route('/')
def index():
    # Get now playing directly from Last.fm
         current_time = time.time()
    # Use cache if fresh
    if current_time - now_playing_cache['timestamp'] < CACHE_DURATION:
        current_track = now_playing_cache['data']
    else:
        # Fetch fresh data
        now_playing_track = None
        try:
            now_playing_track = user.get_now_playing()
        except Exception as e:
            print(f"Error fetching now playing: {e}")

        recent_tracks = []
        try:
            recent_tracks = user.get_recent_tracks(limit=1)
        except Exception as e:
            print(f"Error fetching recent tracks: {e}")

        if now_playing_track:
            current_track = {
                'artist': now_playing_track.artist.name,
                'song': now_playing_track.title,
                'album': now_playing_track.get_album().get_name() if now_playing_track.get_album() else None,
                'timestamp': 'Now',
                'is_now_playing': True
            }
        elif recent_tracks:
            track = recent_tracks[0]
            current_track = {
                'artist': track.track.artist.name,
                'song': track.track.title,
                'album': track.track.get_album().get_name() if track.track.get_album() else None,
                'timestamp': track.playback_date,
                'is_now_playing': False
            }
        else:
            current_track = None

        # Update cache
        now_playing_cache['data'] = current_track
        now_playing_cache['timestamp'] = current_time

    if not current_track:
        return render_template('home.html', album=None, tracks=[], current_track=None, status={'now_playing': None, 'last_played': None})

    artist = current_track['artist']
    song = current_track['song']
    album_name = current_track.get('album')

    # If album missing, try to find it in database (fallback)
    if not album_name:
        album_name = find_best_album_for_song(artist, song)

    if not album_name:
        return render_template('home.html', album=None, tracks=[], current_track=current_track, status={'now_playing': current_track if current_track['is_now_playing'] else None, 'last_played': current_track if not current_track['is_now_playing'] else None})

    # Get album image (cached in DB)
    album_image = get_album_image(artist, album_name)

    # Fetch album metadata from database (static)
    album_meta = get_album_info(artist, album_name)

    # Get album tracklist (cached in DB)
    tracklist = get_album_tracks(artist, album_name)

    if not tracklist:
        album_info = {
            'artist': artist,
            'name': album_name,
            'image': album_image,
            **album_meta
        }
        return render_template('home.html', album=album_info, tracks=[], current_track=current_track, status={'now_playing': current_track if current_track['is_now_playing'] else None, 'last_played': current_track if not current_track['is_now_playing'] else None})

    # Normalize and get ratings from static DB
    norm_current_song = normalize_track_name(song)
    conn = get_db_connection()
    artist_songs = conn.execute('SELECT Song, rating, scrobbles FROM songs WHERE Artist = ?', (artist,)).fetchall()
    conn.close()
    song_lookup = {}
    for s in artist_songs:
        norm = normalize_track_name(s['Song'])
        song_lookup[norm] = {'rating': s['rating'], 'scrobbles': s['scrobbles']}

    tracks_with_rating = []
    for track_name in tracklist:
        norm = normalize_track_name(track_name)
        stats = song_lookup.get(norm, {'rating': None, 'scrobbles': None})
        is_current = (norm_current_song == norm)
        display_name = clean_track_name_for_display(track_name)
        tracks_with_rating.append({
            'name': display_name,
            'rating': stats['rating'],
            'scrobbles': stats['scrobbles'],
            'is_current': is_current
        })

    album_info = {
        'artist': artist,
        'name': album_name,
        'image': album_image,
        **album_meta
    }

    # Get album details (wiki, playcounts) – uses cache but we can also fetch live if desired
    album_details = get_album_details(artist, album_name)

    status = {
        'now_playing': current_track if current_track['is_now_playing'] else None,
        'last_played': current_track if not current_track['is_now_playing'] else None
    }

    return render_template('home.html',
                           album=album_info,
                           tracks=tracks_with_rating,
                           current_track=current_track,
                           status=status,
                           album_details=album_details)

@app.route('/artist/<artist>')
def artist(artist):
    conn = get_db_connection()
    songs = conn.execute('''
        SELECT * FROM songs 
        WHERE Artist = ? 
        ORDER BY Album, Song
    ''', (artist,)).fetchall()
    conn.close()
    return render_template('artist.html', artist=artist, songs=songs)

@app.route('/api/now-playing')
def api_now_playing():
    return jsonify(get_status())
    
@app.route('/album-of-the-day')
def album_of_the_day():
    conn = get_db_connection()
    # Get all distinct essential albums, sorted for stable order
    albums = conn.execute('''
        SELECT Artist, Album FROM songs
        WHERE fullAlbum = 'EA'
        GROUP BY Artist, Album
        ORDER BY Artist, Album
    ''').fetchall()
    conn.close()
    
    if not albums:
        return "No essential albums found", 404
    
    # Use current date to compute a deterministic index
    now = datetime.now()
    epoch = datetime(2020, 1, 1)  # arbitrary fixed date
    day_number = (now - epoch).days
    # Add a year offset so same date in different years yields different index
    year_offset = now.year * 100
    total = len(albums)
    index = (day_number + year_offset) % total
    album = albums[index]
    
    # Redirect to the album page (url_for expects artist and album)
    return redirect(url_for('album_page', artist=album['Artist'], album=album['Album']))

@app.route('/top-played')
@app.route('/top-played')
def top_played():
    period = request.args.get('period', 'overall')
    artists = fetch_user_top_artists(period=period, limit=50)
    albums = fetch_user_top_albums(period=period, limit=50)
    tracks = fetch_user_top_tracks(period=period, limit=50)
    recent = fetch_recent_tracks(limit=50)
    
    # Debug prints
    print(f"Top artists count: {len(artists)}")
    if artists:
        print(f"First artist: {artists[0]}")
    print(f"Top albums count: {len(albums)}")
    print(f"Top tracks count: {len(tracks)}")
    print(f"Recent tracks count: {len(recent)}")
    
    return render_template('top_played.html',
                          artists=artists,
                          albums=albums,
                          tracks=tracks,
                          recent=recent,
                          period=period)
# --- Genre routes ---
@app.route('/genres')
def genres():
    genre_list = [
        "Ambient", "Blues", "Classical Music", "Country", "Dance",
        "Electronic", "Funk", "Hip Hop", "Jazz", "Metal", "Other",
        "Pop", "Reggae", "Regional Music", "Rock", "Ska", "Soul", "Soundtrack"
    ]
    return render_template('genres.html', genres=genre_list)

@app.route('/genre/<genre_name>')
def genre(genre_name):
    conn = get_db_connection()
    albums = conn.execute('''
        SELECT Artist, Album, MIN(Year) as Year, MIN(albumRating) as albumRating
        FROM songs
        WHERE (Gen1 = ? OR Gen2 = ? OR Gen3 = ? OR Gen4 = ?)
          AND Album IS NOT NULL AND Album != ''
        GROUP BY Artist, Album
        ORDER BY Artist, Album
    ''', (genre_name, genre_name, genre_name, genre_name)).fetchall()
    
    album_list = []
    for album in albums:
        album_dict = dict(album)
        album_dict['image'] = get_album_image(album['Artist'], album['Album'])
        album_list.append(album_dict)
    
    conn.close()
    return render_template('genre.html', genre=genre_name, albums=album_list)

# --- Placeholders for other sections ---
@app.route('/essential-albums')
def essential_albums():
    conn = get_db_connection()
    total = conn.execute('SELECT COUNT(DISTINCT Artist || Album) FROM songs WHERE fullAlbum = "EA"').fetchone()[0]
    conn.close()
    return render_template('essential_albums.html', total=total)
    
@app.route('/album/<path:artist>/<path:album>')
def album_page(artist, album):
    # Get album image
    album_image = get_album_image(artist, album)
    
    # Fetch album metadata from database
    album_meta = get_album_info(artist, album)
    
    # Get album tracklist
    tracklist = get_album_tracks(artist, album)
    
    # Get album details (wiki, playcounts, etc.)
    album_details = get_album_details(artist, album)
    
    # If no tracklist, still show album info
    if not tracklist:
        album_info = {
            'artist': artist,
            'name': album,
            'image': album_image,
            **album_meta
        }
        return render_template('album.html', album=album_info, tracks=[], album_details=album_details, error="Could not fetch tracks")
    
    # Get all songs for this artist to build lookup for ratings and scrobbles
    conn = get_db_connection()
    artist_songs = conn.execute('SELECT Song, rating, scrobbles FROM songs WHERE Artist = ?', (artist,)).fetchall()
    conn.close()
    
    song_lookup = {}
    for s in artist_songs:
        norm = normalize_track_name(s['Song'])
        song_lookup[norm] = {'rating': s['rating'], 'scrobbles': s['scrobbles']}
    
    tracks_with_rating = []
    for track_name in tracklist:
        norm = normalize_track_name(track_name)
        stats = song_lookup.get(norm, {'rating': None, 'scrobbles': None})
        display_name = clean_track_name_for_display(track_name)
        tracks_with_rating.append({
            'name': display_name,
            'rating': stats['rating'],
            'scrobbles': stats['scrobbles'],
            'is_current': False  # No current track on this page
        })
    
    album_info = {
        'artist': artist,
        'name': album,
        'image': album_image,
        **album_meta
    }
    
    return render_template('album.html',
                           album=album_info,
                           tracks=tracks_with_rating,
                           album_details=album_details)

@app.route('/essential-artists')
def essential_artists():
    return "<h1>Essential Artists</h1><p>Coming soon...</p><a href='/'>Back home</a>"

@app.route('/essential-tracks')
def essential_tracks():
    # Optionally pass total count for display
    conn = get_db_connection()
    total = conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0]
    conn.close()
    return render_template('essential_tracks.html', total=total)

@app.route('/playlists')
def playlists():
    """Main playlists page with genre grid."""
    genres = [
        "Rock", "Pop", "Jazz", "Dance", "Reggae", "Metal", "Ambient",
        "Country", "Soundtrack", "Hip Hop", "Soul", "Electronic",
        "Classical Music", "Blues", "Other", "Funk"
    ]
    return render_template('playlists.html', genres=genres)
    
@app.route('/curated')
def curated_index():
    """Show list of all curated recommendation posts."""
    return render_template('curated_index.html', lists=curated_lists)
    
@app.route('/health')
def health():
    """Simple health check endpoint for Render."""
    return "OK", 200

@app.route('/curated/<slug>')
def curated_list(slug):
    """Show albums for a specific curated list."""
    # Find the list by slug
    curated = next((item for item in curated_lists if item['slug'] == slug), None)
    if not curated:
        return "List not found", 404

    # For each album, get image from database (if exists)
    albums_with_images = []
    for artist, album in curated['albums']:
        album_info = {
            'Artist': artist,
            'Album': album,
            'image': get_album_image(artist, album)
        }
        albums_with_images.append(album_info)

    return render_template('curated_list.html', curated=curated, albums=albums_with_images)

@app.route('/playlists/<genre>')
def playlist_genre(genre):
    """Show essential albums for a specific genre."""
    conn = get_db_connection()
    # Select unique essential albums where Gen1 matches the genre (case‑insensitive)
    rows = conn.execute('''
        SELECT Artist, Album, HeadGen, Year, albumRating
        FROM songs
        WHERE fullAlbum = 'EA' AND Gen1 = ?
        GROUP BY Artist, Album
        ORDER BY Artist, Album
    ''', (genre,)).fetchall()
    conn.close()

    albums = []
    for row in rows:
        album_dict = dict(row)
        album_dict['image'] = get_album_image(row['Artist'], row['Album'])
        albums.append(album_dict)

    return render_template('playlist_genre.html', genre=genre, albums=albums)

@app.route('/playlists/free-jazz-avant-garde')
def playlist_free_jazz():
    # List of genres to include (as they appear in Gen1)
    genres = ['Free Improvisation', 'Avant-Garde Jazz', 'European Free Jazz', 'Free Jazz']
    
    conn = get_db_connection()
    # Build a query that checks Gen1 against any of the genres
    placeholders = ','.join(['?'] * len(genres))
    rows = conn.execute(f'''
        SELECT Artist, Album, HeadGen, Year, albumRating
        FROM songs
        WHERE fullAlbum = 'EA' AND Gen2 IN ({placeholders})
        GROUP BY Artist, Album
        ORDER BY Artist, Album
    ''', genres).fetchall()
    conn.close()

    albums = []
    for row in rows:
        album_dict = dict(row)
        album_dict['image'] = get_album_image(row['Artist'], row['Album'])
        albums.append(album_dict)

    return render_template('playlist_genre.html', genre='Free Jazz / Avant‑Garde', albums=albums)
    
@app.route('/api/essential-tracks')
def api_essential_tracks():
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT Artist, Album, Song, HeadGen, Lan, fullAlbum,
               "Genus Singer" as GenusSinger,
               Gen1, Gen2, Gen3, Gen4,
               rating, albumRating, Year, Release
        FROM songs
        ORDER BY Artist, Album, Song
    ''').fetchall()
    conn.close()
    # Convert rows to list of dicts
    data = [dict(row) for row in rows]
    return jsonify(data)
 
@app.route('/api/essential-albums')
def api_essential_albums():
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT 
            Artist,
            Album,
            HeadGen,
            Gen1,
            Gen2,
            Gen3,
            Gen4,
            albumRating,
            Release
        FROM songs
        WHERE fullAlbum = 'EA'
        GROUP BY Artist, Album
        ORDER BY Artist, Album
    ''').fetchall()
    conn.close()
    data = [dict(row) for row in rows]
    return jsonify(data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)