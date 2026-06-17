from flask import Flask, request, Response, render_template_string
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import os
import pg8000.native
from datetime import datetime

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE       = os.environ.get("TWILIO_PHONE")
PICKUP_ADDRESS     = "107 Highgrove Crescent"
SERVICE_NAME       = "Bridal Chesed Suitcase"
DATABASE_URL       = os.environ.get("DATABASE_URL")
ADMIN_PASSWORD     = os.environ.get("ADMIN_PASSWORD", "chesed2026")

sessions = {}

def get_db():
    import urllib.parse
    r = urllib.parse.urlparse(DATABASE_URL)
    return pg8000.native.Connection(
        host=r.hostname,
        port=r.port or 5432,
        database=r.path[1:],
        user=r.username,
        password=r.password,
        ssl_context=True
    )

def init_db():
    conn = get_db()
    conn.run("""
        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            wedding_date VARCHAR(20),
            pickup_time VARCHAR(20),
            phone VARCHAR(20),
            status VARCHAR(20) DEFAULT 'Confirmed',
            booked_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.run("""
        CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR(50) PRIMARY KEY,
            value VARCHAR(100)
        )
    """)
    conn.run("""
        INSERT INTO settings (key, value) VALUES
            ('total_suitcases', '2'),
            ('slot_1', '11:00 AM'),
            ('slot_2', '12:00 PM')
        ON CONFLICT (key) DO NOTHING
    """)
    conn.close()

def get_settings():
    try:
        conn = get_db()
        rows = conn.run("SELECT key, value FROM settings")
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception as e:
        print(f"Settings error: {e}")
        return {"total_suitcases": "2", "slot_1": "11:00 AM", "slot_2": "12:00 PM"}

def check_availability(date_str):
    try:
        conn = get_db()
        rows = conn.run("SELECT COUNT(*) FROM bookings WHERE wedding_date = :d AND status = 'Confirmed'", d=date_str)
        conn.close()
        return rows[0][0]
    except Exception as e:
        print(f"Availability error: {e}")
        return 0

def create_booking(date_str, slot, phone):
    try:
        conn = get_db()
        conn.run(
            "INSERT INTO bookings (wedding_date, pickup_time, phone, status) VALUES (:d, :s, :p, 'Confirmed')",
            d=date_str, s=slot, p=phone
        )
        conn.close()
        return True
    except Exception as e:
        print(f"Booking error: {e}")
        return False

@app.route("/voice", methods=["GET", "POST"])
def voice():
    response = VoiceResponse()
    gather = Gather(num_digits=2, action="/got-month", method="POST", timeout=10, finish_on_key="")
    gather.say(f"Welcome to the {SERVICE_NAME} line. Please enter the month of your wedding using your keypad. For June press 0 6. For December press 1 2.", voice="alice")
    response.append(gather)
    response.redirect("/voice")
    return Response(str(response), mimetype="text/xml")

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
        response.say(f"We're sorry, all suitcases are fully booked for {month} {day} 20{year}. Please call back to check another date. Thank you!", voice="alice")
        sessions.pop(caller, None)
        return Response(str(response), mimetype="text/xml")

    slot_1 = settings.get("slot_1", "11:00 AM")
    slot_2 = settings.get("slot_2", "12:00 PM")
    sessions[caller]["slot_1"] = slot_1
    sessions[caller]["slot_2"] = slot_2

    gather = Gather(num_digits=1, action="/got-slot", method="POST", timeout=10)
    gather.say(f"Great news! A suitcase is available on {month} {day} 20{year}. We have two pickup times at {PICKUP_ADDRESS}. Press 1 for {slot_1}. Press 2 for {slot_2}.", voice="alice")
    response.append(gather)
    return Response(str(response), mimetype="text/xml")

@app.route("/got-slot", methods=["POST"])
def got_slot():
    caller  = request.form.get("From", "unknown")
    digit   = request.form.get("Digits", "")
    session = sessions.get(caller, {})
    slot    = session.get("slot_1", "11:00 AM") if digit == "1" else session.get("slot_2", "12:00 PM")
    sessions[caller]["slot"] = slot
    response = VoiceResponse()
    gather = Gather(num_digits=10, action="/got-phone", method="POST", timeout=15, finish_on_key="#")
    gather.say(f"Perfect, {slot} is confirmed. Last step — please enter your 10 digit cell phone number to receive a confirmation text. Press pound when done.", voice="alice")
    response.append(gather)
    return Response(str(response), mimetype="text/xml")

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
    response.say(f"You are all set! Your bridal suitcase is confirmed for {date_str} with a {slot} pickup at {PICKUP_ADDRESS}. Please return it between 7 and 9 PM within 48 hours. A confirmation text is on its way. Mazal tov and have a beautiful simcha!", voice="alice")

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            to=recipient_phone,
            from_=TWILIO_PHONE,
            body=(f"Mazal tov! Your bridal suitcase is confirmed.\n\nDate: {date_str}\nPickup: {slot}\nAddress: {PICKUP_ADDRESS}\n\nReturn between 7-9 PM within 48 hours.\nQuestions? Reply to this message.")
        )
    except Exception as e:
        print(f"Text error: {e}")

    sessions.pop(caller, None)
    return Response(str(response), mimetype="text/xml")

ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Bridal Chesed Suitcase Admin</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }
        h1 { font-size: 22px; } h2 { font-size: 18px; margin-top: 30px; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 10px; border: 1px solid #ddd; text-align: left; font-size: 14px; }
        th { background: #f5f5f5; }
        .form-row { display: flex; gap: 10px; margin: 10px 0; align-items: center; flex-wrap: wrap; }
        input { padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
        button { padding: 8px 16px; background: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
        button.red { background: #dc2626; }
        .badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #dcfce7; color: #166534; }
        .cancelled { background: #fee2e2; color: #991b1b; }
    </style>
</head>
<body>
    <h1>Bridal Chesed Suitcase — Admin</h1>
    <h2>Settings</h2>
    <form method="POST" action="/admin/update-settings?pw={{ pw }}">
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
    <h2>Bookings ({{ bookings|length }})</h2>
    <table>
        <tr><th>Wedding Date</th><th>Pickup</th><th>Phone</th><th>Status</th><th>Booked At</th><th>Action</th></tr>
        {% for b in bookings %}
        <tr>
            <td>{{ b[1] }}</td><td>{{ b[2] }}</td><td>{{ b[3] }}</td>
            <td><span class="badge {{ 'cancelled' if b[4] == 'Cancelled' else '' }}">{{ b[4] }}</span></td>
            <td>{{ b[5].strftime('%m/%d %I:%M %p') if b[5] else '' }}</td>
            <td>
                {% if b[4] != 'Cancelled' %}
                <form method="POST" action="/admin/cancel/{{ b[0] }}?pw={{ pw }}" style="display:inline">
                    <button class="red" type="submit" onclick="return confirm('Cancel?')">Cancel</button>
                </form>
                {% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

@app.route("/admin", methods=["GET"])
def admin():
    pw = request.args.get("pw", "")
    if pw != ADMIN_PASSWORD:
        return "Access denied. Add ?pw=chesed2026 to the URL.", 403
    settings = get_settings()
    conn = get_db()
    bookings = conn.run("SELECT id, wedding_date, pickup_time, phone, status, booked_at FROM bookings ORDER BY booked_at DESC")
    conn.close()
    return render_template_string(ADMIN_HTML, settings=settings, bookings=bookings, pw=pw)

@app.route("/admin/update-settings", methods=["POST"])
def update_settings():
    pw = request.args.get("pw", "")
    if pw != ADMIN_PASSWORD:
        return "Access denied.", 403
    conn = get_db()
    conn.run("UPDATE settings SET value = :v WHERE key = 'total_suitcases'", v=request.form.get("total_suitcases"))
    conn.run("UPDATE settings SET value = :v WHERE key = 'slot_1'", v=request.form.get("slot_1"))
    conn.run("UPDATE settings SET value = :v WHERE key = 'slot_2'", v=request.form.get("slot_2"))
    conn.close()
    return f'<p>Saved! <a href="/admin?pw={pw}">Back to admin</a></p>'

@app.route("/admin/cancel/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    pw = request.args.get("pw", "")
    if pw != ADMIN_PASSWORD:
        return "Access denied.", 403
    conn = get_db()
    conn.run("UPDATE bookings SET status = 'Cancelled' WHERE id = :id", id=booking_id)
    conn.close()
    return f'<p>Cancelled. <a href="/admin?pw={pw}">Back to admin</a></p>'

@app.route("/health")
def health():
    return "OK"

with app.app_context():
    try:
        init_db()
    except Exception as e:
        print(f"DB init error: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
