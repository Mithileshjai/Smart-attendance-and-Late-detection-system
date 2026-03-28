from flask import Flask, render_template, request, redirect, jsonify, Response
from datetime import datetime, timedelta
import sqlite3
import math
import csv
import io

# ── COLLEGE CONFIG ─────────────────────────────────────────
COLLEGE_LAT        = 13.0323
COLLEGE_LON        = 80.1807
AVERAGE_SPEED_KMPH = 25
COLLEGE_START_TIME = "08:30"
ARRIVAL_RADIUS_KM  = 0.3

app = Flask(__name__)


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect("attendance.db")
    conn.row_factory = sqlite3.Row
    return conn


def haversine_distance(lat1, lon1, lat2, lon2):
    R    = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl   = math.radians(lon2 - lon1)
    a    = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def compute_status(start_time_str, eta_minutes):
    start_dt   = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
    arrival_dt = start_dt + timedelta(minutes=eta_minutes)
    cutoff_dt  = datetime.strptime(
        start_dt.strftime("%Y-%m-%d") + " " + COLLEGE_START_TIME + ":00",
        "%Y-%m-%d %H:%M:%S"
    )
    return "On-Time" if arrival_dt <= cutoff_dt else "Late"


# ═══════════════════════════════════════════════════════════
#  LOCAL AI PREDICTION ENGINE
# ═══════════════════════════════════════════════════════════

def run_local_prediction(start_time, eta_text, distance,
                         total_days, late_days, ontime_days,
                         late_pct, grace_at_risk, safe_ontime_needed,
                         recent_history):
    eta_minutes = 0
    if eta_text and eta_text != "Unknown":
        parts = eta_text.lower().replace("hours","hour").replace("mins","min").replace("minutes","min")
        if "hour" in parts:
            try:
                h = int(parts.split("hour")[0].strip().split()[-1])
                eta_minutes += h * 60
            except: pass
        if "min" in parts:
            try:
                m = int(parts.split("min")[0].strip().split()[-1])
                eta_minutes += m
            except: pass
    if eta_minutes == 0:
        eta_minutes = 30

    try:
        start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    except:
        start_dt = datetime.now()

    arrival_dt   = start_dt + timedelta(minutes=eta_minutes)
    cutoff_dt    = start_dt.replace(hour=8, minute=30, second=0, microsecond=0)
    minutes_diff = (arrival_dt - cutoff_dt).total_seconds() / 60
    will_be_late = arrival_dt > cutoff_dt

    if will_be_late:
        mins_late = round(minutes_diff)
        if mins_late <= 5:
            late_reason = f"You will arrive approximately {mins_late} minute(s) late — very close, traffic could make it worse."
        elif mins_late <= 15:
            late_reason = f"You will arrive about {mins_late} minutes late based on your current ETA of {eta_text}."
        else:
            late_reason = f"You started too late — with {eta_text} travel time, you will arrive {mins_late} minutes after 8:30 AM."
    else:
        mins_early = round(-minutes_diff)
        if mins_early <= 5:
            late_reason = f"You will just barely make it — arriving about {mins_early} minute(s) before 8:30 AM."
        else:
            late_reason = f"You will arrive approximately {mins_early} minutes early. Good timing!"

    ontime_rate = round((ontime_days / total_days * 100), 1) if total_days > 0 else 0
    needed_more = max(0, safe_ontime_needed - ontime_days)

    if total_days == 0:
        grace_analysis = "No attendance history yet. Start your journey regularly to build your record."
    elif grace_at_risk:
        grace_analysis = (f"Your on-time rate is {ontime_rate}%, below the 75% requirement. "
                          f"You need {needed_more} more on-time arrival(s) to be safe.")
    else:
        buffer = ontime_days - safe_ontime_needed
        grace_analysis = (f"Your on-time rate is {ontime_rate}%, safely above 75%. "
                          f"You have a buffer of {buffer} late day(s) before grace attendance is at risk.")

    if grace_at_risk and will_be_late:    risk_level = "High"
    elif grace_at_risk or (will_be_late and late_pct >= 40): risk_level = "Medium"
    elif will_be_late and late_pct < 20:  risk_level = "Low"
    elif not will_be_late and not grace_at_risk: risk_level = "Low"
    else:                                 risk_level = "Medium"

    if len(recent_history) == 0:
        pattern_insight = "No attendance history available yet to analyse patterns."
    else:
        recent_late   = sum(1 for r in recent_history if r.get("status") == "Late")
        recent_ontime = sum(1 for r in recent_history if r.get("status") == "On-Time")
        recent_total  = len(recent_history)
        if recent_late == 0:
            pattern_insight = f"Excellent recent trend — on-time all {recent_total} recent day(s). Keep it up!"
        elif recent_ontime == 0:
            pattern_insight = f"Concerning trend — late all {recent_total} recent day(s). Try leaving earlier."
        elif recent_late > recent_ontime:
            pattern_insight = f"Mostly late recently ({recent_late} late vs {recent_ontime} on-time in last {recent_total} days)."
        else:
            pattern_insight = f"Mixed pattern — {recent_ontime} on-time and {recent_late} late in last {recent_total} days."

    if will_be_late and grace_at_risk:
        recommendation = "Leave immediately and inform your faculty — both today's lateness and overall attendance need urgent attention."
    elif will_be_late and not grace_at_risk:
        recommendation = f"Try to leave 15–20 minutes earlier tomorrow. ETA of {eta_text} is too close to the 8:30 AM cutoff."
    elif not will_be_late and grace_at_risk:
        recommendation = f"Good — you'll be on-time today. You still need {needed_more} more on-time day(s)."
    else:
        recommendation = "You're on track! Maintain your current routine to keep your attendance record strong."

    return {
        "will_be_late":           will_be_late,
        "late_prediction_reason": late_reason,
        "grace_at_risk":          grace_at_risk,
        "grace_analysis":         grace_analysis,
        "recommendation":         recommendation,
        "risk_level":             risk_level,
        "pattern_insight":        pattern_insight,
    }


