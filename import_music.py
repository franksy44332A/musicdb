import sqlite3
import pandas as pd

# ===== CONFIGURATION =====
EXCEL_FILE = "music_librarydb.xlsx"      # <-- change to your actual file name
SHEET_NAME = 0                            # 0 = first sheet; or put sheet name in quotes
DB_FILE = "music.db"
TABLE_NAME = "songs"
# Columns that together uniquely identify a row
UNIQUE_KEYS = ["Artist", "Album", "Song"]  # composite primary key
# ==========================

print("Reading Excel file...")
df = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME, dtype=str)  # read all as text

# Clean column names (remove extra spaces) – but keep original names otherwise
df.columns = df.columns.str.strip()

print(f"Found {len(df)} rows and {len(df.columns)} columns.")

# Connect to SQLite (creates the file if it doesn't exist)
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# Create table if it doesn't exist, with a UNIQUE constraint on the three key columns
columns = df.columns.tolist()
# Quote column names to handle spaces or special characters
columns_definition = ",\n    ".join([f'"{col}" TEXT' for col in columns])

# Add composite unique constraint
unique_cols = ', '.join([f'"{col}"' for col in UNIQUE_KEYS])
create_table_sql = f'''
CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" (
    {columns_definition},
    UNIQUE({unique_cols}) ON CONFLICT REPLACE
);
'''
cursor.execute(create_table_sql)
print("Table is ready.")

# Prepare the INSERT OR REPLACE statement
placeholders = ", ".join(["?"] * len(columns))
quoted_columns = ", ".join([f'"{col}"' for col in columns])
insert_sql = f'INSERT OR REPLACE INTO "{TABLE_NAME}" ({quoted_columns}) VALUES ({placeholders})'

# Convert DataFrame to list of tuples (rows), replacing NaN with None (SQL NULL)
rows = df.where(pd.notnull(df), None).values.tolist()

# Insert data in batches for speed
batch_size = 500
total = len(rows)
for i in range(0, total, batch_size):
    batch = rows[i:i+batch_size]
    cursor.executemany(insert_sql, batch)
    print(f"Inserted rows {i+1} to {min(i+batch_size, total)}")

conn.commit()
conn.close()
print(f"Done! Database saved as {DB_FILE}")