from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import os
import requests
from datetime import datetime

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE       = os.environ.get("TWILIO_PHONE")
MIL_EMAIL          = os.environ.get("MIL_EMAIL")
PICKUP_ADDRESS     = "107 Highgrove Crescent"
SERVICE_NAME       = "Bridal Chesed Suitcase"

AIRTABLE_API_KEY   = os.environ.get("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID   = os.environ.get("AIRTABLE_BASE_ID")
AIRTABLE_TABLE     = "Bookings"
AIRTABLE_SETTINGS  = "Settings"

sessions = {}

def airtable_headers():
    return {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

def get_settings():
    """Get available suitcases and time slots from Airtable Settings table."""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_SETTINGS}"
        resp = requests.get(url, headers=airtable_headers())
        records = resp.json().get("records", [])
        settings = {}
        for r in records:
            fields = r.get("fields", {})
            settings[fields.get("Key")] = fields.get("Value")
        return settings
    except Exception as e:
        print(f"Settings error: {e}")
        return {"total_suitcases": "2", "slot_1": "11:00 AM", "slot_2": "12:00 PM"}

def check_availability(date_str):
    """Check how many suitcases are booked on a given date."""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE}"
        params = {"filterByFormula": f"{{Wedding Date}}='{date_str}'"}
        resp = requests.get(url, headers=airtable_headers(), params=params)
        records = resp.json().get("records", [])
        return len(records)
    except Exception as e:
        print(f"Availability error: {e}")
        return 0

def create_booking(date_str, slot, phone):
    """Create a booking record in Airtable."""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE}"
        data = {
            "records": [{
                "fields": {
                    "Wedding Date": date_str,
                    "Pickup Time": slot,
                    "Phone": phone,
                    "Status": "Confirmed",
                    "Booked At": datetime.now().isoformat()
                }
            }]
        }
        resp = requests.post(url, headers=airtable_headers(), json=data)
        return resp.status_code == 200
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
    caller = request.form.get("From", "unknown")
    year   = request.form.get("Digits", "")
    session = sessions.get(caller, {})
    month  = session.get("month", "00")
    day    = session.get("day", "00")
    date_str = f"{month}/{day}/20{year}"
    sessions[caller]["date_str"] = date_str

    # Check availability in Airtable
    settings = get_settings()
    total    = int(settings.get("total_suitcases", "2"))
    booked   = check_availability(date_str)
    available = total - booked

    response = VoiceResponse()

    if available <= 0:
        response.say(
            f"We're sorry, both suitcases are fully booked for {month} {day} 20{year}. "
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
        f"Press 1 for {slot_1}. "
        f"Press 2 for {slot_2}.",
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

    # Save to Airtable
    create_booking(date_str, slot, recipient_phone)

    response = VoiceResponse()
    response.say(
        f"You are all set! Your bridal suitcase is confirmed for {date_str} "
        f"with a {slot} pickup at {PICKUP_ADDRESS}. "
        "Please return it between 7 and 9 PM within 48 hours of your wedding. "
        "A confirmation text is on its way. "
        "Mazal tov and have a beautiful simcha!",
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
        send_mil_email(date_str, slot, recipient_phone)
    except Exception as e:
        print(f"Notification error: {e}")

    sessions.pop(caller, None)
    return Response(str(response), mimetype="text/xml")


def send_mil_email(date_str, slot, phone):
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get("SENDGRID_API_KEY", "skip"))
        message = Mail(
            from_email="bookings@bridalchesed.com",
            to_emails=MIL_EMAIL,
            subject=f"New Suitcase Booking - {date_str}",
            plain_text_content=(
                f"New suitcase booking!\n\n"
                f"Wedding date: {date_str}\n"
                f"Pickup time: {slot}\n"
                f"Location: {PICKUP_ADDRESS}\n"
                f"Caller phone: {phone}\n\n"
                f"Please have the suitcase ready outside by the chosen pickup time."
            )
        )
        sg.send(message)
    except Exception as e:
        print(f"Email error: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