# ═══════════════════════════════════════════════════════════
#  LOGIN
# ═══════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def login_page():
    return render_template("login.html", error=None)


@app.route("/login", methods=["POST"])
def login_action():
    user_id  = request.form["user_id"].strip()
    password = request.form["password"].strip()
    role     = request.form["role"]
    conn     = get_db()
    cursor   = conn.cursor()

    if role == "Student":
        cursor.execute(
            "SELECT student_id, is_blocked FROM students WHERE student_id=? AND password=?",
            (user_id, password)
        )
        result = cursor.fetchone()
        conn.close()
        if not result:
            return render_template("login.html", error="Invalid Student ID or Password")
        if result["is_blocked"]:
            return render_template("login.html", error="Your account has been blocked. Contact admin.")
        return redirect(f"/student_dashboard?student_id={user_id}")

    elif role == "Faculty":
        cursor.execute(
            "SELECT faculty_id, is_blocked FROM faculty WHERE faculty_id=? AND password=?",
            (user_id, password)
        )
        result = cursor.fetchone()
        conn.close()
        if not result:
            return render_template("login.html", error="Invalid Faculty ID or Password")
        if result["is_blocked"]:
            return render_template("login.html", error="Your account has been blocked. Contact admin.")
        return redirect(f"/faculty_dashboard?faculty_id={user_id}")

    elif role == "Admin":
        cursor.execute(
            "SELECT admin_id FROM admin WHERE admin_id=? AND password=?",
            (user_id, password)
        )
        result = cursor.fetchone()
        conn.close()
        if not result:
            return render_template("login.html", error="Invalid Admin ID or Password")
        return redirect(f"/admin_dashboard?admin_id={user_id}")

    conn.close()
    return redirect("/")


# ═══════════════════════════════════════════════════════════
#  STUDENT DASHBOARD
# ═══════════════════════════════════════════════════════════

