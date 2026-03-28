import csv
import sqlite3

conn = sqlite3.connect("attendance.db")
cursor = conn.cursor()

# ---------- LOAD / UPDATE STUDENTS ----------
with open("students_cleaned.csv", newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]

    for row in reader:
        cursor.execute("""
            INSERT OR REPLACE INTO students
            (student_id, password, name, area)
            VALUES (?, ?, ?, ?)
        """, (
            row["student_id"],
            row["password"],
            row["name"],
            row["area"]
        ))

# ---------- LOAD / UPDATE FACULTY ----------
with open("faculty.csv", newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]

    for row in reader:
        cursor.execute("""
            INSERT OR REPLACE INTO faculty
            (faculty_id, password, name)
            VALUES (?, ?, ?)
        """, (
            row["faculty_id"],
            row["password"],
            row["name"]
        ))

conn.commit()
conn.close()

print("✅ Student and Faculty data loaded / updated successfully")
