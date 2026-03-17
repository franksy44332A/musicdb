import sqlite3
import pandas as pd

# ===== CONFIGURATION =====
EXCEL_FILE = "music_librarydb.xlsx"   # your Excel file name
SHEET_NAME = 0                         # first sheet
DB_FILE = "music.db"
TABLE_NAME = "songs"
# Columns from Excel that should be updated (all except dynamic ones)
EXCEL_COLUMNS = [
    'Artist', 'Album', 'Song', 'NoteX', 'HeadGen', 'Lan', 'fullAlbum',
    'Genus Singer', 'Gen1', 'Gen2', 'Gen3', 'Gen4', 'N1', 'N2', 'soft',
    'P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'rating', 'albumRating', 'Year',
    'Release', 'essentialArtist', 'extra', 'songReview', 'albumReview',
    'Bio', 'credits', 'coverTracksDurationURL', 'numScrobblesURL',
    'BioURL', 'AlbumIDURL', 'creditsURL'
]
# Note: 'scrobbles' and 'last_played' are NOT included – they will be preserved.
# =========================

print("Reading Excel file...")
df = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME, dtype=str)

print(f"Found {len(df)} rows and {len(df.columns)} columns.")

# Clean column names
df.columns = df.columns.str.strip()

conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# Get list of existing columns in the database (to verify)
cursor.execute(f"PRAGMA table_info({TABLE_NAME})")
db_columns = [col[1] for col in cursor.fetchall()]

# Prepare the update and insert statements
# Build a list of columns to update (only those present in both Excel and DB)
update_columns = [col for col in EXCEL_COLUMNS if col in db_columns]
insert_columns = update_columns.copy()  # same set for insert

# Build SQL snippets
set_clause = ", ".join([f'"{col}" = ?' for col in update_columns])
insert_placeholders = ", ".join(["?"] * len(insert_columns))
quoted_insert_columns = ", ".join([f'"{col}"' for col in insert_columns])

# For each row, we'll first try to update; if no row updated, we insert.
update_sql = f'''
    UPDATE "{TABLE_NAME}"
    SET {set_clause}
    WHERE Artist = ? AND Album = ? AND Song = ?
'''
insert_sql = f'''
    INSERT INTO "{TABLE_NAME}" ({quoted_insert_columns})
    VALUES ({insert_placeholders})
'''

# Convert DataFrame to list of tuples, replacing NaN with None
rows = df.where(pd.notnull(df), None).values.tolist()

updated_count = 0
inserted_count = 0

for row in rows:
    # Extract the key fields (Artist, Album, Song)
    # row order matches the DataFrame columns
    # We need to map row values to the correct columns.
    # Instead of relying on order, let's use a dict for clarity.
    row_dict = dict(zip(df.columns, row))
    artist = row_dict.get('Artist')
    album = row_dict.get('Album')
    song = row_dict.get('Song')
    if not artist or not album or not song:
        print("Skipping row with missing key fields")
        continue

    # Build list of update values in the same order as update_columns
    update_values = [row_dict.get(col) for col in update_columns]
    # Append key fields for WHERE clause
    update_values.extend([artist, album, song])

    # Try update
    cursor.execute(update_sql, update_values)
    if cursor.rowcount > 0:
        updated_count += 1
    else:
        # No existing row – insert new one
        insert_values = [row_dict.get(col) for col in insert_columns]
        cursor.execute(insert_sql, insert_values)
        inserted_count += 1

conn.commit()
conn.close()

print(f"Done. Updated {updated_count} existing rows, inserted {inserted_count} new rows.")