@app.route("/student_dashboard")
def student_dashboard():
    student_id = request.args.get("student_id")
    conn       = get_db()
    cursor     = conn.cursor()
    today      = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT start_time, live_status FROM attendance_logs
        WHERE user_id=? AND date(start_time)=?
    """, (student_id, today))
    row = cursor.fetchone()
    conn.close()

    journey_started_today = row is not None
    tracking_active = row is not None and row["live_status"] not in ("Arrived", "Stopped")

    return render_template(
        "student_dashboard.html",
        student_id=student_id,
        journey_started_today=journey_started_today,
        tracking_active=tracking_active
    )


# ═══════════════════════════════════════════════════════════
#  STUDENT: HISTORY + STATS API
# ═══════════════════════════════════════════════════════════

@app.route("/api/student_history")
def student_history():
    student_id = request.args.get("student_id")
    conn       = get_db()
    cursor     = conn.cursor()

    cursor.execute("""
        SELECT date(start_time) AS day, start_time, arrived_time,
               distance, eta_minutes, status, live_status
        FROM attendance_logs WHERE user_id=? ORDER BY start_time DESC
    """, (student_id,))
    rows = cursor.fetchall()
    conn.close()

    history = [{
        "day":          r["day"],
        "start_time":   r["start_time"] or "—",
        "arrived_time": r["arrived_time"] or "—",
        "distance":     round(r["distance"], 2) if r["distance"] else "—",
        "eta_minutes":  r["eta_minutes"] or "—",
        "status":       r["status"] or "—",
        "live_status":  r["live_status"] or "—",
    } for r in rows]

    total   = len(history)
    on_time = sum(1 for h in history if h["status"] == "On-Time")
    late    = sum(1 for h in history if h["status"] == "Late")
    late_pct = round(late / total * 100, 1) if total > 0 else 0

    score = 100
    streak = 0
    max_streak = 0
    for h in reversed(history):
        if h["status"] == "On-Time":
            streak += 1; max_streak = max(max_streak, streak)
        else:
            score -= 5; streak = 0
    score += min(max_streak * 2, 20)
    score  = max(0, min(100, score))

    current_streak = 0
    for h in history:
        if h["status"] == "On-Time": current_streak += 1
        else: break

    return jsonify({
        "history": history, "total": total, "on_time": on_time,
        "late": late, "late_pct": late_pct, "score": score,
        "current_streak": current_streak, "max_streak": max_streak,
        "calendar": {h["day"]: h["status"] for h in history},
    })


# ═══════════════════════════════════════════════════════════
#  FACULTY DASHBOARD
# ═══════════════════════════════════════════════════════════

@app.route("/faculty_dashboard")
def faculty_dashboard():
    faculty_id = request.args.get("faculty_id", "")
    conn       = get_db()
    cursor     = conn.cursor()
    today      = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT name FROM faculty WHERE faculty_id=?", (faculty_id,))
    fac_row      = cursor.fetchone()
    faculty_name = fac_row["name"] if fac_row else "Faculty"

    cursor.execute("""
        SELECT s.student_id, s.name, s.area,
               al.start_time, al.eta_minutes, al.live_lat, al.live_lon,
               al.live_status, al.arrived_time, al.distance
        FROM students s
        LEFT JOIN attendance_logs al
            ON s.student_id = al.user_id AND date(al.start_time) = ?
        ORDER BY s.student_id
    """, (today,))
    rows = cursor.fetchall()
    conn.close()

    records = []
    for r in rows:
        eta_clock = "—"
        if r["start_time"] and r["eta_minutes"]:
            st        = datetime.strptime(r["start_time"], "%Y-%m-%d %H:%M:%S")
            eta_clock = (st + timedelta(minutes=r["eta_minutes"])).strftime("%I:%M %p")
        records.append({
            "student_id":   r["student_id"],
            "name":         r["name"] or "—",
            "area":         r["area"] or "—",
            "started":      "Yes" if r["start_time"] else "No",
            "start_time":   r["start_time"] or "—",
            "eta_minutes":  r["eta_minutes"] or "—",
            "eta_clock":    eta_clock,
            "live_lat":     r["live_lat"] or "",
            "live_lon":     r["live_lon"] or "",
            "live_status":  r["live_status"] or "—",
            "arrived_time": r["arrived_time"] or "—",
            "distance":     round(r["distance"], 2) if r["distance"] else "—",
        })

    return render_template(
        "faculty_dashboard.html",
        records=records, today=today,
        faculty_id=faculty_id, faculty_name=faculty_name
    )


# ═══════════════════════════════════════════════════════════
#  FACULTY: HISTORICAL REPORT + EXPORT
# ═══════════════════════════════════════════════════════════

@app.route("/api/faculty_report")
def faculty_report():
    date   = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    conn   = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.student_id, s.name, s.area,
               al.start_time, al.arrived_time, al.distance,
               al.eta_minutes, al.status, al.live_status
        FROM students s
        LEFT JOIN attendance_logs al
            ON s.student_id = al.user_id AND date(al.start_time) = ?
        ORDER BY s.student_id
    """, (date,))
    rows = cursor.fetchall()
    conn.close()

    records = []
    for r in rows:
        eta_clock = "—"
        if r["start_time"] and r["eta_minutes"]:
            st        = datetime.strptime(r["start_time"], "%Y-%m-%d %H:%M:%S")
            eta_clock = (st + timedelta(minutes=r["eta_minutes"])).strftime("%I:%M %p")
        records.append({
            "student_id":   r["student_id"],
            "name":         r["name"] or "—",
            "area":         r["area"] or "—",
            "start_time":   r["start_time"] or "Not Started",
            "arrived_time": r["arrived_time"] or "—",
            "distance":     round(r["distance"], 2) if r["distance"] else "—",
            "eta_clock":    eta_clock,
            "status":       r["status"] or "Absent",
            "live_status":  r["live_status"] or "—",
        })

    total   = len(records)
    started = sum(1 for r in records if r["start_time"] != "Not Started")
    on_time = sum(1 for r in records if r["status"] == "On-Time")
    late    = sum(1 for r in records if r["status"] == "Late")

    return jsonify({
        "date": date, "records": records,
        "summary": {"total": total, "started": started,
                    "on_time": on_time, "late": late, "absent": total - started}
    })


