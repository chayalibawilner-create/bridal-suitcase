from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import os

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE       = os.environ.get("TWILIO_PHONE")
MIL_EMAIL          = os.environ.get("MIL_EMAIL")
PICKUP_ADDRESS     = "107 Highgrove Crescent"

# Simple in-memory store keyed by caller number
sessions = {}

# ── Step 1: Welcome + ask for month ────────────────────────────────────────
@app.route("/voice", methods=["GET", "POST"])
def voice():
    response = VoiceResponse()
    gather = Gather(num_digits=2, action="/got-month", method="POST", timeout=10, finish_on_key="")
    gather.say(
        "Welcome to the Bridal Suitcase Chesed line. "
        "Let's get you booked. "
        "Please enter the month of your wedding. "
        "For example press 0 6 for June.",
        voice="alice"
    )
    response.append(gather)
    response.redirect("/voice")
    return Response(str(response), mimetype="text/xml")


# ── Step 2: Got month, ask for day ─────────────────────────────────────────
@app.route("/got-month", methods=["POST"])
def got_month():
    caller = request.form.get("From", "unknown")
    month  = request.form.get("Digits", "")
    sessions[caller] = {"month": month}

    response = VoiceResponse()
    gather = Gather(num_digits=2, action="/got-day", method="POST", timeout=10, finish_on_key="")
    gather.say(
        f"Got it. Now please enter the day of your wedding. "
        "For example press 1 5 for the 15th.",
        voice="alice"
    )
    response.append(gather)
    response.redirect("/got-month")
    return Response(str(response), mimetype="text/xml")


# ── Step 3: Got day, ask for year ──────────────────────────────────────────
@app.route("/got-day", methods=["POST"])
def got_day():
    caller = request.form.get("From", "unknown")
    day    = request.form.get("Digits", "")
    if caller in sessions:
        sessions[caller]["day"] = day

    response = VoiceResponse()
    gather = Gather(num_digits=2, action="/got-year", method="POST", timeout=10, finish_on_key="")
    gather.say(
        "Almost there. Now please enter the last two digits of the year. "
        "For 2026 press 2 6.",
        voice="alice"
    )
    response.append(gather)
    return Response(str(response), mimetype="text/xml")


# ── Step 4: Got year, offer time slots ─────────────────────────────────────
@app.route("/got-year", methods=["POST"])
def got_year():
    caller = request.form.get("From", "unknown")
    year   = request.form.get("Digits", "")
    if caller in sessions:
        sessions[caller]["year"] = year

    session  = sessions.get(caller, {})
    month    = session.get("month", "??")
    day      = session.get("day", "??")
    date_str = f"{month}/{day}/20{year}"
    sessions[caller]["date_str"] = date_str

    response = VoiceResponse()
    gather = Gather(num_digits=1, action="/got-slot", method="POST", timeout=10)
    gather.say(
        f"Great! Your wedding date is {month} {day} 20{year}. "
        f"We have two pickup times available at {PICKUP_ADDRESS}. "
        "Press 1 for 11 AM. "
        "Press 2 for 12 PM.",
        voice="alice"
    )
    response.append(gather)
    return Response(str(response), mimetype="text/xml")


# ── Step 5: Got slot, ask for phone number ──────────────────────────────────
@app.route("/got-slot", methods=["POST"])
def got_slot():
    caller = request.form.get("From", "unknown")
    digit  = request.form.get("Digits", "")

    slot_map = {"1": "11:00 AM", "2": "12:00 PM"}
    slot = slot_map.get(digit, "11:00 AM")
    if caller in sessions:
        sessions[caller]["slot"] = slot

    response = VoiceResponse()
    gather = Gather(num_digits=10, action="/got-phone", method="POST", timeout=15, finish_on_key="#")
    gather.say(
        f"Perfect, {slot} it is. "
        "Last step. Please enter your 10 digit cell phone number "
        "so we can send you a confirmation text. "
        "Press pound when done.",
        voice="alice"
    )
    response.append(gather)
    return Response(str(response), mimetype="text/xml")


# ── Step 6: Complete booking ────────────────────────────────────────────────
@app.route("/got-phone", methods=["POST"])
def got_phone():
    caller  = request.form.get("From", "unknown")
    digits  = request.form.get("Digits", "")
    session = sessions.get(caller, {})

    date_str = session.get("date_str", "your wedding date")
    slot     = session.get("slot", "your chosen time")
    recipient_phone = f"+1{digits}" if len(digits) == 10 else caller

    response = VoiceResponse()
    response.say(
        f"You are all set! Your bridal suitcase is confirmed for {date_str} "
        f"with a {slot} pickup at {PICKUP_ADDRESS}. "
        "Please return the suitcase between 7 and 9 PM within 48 hours. "
        "A confirmation text is on its way. "
        "Mazal tov and have a beautiful simcha!",
        voice="alice"
    )

    # Send texts and email
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        # Confirmation text
        client.messages.create(
            to=recipient_phone,
            from_=TWILIO_PHONE,
            body=(
                f"Mazal tov! Your bridal suitcase is confirmed.\n\n"
                f"Date: {date_str}\n"
                f"Pickup: {slot}\n"
                f"Address: {PICKUP_ADDRESS}\n\n"
                f"Please return between 7-9 PM within 48 hours.\n"
                f"Questions? Reply to this message."
            )
        )

        # Email to MIL
        send_mil_email(date_str, slot, recipient_phone)

    except Exception as e:
        print(f"Notification error: {e}")

    sessions.pop(caller, None)
    return Response(str(response), mimetype="text/xml")


def send_mil_email(date_str, slot, caller_phone):
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get("SENDGRID_API_KEY"))
        message = Mail(
            from_email="bookings@bridalchesed.com",
            to_emails=MIL_EMAIL,
            subject=f"New Suitcase Booking - {date_str}",
            plain_text_content=(
                f"New suitcase booking!\n\n"
                f"Wedding date: {date_str}\n"
                f"Pickup time: {slot}\n"
                f"Pickup location: {PICKUP_ADDRESS}\n"
                f"Return deadline: 7-9 PM within 48 hours\n"
                f"Caller phone: {caller_phone}\n\n"
                f"Please have the suitcase ready outside by the chosen pickup time."
            )
        )
        sg.send(message)
    except Exception as e:
        print(f"Email error: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
