from flask import Flask, request, Response, jsonify, render_template_string
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import os
import psycopg2
import psycopg2.extras
from datetime import datetime

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE       = os.environ.get("TWILIO_PHONE")
MIL_EMAIL          = os.environ.get("MIL_EMAIL")
PICKUP_ADDRESS     = "107 Highgrove Crescent"
SERVICE_NAME       = "Bridal Chesed Suitcase"
DATABASE_URL       = os.environ.get("DATABASE_URL")
ADMIN_PASSWORD     = os.environ.get("ADMIN_PASSWORD", "chesed2026")

sessions = {}

def get_db():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            wedding_date VARCHAR(20),
            pickup_time VARCHAR(20),
            phone VARCHAR(20),
            status VARCHAR(20) DEFAULT 'Confirmed',
            booked_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR(50) PRIMARY KEY,
            value VARCHAR(100)
        )
    """)
    cur.execute("""
        INSERT INTO settings (key, value) VALUES
            ('total_suitcases', '2'),
            ('slot_1', '11:00 AM'),
            ('slot_2', '12:00 PM')
        ON CONFLICT (key) DO NOTHING
    """)
    conn.commit()
    cur.close()
    conn.close()

def get_settings():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT key, value FROM settings")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {row['key']: row['value'] for row in rows}
    except Exception as e:
        print(f"Settings error: {e}")
        return {"total_suitcases": "2", "slot_1": "11:00 AM", "slot_2": "12:00 PM"}

def check_availability(date_str):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM bookings WHERE wedding_date = %s AND status = 'Confirmed'", (date_str,))
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return count
    except Exception as e:
        print(f"Availability error: {e}")
        return 0

def create_booking(date_str, slot, phone):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bookings (wedding_date, pickup_time, phone, status) VALUES (%s, %s, %s, 'Confirmed')",
            (date_str, slot, phone)
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Booking error: {e}")
        return False

# ── Step 1: Welcome ─────────────────────────────────────────────────────────
@app.route("/voice", methods=["GET", "POST"])
def voice():
    response = VoiceResponse()
    gather = Gather(num_digits=2, action="/got-month", method="POST", timeout=10, finish_on_key="")
    gather.say(
        f"Welcome to the {SERVICE_NAME} line. "
        "Please enter the month of your wedding using your keypad. "
        "For June press 0 6. For December press 1 2.",
        voice="alice"
    )
    response.append(gather)
    response.redirect("/voice")
    return Response(str(response), mimetype="text/xml")

# ── Step 2: Got month ───────────────────────────────────────────────────────
@app.route("/got-month", methods=["POST"])
def got_month():
    caller = request.form.get("From", "unknown")
    month  = request.form.get("Digits", "")
    sessions[caller] = {"month": month}
    response = VoiceResponse()
    gather = Gather(num_digits=2, action="/got-day", method="POST", timeout=10, finish_on_key="")
    gather.say("Now please enter the day of your wedding. For the 15th press 1 5.", voice="alice")
    response.append(gather)
    return Response(str(response), mimetype="text/xml")

# ── Step 3: Got day ─────────────────────────────────────────────────────────
@app.route("/got-day", methods=["POST"])
def got_day():
    caller = request.form.get("From", "unknown")
    day    = request.form.get("Digits", "")
    if caller in sessions:
        sessions[caller]["day"] = day
    response = VoiceResponse()
    gather = Gather(num_digits=2, action="/got-year", method="POST", timeout=10, finish_on_key="")
    gather.say("Now enter the last two digits of the year. For 2026 press 2 6.", voice="alice")
    response.append(gather)
    return Response(str(response), mimetype="text/xml")

# ── Step 4: Got year — check availability ───────────────────────────────────
@app.route("/got-year", methods=["POST"])
def got_year():
    caller  = request.form.get("From", "unknown")
    year    = request.form.get("Digits", "")
    session = sessions.get(caller, {})
    month   = session.get("month", "00")
    day     = session.get("day", "00")
    date_str = f"{month}/{day}/20{year}"
    sessions[caller]["date_str"] = date_str

    settings  = get_settings()
    total     = int(settings.get("total_suitcases", "2"))
    booked    = check_availability(date_str)
    available = total - booked

    response = VoiceResponse()
    if available <= 0:
        response.say(
            f"We're sorry, all suitcases are fully booked for {month} {day} 20{year}. "
            "Please call back to check another date. Thank you!",
            voice="alice"
        )
        sessions.pop(caller, None)
        return Response(str(response), mimetype="text/xml")

    slot_1 = settings.get("slot_1", "11:00 AM")
    slot_2 = settings.get("slot_2", "12:00 PM")
    sessions[caller]["slot_1"] = slot_1
    sessions[caller]["slot_2"] = slot_2

    gather = Gather(num_digits=1, action="/got-slot", method="POST", timeout=10)
    gather.say(
        f"Great news! A suitcase is available on {month} {day} 20{year}. "
        f"We have two pickup times at {PICKUP_ADDRESS}. "
        f"Press 1 for {slot_1}. Press 2 for {slot_2}.",
        voice="alice"
    )
    response.append(gather)
    return Response(str(response), mimetype="text/xml")

# ── Step 5: Got slot ────────────────────────────────────────────────────────
@app.route("/got-slot", methods=["POST"])
def got_slot():
    caller  = request.form.get("From", "unknown")
    digit   = request.form.get("Digits", "")
    session = sessions.get(caller, {})
    slot    = session.get("slot_1", "11:00 AM") if digit == "1" else session.get("slot_2", "12:00 PM")
    sessions[caller]["slot"] = slot
    response = VoiceResponse()
    gather = Gather(num_digits=10, action="/got-phone", method="POST", timeout=15, finish_on_key="#")
    gather.say(
        f"Perfect, {slot} is confirmed. "
        "Last step — please enter your 10 digit cell phone number "
        "to receive a confirmation text. Press pound when done.",
        voice="alice"
    )
    response.append(gather)
    return Response(str(response), mimetype="text/xml")

# ── Step 6: Complete booking ────────────────────────────────────────────────
@app.route("/got-phone", methods=["POST"])
def got_phone():
    caller   = request.form.get("From", "unknown")
    digits   = request.form.get("Digits", "")
    session  = sessions.get(caller, {})
    date_str = session.get("date_str", "your wedding date")
    slot     = session.get("slot", "your chosen time")
    recipient_phone = f"+1{digits}" if len(digits) == 10 else caller

    create_booking(date_str, slot, recipient_phone)

    response = VoiceResponse()
    response.say(
        f"You are all set! Your bridal suitcase is confirmed for {date_str} "
        f"with a {slot} pickup at {PICKUP_ADDRESS}. "
        "Please return it between 7 and 9 PM within 48 hours of your wedding. "
        "A confirmation text is on its way. Mazal tov and have a beautiful simcha!",
        voice="alice"
    )

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            to=recipient_phone,
            from_=TWILIO_PHONE,
            body=(
                f"Mazal tov! Your bridal suitcase is confirmed.\n\n"
                f"Date: {date_str}\n"
                f"Pickup: {slot}\n"
                f"Address: {PICKUP_ADDRESS}\n\n"
                f"Return between 7-9 PM within 48 hours.\n"
                f"Questions? Reply to this message."
            )
        )
    except Exception as e:
        print(f"Text error: {e}")

    sessions.pop(caller, None)
    return Response(str(response), mimetype="text/xml")


# ── Admin panel ─────────────────────────────────────────────────────────────
ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Bridal Chesed Suitcase — Admin</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }
        h1 { font-size: 22px; }
        h2 { font-size: 18px; margin-top: 30px; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 10px; border: 1px solid #ddd; text-align: left; font-size: 14px; }
        th { background: #f5f5f5; }
        .form-row { display: flex; gap: 10px; margin: 10px 0; align-items: center; }
        input { padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
        button { padding: 8px 16px; background: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
        button.red { background: #dc2626; }
        .badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #dcfce7; color: #166534; }
    </style>
</head>
<body>
    <h1>Bridal Chesed Suitcase — Admin Panel</h1>

    <h2>Settings</h2>
    <form method="POST" action="/admin/update-settings">
        <div class="form-row">
            <label>Total suitcases:</label>
            <input type="number" name="total_suitcases" value="{{ settings.total_suitcases }}" style="width:60px">
            <label>Slot 1:</label>
            <input type="text" name="slot_1" value="{{ settings.slot_1 }}" style="width:100px">
            <label>Slot 2:</label>
            <input type="text" name="slot_2" value="{{ settings.slot_2 }}" style="width:100px">
            <button type="submit">Save</button>
        </div>
    </form>

    <h2>Upcoming Bookings ({{ bookings|length }})</h2>
    <table>
        <tr><th>Wedding Date</th><th>Pickup Time</th><th>Phone</th><th>Status</th><th>Booked At</th><th>Action</th></tr>
        {% for b in bookings %}
        <tr>
            <td>{{ b.wedding_date }}</td>
            <td>{{ b.pickup_time }}</td>
            <td>{{ b.phone }}</td>
            <td><span class="badge">{{ b.status }}</span></td>
            <td>{{ b.booked_at.strftime('%m/%d %I:%M %p') if b.booked_at else '' }}</td>
            <td>
                <form method="POST" action="/admin/cancel/{{ b.id }}" style="display:inline">
                    <button class="red" type="submit" onclick="return confirm('Cancel this booking?')">Cancel</button>
                </form>
            </td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

@app.route("/admin", methods=["GET"])
def admin():
    password = request.args.get("pw", "")
    if password != ADMIN_PASSWORD:
        return "Access denied. Add ?pw=yourpassword to the URL.", 403
    settings = get_settings()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM bookings ORDER BY booked_at DESC")
    bookings = cur.fetchall()
    cur.close()
    conn.close()
    return render_template_string(ADMIN_HTML, settings=settings, bookings=bookings)

@app.route("/admin/update-settings", methods=["POST"])
def update_settings():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE settings SET value = %s WHERE key = 'total_suitcases'", (request.form.get("total_suitcases"),))
    cur.execute("UPDATE settings SET value = %s WHERE key = 'slot_1'", (request.form.get("slot_1"),))
    cur.execute("UPDATE settings SET value = %s WHERE key = 'slot_2'", (request.form.get("slot_2"),))
    conn.commit()
    cur.close()
    conn.close()
    return f'<p>Saved! <a href="/admin?pw={ADMIN_PASSWORD}">Back to admin</a></p>'

@app.route("/admin/cancel/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET status = 'Cancelled' WHERE id = %s", (booking_id,))
    conn.commit()
    cur.close()
    conn.close()
    return f'<p>Cancelled. <a href="/admin?pw={ADMIN_PASSWORD}">Back to admin</a></p>'

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