@app.route("/api/export_csv")
def export_csv():
    date   = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    conn   = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.student_id, s.name, s.area,
               al.start_time, al.arrived_time, al.distance,
               al.eta_minutes, al.status, al.live_status
        FROM students s
        LEFT JOIN attendance_logs al
            ON s.student_id = al.user_id AND date(al.start_time) = ?
        ORDER BY s.student_id
    """, (date,))
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student ID","Name","Area","Start Time","Arrived Time",
                     "Distance (km)","ETA (mins)","Status","Live Status"])
    for r in rows:
        writer.writerow([
            r["student_id"], r["name"] or "—", r["area"] or "—",
            r["start_time"] or "Not Started", r["arrived_time"] or "—",
            round(r["distance"], 2) if r["distance"] else "—",
            r["eta_minutes"] or "—", r["status"] or "Absent", r["live_status"] or "—",
        ])

    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=attendance_{date}.csv"})


# ═══════════════════════════════════════════════════════════
#  START JOURNEY
# ═══════════════════════════════════════════════════════════

@app.route("/start_journey", methods=["POST"])
def start_journey():
    data       = request.get_json()
    student_id = data["student_id"]
    latitude   = float(data["latitude"])
    longitude  = float(data["longitude"])
    conn       = get_db()
    cursor     = conn.cursor()
    today      = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT COUNT(*) FROM attendance_logs WHERE user_id=? AND date(start_time)=?",
                   (student_id, today))
    if cursor.fetchone()[0] > 0:
        conn.close()
        return jsonify({"error": "Journey already started today."})

    start_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    distance_km = haversine_distance(latitude, longitude, COLLEGE_LAT, COLLEGE_LON)
    eta_minutes = round((distance_km / AVERAGE_SPEED_KMPH) * 60)
    status      = compute_status(start_time, eta_minutes)

    cursor.execute("""
        INSERT INTO attendance_logs
        (user_id, start_time, latitude, longitude, distance,
         eta_minutes, decision, status, live_lat, live_lon, live_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (student_id, start_time, latitude, longitude,
          round(distance_km, 2), eta_minutes, "Journey Started",
          status, latitude, longitude, "Travelling"))
    conn.commit()
    conn.close()

    return jsonify({"distance_km": round(distance_km, 2), "eta_minutes": eta_minutes,
                    "start_time": start_time, "status": status})


