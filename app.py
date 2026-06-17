from flask import Flask, request, Response, render_template_string
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import os
import urllib.parse
import json

def add_to_google_calendar(date_str, slot, phone):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        service_account_info = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT", "{}"))
        calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "")

        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/calendar"]
        )
        service = build("calendar", "v3", credentials=credentials)

        # Parse date
        parts = date_str.split("/")
        if len(parts) == 3:
            month, day, year = parts
            date_formatted = f"{year}-{month}-{day}"
        else:
            return

        event = {
            "summary": f"Bridal Suitcase Pickup — {phone}",
            "description": f"Pickup time: {slot}\nPhone: {phone}\nAddress: {PICKUP_ADDRESS}",
            "start": {"date": date_formatted},
            "end": {"date": date_formatted},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 24 * 60},
                    {"method": "popup", "minutes": 60}
                ]
            }
        }
        service.events().insert(calendarId=calendar_id, body=event).execute()
        print("Calendar event created")
    except Exception as e:
        print(f"Calendar error: {e}")

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE       = os.environ.get("TWILIO_PHONE")
PICKUP_ADDRESS     = "107 Highgrove Crescent"
SERVICE_NAME       = "Bridal Chesed Suitcase"
DATABASE_URL       = os.environ.get("DATABASE_URL", "")
ADMIN_PASSWORD     = os.environ.get("ADMIN_PASSWORD", "chesed2026")

sessions = {}

