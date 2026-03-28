import sqlite3

conn   = sqlite3.connect("attendance.db")
cursor = conn.cursor()

# List of new columns to add safely (won't crash if already exists)
new_columns = [
    ("distance",     "REAL"),
    ("eta_minutes",  "INTEGER"),
    ("status",       "TEXT DEFAULT 'Pending'"),
    ("live_lat",     "REAL"),
    ("live_lon",     "REAL"),
    ("live_status",  "TEXT DEFAULT 'Travelling'"),
    ("arrived_time", "TEXT"),
]

for col_name, col_type in new_columns:
    try:
        cursor.execute(f"ALTER TABLE attendance_logs ADD COLUMN {col_name} {col_type}")
        print(f"✅ Added column: {col_name}")
    except sqlite3.OperationalError:
        print(f"⏭️  Skipped (already exists): {col_name}")

conn.commit()
conn.close()
print("\n✅ Migration complete. Run app.py now.")