# ═══════════════════════════════════════════════════════════
#  LIVE LOCATION UPDATE
# ═══════════════════════════════════════════════════════════

@app.route("/update_location", methods=["POST"])
def update_location():
    data       = request.get_json()
    student_id = data["student_id"]
    latitude   = float(data["latitude"])
    longitude  = float(data["longitude"])
    conn       = get_db()
    cursor     = conn.cursor()
    today      = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT live_status FROM attendance_logs WHERE user_id=? AND date(start_time)=?",
                   (student_id, today))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "No journey found for today."})
    if row["live_status"] in ("Arrived", "Stopped"):
        conn.close()
        return jsonify({"arrived": True, "live_status": row["live_status"]})

    dist_to_college = haversine_distance(latitude, longitude, COLLEGE_LAT, COLLEGE_LON)
    arrived         = dist_to_college <= ARRIVAL_RADIUS_KM
    live_status     = "Arrived" if arrived else "Travelling"

    if arrived:
        cursor.execute("""
            UPDATE attendance_logs SET live_lat=?, live_lon=?, live_status=?, arrived_time=?
            WHERE user_id=? AND date(start_time)=?
        """, (latitude, longitude, live_status,
              datetime.now().strftime("%Y-%m-%d %H:%M:%S"), student_id, today))
    else:
        cursor.execute("""
            UPDATE attendance_logs SET live_lat=?, live_lon=?, live_status=?
            WHERE user_id=? AND date(start_time)=?
        """, (latitude, longitude, live_status, student_id, today))

    conn.commit()
    conn.close()
    return jsonify({"arrived": arrived, "live_status": live_status,
                    "dist_to_college_km": round(dist_to_college, 2)})


# ═══════════════════════════════════════════════════════════
#  FACULTY: LIVE LOCATION POLL
# ═══════════════════════════════════════════════════════════