def get_db():
    import pg8000.dbapi
    r = urllib.parse.urlparse(DATABASE_URL)
    conn = pg8000.dbapi.connect(
        host=r.hostname,
        port=r.port or 5432,
        database=r.path[1:],
        user=r.username,
        password=r.password
    )
    conn.autocommit = True
    return conn

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            wedding_date VARCHAR(20),
            pickup_time VARCHAR(20),
            phone VARCHAR(20),
            status VARCHAR(20) DEFAULT 'Confirmed',
            booked_at TIMESTAMP DEFAULT NOW()
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR(50) PRIMARY KEY,
            value VARCHAR(300)
        )""")
        cur.execute("""INSERT INTO settings (key, value) VALUES
            ('total_suitcases', '2'),
            ('time_slots', '11:00 AM,12:00 PM')
            ON CONFLICT (key) DO NOTHING""")
        cur.close()
        conn.close()
        print("DB initialized OK")
    except Exception as e:
        print(f"DB init error: {e}")

def get_settings():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM settings")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception as e:
        print(f"Settings error: {e}")
        return {"total_suitcases": "2", "time_slots": "11:00 AM,12:00 PM"}

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
        cur.execute("INSERT INTO bookings (wedding_date, pickup_time, phone, status) VALUES (%s, %s, %s, 'Confirmed')",
                    (date_str, slot, phone))
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Booking error: {e}")
        return False

@app.route("/voice", methods=["GET", "POST"])
def voice():
    response = VoiceResponse()
    gather = Gather(num_digits=1, action="/main-menu", method="POST", timeout=10)
    gather.say(
        f"Welcome to the {SERVICE_NAME} line. "
        "Press 1 to book a suitcase. "
        "Press 2 to confirm you have returned your suitcase. "
        "Press 3 to speak with someone or get help.",
        voice="alice"
    )
    response.append(gather)
    response.redirect("/voice")
    return Response(str(response), mimetype="text/xml")

@app.route("/main-menu", methods=["POST"])
def main_menu():
    digit = request.form.get("Digits", "")
    response = VoiceResponse()
    if digit == "3":
        response.say(
            "To speak with someone or get help, please call or text 7 3 2 5 0 3 2 9 1 7. "
            "We will do our best to assist you. Thank you and have a wonderful day!",
            voice="alice"
        )
        return Response(str(response), mimetype="text/xml")
    if digit == "2":
        gather = Gather(num_digits=10, action="/confirm-return", method="POST", timeout=15, finish_on_key="#")
        gather.say(
            "To confirm your suitcase return, please enter the 10 digit phone number you used to book. Press pound when done.",
            voice="alice"
        )
        response.append(gather)
        return Response(str(response), mimetype="text/xml")
    # Press 1 or anything else — proceed to booking
    gather = Gather(num_digits=2, action="/got-month", method="POST", timeout=10, finish_on_key="")
    gather.say("Please enter the month of your wedding using your keypad. For June press 0 6. For December press 1 2.", voice="alice")
    response.append(gather)
    return Response(str(response), mimetype="text/xml")

@app.route("/confirm-return", methods=["POST"])
def confirm_return():
    digits = request.form.get("Digits", "")
    phone = f"+1{digits}" if len(digits) == 10 else digits
    response = VoiceResponse()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM bookings WHERE phone = %s AND status = 'Confirmed' ORDER BY booked_at DESC LIMIT 1",
            (phone,)
        )
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE bookings SET status = 'Returned' WHERE id = %s", (row[0],))
            response.say("Thank you! Your suitcase return has been confirmed. Mazal tov again on your simcha!", voice="alice")
        else:
            response.say(
                "We could not find an active booking under that phone number. "
                "If you need help, please call or text 7 3 2 5 0 3 2 9 1 7.",
                voice="alice"
            )
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Confirm return error: {e}")
        response.say("Sorry, something went wrong. Please call or text 7 3 2 5 0 3 2 9 1 7 for help.", voice="alice")
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
        response.say(f"We're sorry, all suitcases are fully booked for {month} {day} 20{year}. If you need assistance or would like to reach out, please call 7 3 2 5 0 3 2 9 1 7. Thank you!", voice="alice")
        sessions.pop(caller, None)
        return Response(str(response), mimetype="text/xml")

    slots_str = settings.get("time_slots", "11:00 AM,12:00 PM")
    slots = [s.strip() for s in slots_str.split(",") if s.strip()]
    sessions[caller]["slots"] = slots

    slot_phrases = " ".join([f"Press {i+1} for {s}." for i, s in enumerate(slots)])

    gather = Gather(num_digits=1, action="/got-slot", method="POST", timeout=10)
    gather.say(f"Great news! A suitcase is available on {month} {day} 20{year}. We have pickup times at {PICKUP_ADDRESS}. {slot_phrases}", voice="alice")
    response.append(gather)
    return Response(str(response), mimetype="text/xml")

@app.route("/got-slot", methods=["POST"])
def got_slot():
    caller  = request.form.get("From", "unknown")
    digit   = request.form.get("Digits", "")
    session = sessions.get(caller, {})
    slots   = session.get("slots", ["11:00 AM", "12:00 PM"])
    try:
        idx = int(digit) - 1
        slot = slots[idx] if 0 <= idx < len(slots) else slots[0]
    except (ValueError, IndexError):
        slot = slots[0]
    sessions[caller]["slot"] = slot
    response = VoiceResponse()
    gather = Gather(num_digits=10, action="/got-phone", method="POST", timeout=15, finish_on_key="#")
    gather.say(f"Perfect, {slot} is confirmed. Last step, please enter your 10 digit cell phone number to receive a confirmation text. Press pound when done.", voice="alice")
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
    add_to_google_calendar(date_str, slot, recipient_phone)

    response = VoiceResponse()
    response.say(f"You are all set! Your bridal suitcase is confirmed for {date_str} with a {slot} pickup at {PICKUP_ADDRESS}. Please return it between 7 and 9 PM within 48 hours. A confirmation text is on its way. Mazal tov and have a beautiful simcha!", voice="alice")

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            to=recipient_phone,
            from_=TWILIO_PHONE,
            body=f"Mazal tov! Your bridal suitcase is confirmed.\n\nDate: {date_str}\nPickup: {slot}\nAddress: {PICKUP_ADDRESS}\n\nReturn between 7-9 PM within 48 hours.\nQuestions? Reply to this message."
        )
    except Exception as e:
        print(f"Text error: {e}")

    sessions.pop(caller, None)
    return Response(str(response), mimetype="text/xml")

ADMIN_HTML = """<!DOCTYPE html>
<html><head><title>Admin</title><meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font-family:sans-serif;max-width:800px;margin:40px auto;padding:0 20px}h1{font-size:22px}h2{font-size:18px;margin-top:30px}table{width:100%;border-collapse:collapse;margin-top:10px}th,td{padding:10px;border:1px solid #ddd;text-align:left;font-size:14px}th{background:#f5f5f5}.form-row{display:flex;gap:10px;margin:10px 0;align-items:center;flex-wrap:wrap}input{padding:8px;border:1px solid #ddd;border-radius:4px;font-size:14px}input.wide{width:300px}button{padding:8px 16px;background:#2563eb;color:white;border:none;border-radius:4px;cursor:pointer;font-size:14px}button.red{background:#dc2626}.badge{padding:2px 8px;border-radius:10px;font-size:12px;background:#dcfce7;color:#166534}.cancelled{background:#fee2e2;color:#991b1b}.hint{font-size:12px;color:#666;margin-top:4px}</style>
</head><body>
<h1>Bridal Chesed Suitcase — Admin</h1>
<h2>Settings</h2>
<form method="POST" action="/admin/update-settings?pw={{ pw }}">
<div class="form-row">
<label>Total suitcases:</label><input type="number" name="total_suitcases" value="{{ settings.total_suitcases }}" style="width:60px">
</div>
<div class="form-row">
<label>Pickup time slots:</label><input type="text" class="wide" name="time_slots" value="{{ settings.time_slots }}">
</div>
<div class="hint">Separate times with commas, e.g: 11:00 AM, 12:00 PM, 2:00 PM. Add or remove as many as you want.</div>
<div class="form-row"><button type="submit">Save</button></div>
</form>
<h2>Bookings ({{ bookings|length }})</h2>
<table><tr><th>Wedding Date</th><th>Pickup</th><th>Phone</th><th>Status</th><th>Booked At</th><th>Action</th></tr>
{% for b in bookings %}<tr>
<td>{{ b[1] }}</td><td>{{ b[2] }}</td><td>{{ b[3] }}</td>
<td><span class="badge {{ 'cancelled' if b[4] in ['Cancelled','Returned'] else '' }}">{{ b[4] }}</span></td>
<td>{{ b[5] }}</td>
<td>
{% if b[4] == 'Confirmed' %}
<form method="POST" action="/admin/returned/{{ b[0] }}?pw={{ pw }}" style="display:inline">
<button type="submit" style="background:#16a34a">Mark returned</button>
</form>
<form method="POST" action="/admin/cancel/{{ b[0] }}?pw={{ pw }}" style="display:inline">
<button class="red" type="submit" onclick="return confirm('Cancel?')">Cancel</button>
</form>
{% endif %}
</td>
</tr>{% endfor %}</table>
</body></html>"""

@app.route("/admin")
def admin():
    pw = request.args.get("pw", "")
    if pw != ADMIN_PASSWORD:
        return "Access denied. Add ?pw=chesed2026 to the URL.", 403
    settings = get_settings()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, wedding_date, pickup_time, phone, status, booked_at FROM bookings ORDER BY booked_at DESC")
    bookings = cur.fetchall()
    cur.close()
    conn.close()
    return render_template_string(ADMIN_HTML, settings=settings, bookings=bookings, pw=pw)

@app.route("/admin/update-settings", methods=["POST"])
def update_settings():
    pw = request.args.get("pw", "")
    if pw != ADMIN_PASSWORD:
        return "Access denied.", 403
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE settings SET value = %s WHERE key = 'total_suitcases'", (request.form.get("total_suitcases"),))
    cur.execute("UPDATE settings SET value = %s WHERE key = 'time_slots'", (request.form.get("time_slots"),))
    cur.close()
    conn.close()
    return f'<p>Saved! <a href="/admin?pw={pw}">Back to admin</a></p>'

@app.route("/admin/cancel/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    pw = request.args.get("pw", "")
    if pw != ADMIN_PASSWORD:
        return "Access denied.", 403
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET status = 'Cancelled' WHERE id = %s", (booking_id,))
    cur.close()
    conn.close()
    return f'<p>Cancelled. <a href="/admin?pw={pw}">Back to admin</a></p>'

@app.route("/admin/returned/<int:booking_id>", methods=["POST"])
def mark_returned(booking_id):
    pw = request.args.get("pw", "")
    if pw != ADMIN_PASSWORD:
        return "Access denied.", 403
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET status = 'Returned' WHERE id = %s", (booking_id,))
    cur.close()
    conn.close()
    return f'<p>Marked as returned — suitcase is now available again. <a href="/admin?pw={pw}">Back to admin</a></p>'

@app.route("/health")
def health():
    return "OK"

with app.app_context():
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
