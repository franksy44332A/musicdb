import pylast
API_KEY = "34128edb40451a9342b4c1ea124b7bed"
API_SECRET = "eeb7d38564276e52853581da81b4ab76"
USERNAME = "kattfarfars"

network = pylast.LastFMNetwork(api_key=API_KEY, api_secret=API_SECRET)
user = network.get_user(USERNAME)

recent = user.get_recent_tracks(limit=5)
print("Got recent tracks, length:", len(recent))
for idx, track in enumerate(recent):
    print(f"\nTrack {idx+1}:")
    print("  Artist:", track.track.artist.name)
    print("  Song:", track.track.title)
    print("  Played at:", track.playback_date)
    print("  Has 'now_playing' attr?", hasattr(track, 'now_playing'))
    if hasattr(track, 'now_playing'):
        print("  Value of now_playing:", track.now_playing)
    print("-" * 40)