@app.route("/faculty/live_location")
def faculty_live_location():
    student_id = request.args.get("student_id")
    conn       = get_db()
    cursor     = conn.cursor()
    today      = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT live_lat, live_lon, live_status, start_time,
               eta_minutes, arrived_time, distance, latitude, longitude
        FROM attendance_logs WHERE user_id=? AND date(start_time)=?
    """, (student_id, today))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "No journey found for this student today."})

    eta_clock = "—"
    if row["start_time"] and row["eta_minutes"]:
        st        = datetime.strptime(row["start_time"], "%Y-%m-%d %H:%M:%S")
        eta_clock = (st + timedelta(minutes=row["eta_minutes"])).strftime("%I:%M %p")

    return jsonify({
        "live_lat": row["live_lat"], "live_lon": row["live_lon"],
        "live_status": row["live_status"], "start_time": row["start_time"],
        "eta_minutes": row["eta_minutes"], "eta_clock": eta_clock,
        "arrived_time": row["arrived_time"] or "—", "distance": row["distance"],
        "origin_lat": row["latitude"], "origin_lon": row["longitude"],
    })


# ═══════════════════════════════════════════════════════════
#  AI PREDICTION
# ═══════════════════════════════════════════════════════════

@app.route("/api/student_prediction", methods=["POST"])
def student_prediction():
    data       = request.get_json()
    student_id = data.get("student_id")
    start_time = data.get("start_time")
    eta_text   = data.get("eta_text")
    distance   = data.get("distance")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT date(start_time) as day, status FROM attendance_logs WHERE user_id=? ORDER BY start_time ASC",
                   (student_id,))
    history = [dict(r) for r in cursor.fetchall()]
    conn.close()

    total_days         = len(history)
    late_days          = sum(1 for h in history if h["status"] == "Late")
    ontime_days        = sum(1 for h in history if h["status"] == "On-Time")
    late_pct           = round((late_days / total_days * 100), 1) if total_days > 0 else 0
    safe_ontime_needed = math.ceil(total_days * 0.75)
    grace_at_risk      = ontime_days < safe_ontime_needed
    recent_history     = history[-5:] if len(history) >= 5 else history

    result = run_local_prediction(
        start_time=start_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        eta_text=eta_text or "Unknown", distance=distance or "Unknown",
        total_days=total_days, late_days=late_days, ontime_days=ontime_days,
        late_pct=late_pct, grace_at_risk=grace_at_risk,
        safe_ontime_needed=safe_ontime_needed, recent_history=recent_history
    )
    result.update({"total_days": total_days, "late_days": late_days,
                   "ontime_days": ontime_days, "late_pct": late_pct})
    return jsonify(result)


# ═══════════════════════════════════════════════════════════
#  ADMIN DASHBOARD
# ═══════════════════════════════════════════════════════════

@app.route("/admin_dashboard")
def admin_dashboard():
    admin_id = request.args.get("admin_id", "ADMIN001")
    conn     = get_db()
    cursor   = conn.cursor()
    today    = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT COUNT(*) FROM students")
    total_students = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM faculty")
    total_faculty = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM attendance_logs WHERE date(start_time)=?", (today,))
    today_journeys = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM attendance_logs WHERE date(start_time)=? AND status='Late'", (today,))
    today_late = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM students WHERE is_blocked=1")
    blocked_students = cursor.fetchone()[0]

    conn.close()

    return render_template("admin_dashboard.html",
        admin_id=admin_id,
        total_students=total_students,
        total_faculty=total_faculty,
        today_journeys=today_journeys,
        today_late=today_late,
        blocked_students=blocked_students,
        today=today
    )


# ── Admin: Get all students ────────────────────────────────
@app.route("/api/admin/students")
def admin_get_students():
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT student_id, name, area, password, is_blocked FROM students ORDER BY student_id")
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Admin: Get all faculty ─────────────────────────────────
@app.route("/api/admin/faculty")
def admin_get_faculty():
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT faculty_id, name, password, is_blocked FROM faculty ORDER BY faculty_id")
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Admin: Add student ─────────────────────────────────────
@app.route("/api/admin/add_student", methods=["POST"])
def admin_add_student():
    d      = request.get_json()
    conn   = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO students (student_id, name, area, password) VALUES (?,?,?,?)",
                       (d["student_id"], d["name"], d["area"], d["password"]))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "error": "Student ID already exists"})


# ── Admin: Add faculty ─────────────────────────────────────
@app.route("/api/admin/add_faculty", methods=["POST"])
def admin_add_faculty():
    d      = request.get_json()
    conn   = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO faculty (faculty_id, name, password) VALUES (?,?,?)",
                       (d["faculty_id"], d["name"], d["password"]))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "error": "Faculty ID already exists"})


# ── Admin: Delete student ──────────────────────────────────
@app.route("/api/admin/delete_student", methods=["POST"])
def admin_delete_student():
    d      = request.get_json()
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM students WHERE student_id=?", (d["student_id"],))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ── Admin: Delete faculty ──────────────────────────────────
@app.route("/api/admin/delete_faculty", methods=["POST"])
def admin_delete_faculty():
    d      = request.get_json()
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM faculty WHERE faculty_id=?", (d["faculty_id"],))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ── Admin: Reset password ──────────────────────────────────
@app.route("/api/admin/reset_password", methods=["POST"])
def admin_reset_password():
    d      = request.get_json()
    role   = d.get("role")
    uid    = d.get("id")
    newpwd = d.get("new_password")
    conn   = get_db()
    cursor = conn.cursor()

    if role == "student":
        cursor.execute("UPDATE students SET password=? WHERE student_id=?", (newpwd, uid))
    elif role == "faculty":
        cursor.execute("UPDATE faculty SET password=? WHERE faculty_id=?", (newpwd, uid))

    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ── Admin: Block / Unblock ─────────────────────────────────
@app.route("/api/admin/toggle_block", methods=["POST"])
def admin_toggle_block():
    d      = request.get_json()
    role   = d.get("role")
    uid    = d.get("id")
    block  = 1 if d.get("block") else 0
    conn   = get_db()
    cursor = conn.cursor()

    if role == "student":
        cursor.execute("UPDATE students SET is_blocked=? WHERE student_id=?", (block, uid))
    elif role == "faculty":
        cursor.execute("UPDATE faculty SET is_blocked=? WHERE faculty_id=?", (block, uid))

    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ── Admin: Full attendance history ────────────────────────
@app.route("/api/admin/attendance_history")
def admin_attendance_history():
    student_id = request.args.get("student_id", "")
    date_from  = request.args.get("from", "")
    date_to    = request.args.get("to", "")
    conn       = get_db()
    cursor     = conn.cursor()

    query  = """
        SELECT al.user_id, s.name, date(al.start_time) as day,
               al.start_time, al.arrived_time, al.distance,
               al.eta_minutes, al.status, al.live_status
        FROM attendance_logs al
        LEFT JOIN students s ON al.user_id = s.student_id
        WHERE 1=1
    """
    params = []
    if student_id:
        query += " AND al.user_id LIKE ?"; params.append(f"%{student_id}%")
    if date_from:
        query += " AND date(al.start_time) >= ?"; params.append(date_from)
    if date_to:
        query += " AND date(al.start_time) <= ?"; params.append(date_to)
    query += " ORDER BY al.start_time DESC LIMIT 500"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return jsonify([{
        "user_id":      r["user_id"],
        "name":         r["name"] or "—",
        "day":          r["day"],
        "start_time":   r["start_time"] or "—",
        "arrived_time": r["arrived_time"] or "—",
        "distance":     round(r["distance"], 2) if r["distance"] else "—",
        "eta_minutes":  r["eta_minutes"] or "—",
        "status":       r["status"] or "—",
        "live_status":  r["live_status"] or "—",
    } for r in rows])


# ── Admin: Manually mark attendance ───────────────────────
@app.route("/api/admin/mark_attendance", methods=["POST"])
def admin_mark_attendance():
    d          = request.get_json()
    student_id = d.get("student_id")
    date       = d.get("date")
    status     = d.get("status")
    conn       = get_db()
    cursor     = conn.cursor()

    # Check if record exists for that date
    cursor.execute("SELECT id FROM attendance_logs WHERE user_id=? AND date(start_time)=?",
                   (student_id, date))
    existing = cursor.fetchone()

    if existing:
        cursor.execute("UPDATE attendance_logs SET status=? WHERE user_id=? AND date(start_time)=?",
                       (status, student_id, date))
    else:
        start_time = date + " 08:00:00"
        cursor.execute("""
            INSERT INTO attendance_logs
            (user_id, start_time, latitude, longitude, distance,
             eta_minutes, decision, status, live_lat, live_lon, live_status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (student_id, start_time, COLLEGE_LAT, COLLEGE_LON,
              0, 0, "Manually Marked", status, COLLEGE_LAT, COLLEGE_LON, "Arrived"))

    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ── Admin: Export full CSV ─────────────────────────────────
