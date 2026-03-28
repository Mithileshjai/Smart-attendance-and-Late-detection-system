import sqlite3

conn = sqlite3.connect("attendance.db")
cursor = conn.cursor()

cursor.execute("SELECT * FROM students")
print(cursor.fetchall())

cursor.execute("SELECT * FROM faculty")
print(cursor.fetchall())

conn.close()
