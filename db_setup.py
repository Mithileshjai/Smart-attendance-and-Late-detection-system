import sqlite3

conn   = sqlite3.connect("attendance.db")
cursor = conn.cursor()

# Students
cursor.execute("""
CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    password   TEXT,
    name       TEXT,
    area       TEXT,
    is_blocked INTEGER DEFAULT 0
)
""")

# Faculty
cursor.execute("""
CREATE TABLE IF NOT EXISTS faculty (
    faculty_id TEXT PRIMARY KEY,
    password   TEXT,
    name       TEXT,
    is_blocked INTEGER DEFAULT 0
)
""")

# Admin
cursor.execute("""
CREATE TABLE IF NOT EXISTS admin (
    admin_id TEXT PRIMARY KEY,
    password TEXT,
    name     TEXT
)
""")

# Insert admin — Dr. Rajeswari Mukesh HoD/IT (FAC005)
cursor.execute("""
    INSERT OR REPLACE INTO admin (admin_id, password, name)
    VALUES ('FAC005', 'faculty@srm5', 'Dr. Rajeswari Mukesh HoD/IT')
""")

# Attendance logs
cursor.execute("""
CREATE TABLE IF NOT EXISTS attendance_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT,
    role         TEXT,
    start_time   TEXT,
    latitude     REAL,
    longitude    REAL,
    distance     REAL,
    eta_minutes  INTEGER,
    decision     TEXT,
    status       TEXT DEFAULT 'Pending',
    live_lat     REAL,
    live_lon     REAL,
    live_status  TEXT DEFAULT 'Travelling',
    arrived_time TEXT
)
""")

# Add is_blocked columns if upgrading existing DB
for table, col in [("students", "is_blocked"), ("faculty", "is_blocked")]:
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} INTEGER DEFAULT 0")
        print(f"✅ Added {col} to {table}")
    except sqlite3.OperationalError:
        print(f"⏭️  {table}.{col} already exists")

conn.commit()
conn.close()
print("✅ Database setup complete.")
print("👤 Admin login → ID: ADMIN001  Password: admin@srm")