@app.route("/api/admin/export_full_csv")
def admin_export_full_csv():
    date_from = request.args.get("from", "")
    date_to   = request.args.get("to", "")
    conn      = get_db()
    cursor    = conn.cursor()

    query  = """
        SELECT al.user_id, s.name, s.area, date(al.start_time) as day,
               al.start_time, al.arrived_time, al.distance,
               al.eta_minutes, al.status, al.live_status
        FROM attendance_logs al
        LEFT JOIN students s ON al.user_id = s.student_id WHERE 1=1
    """
    params = []
    if date_from: query += " AND date(al.start_time) >= ?"; params.append(date_from)
    if date_to:   query += " AND date(al.start_time) <= ?"; params.append(date_to)
    query += " ORDER BY al.start_time DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student ID","Name","Area","Date","Start Time",
                     "Arrived Time","Distance (km)","ETA (mins)","Status","Live Status"])
    for r in rows:
        writer.writerow([
            r["user_id"], r["name"] or "—", r["area"] or "—", r["day"],
            r["start_time"] or "—", r["arrived_time"] or "—",
            round(r["distance"], 2) if r["distance"] else "—",
            r["eta_minutes"] or "—", r["status"] or "—", r["live_status"] or "—",
        ])

    output.seek(0)
    fname = f"full_attendance_{date_from}_to_{date_to}.csv" if date_from else "full_attendance.csv"
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(debug=True)