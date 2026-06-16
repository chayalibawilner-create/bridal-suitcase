from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import os
from datetime import datetime, timedelta
import json

app = Flask(__name__)

# --- CONFIGURATION --- edit these before deploying ---
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE       = os.environ.get("TWILIO_PHONE")        # your Twilio number e.g. +17321234567
MIL_EMAIL          = os.environ.get("MIL_EMAIL")           # mother-in-law's email
PICKUP_ADDRESS     = "107 Highgrove Crescent"
SENDGRID_API_KEY   = os.environ.get("SENDGRID_API_KEY")    # free at sendgrid.com

# In-memory booking store (resets on restart — fine for testing)
bookings = {}

# ── Step 1: Caller arrives ──────────────────────────────────────────────────
@app.route("/voice", methods=["GET", "POST"])
def voice():
    response = VoiceResponse()
    gather = Gather(num_digits=8, action="/got-date", method="POST", timeout=10)
    gather.say(
        "Welcome to the Bridal Suitcase Chesed line. "
        "To book a suitcase, please enter your wedding date using your keypad. "
        "Enter the month, then the day, then the two-digit year. "
        "For example, for June 15th 2026, press 0 6 1 5 2 6. "
        "Press pound when done.",
        voice="alice"
    )
    response.append(gather)
    response.say("We didn't receive any input. Please call back and try again.", voice="alice")
    return Response(str(response), mimetype="text/xml")


# ── Step 2: We have the date — offer time slots ─────────────────────────────
@app.route("/got-date", methods=["POST"])
def got_date():
    digits = request.form.get("Digits", "")
    caller = request.form.get("From", "")
    response = VoiceResponse()

    if len(digits) != 8:
        response.say("Sorry, that date didn't come through correctly. Please call back and try again.", voice="alice")
        return Response(str(response), mimetype="text/xml")

    # Store date on caller session
    bookings[caller] = {"date_raw": digits, "caller": caller}

    gather = Gather(num_digits=1, action="/got-slot", method="POST", timeout=10)
    gather.say(
        f"Great! We have two pickup times available at {PICKUP_ADDRESS}. "
        "Press 1 for 11 AM. "
        "Press 2 for 12 PM. "
        "Press 9 to hear this again.",
        voice="alice"
    )
    response.append(gather)
    return Response(str(response), mimetype="text/xml")


# ── Step 3: Time slot chosen — ask for their phone number ───────────────────
@app.route("/got-slot", methods=["POST"])
def got_slot():
    digit = request.form.get("Digits", "")
    caller = request.form.get("From", "")
    response = VoiceResponse()

    if digit == "9":
        # Replay
        gather = Gather(num_digits=1, action="/got-slot", method="POST", timeout=10)
        gather.say(
            f"Press 1 for 11 AM pickup at {PICKUP_ADDRESS}. Press 2 for 12 PM.",
            voice="alice"
        )
        response.append(gather)
        return Response(str(response), mimetype="text/xml")

    slot_map = {"1": "11:00 AM", "2": "12:00 PM"}
    if digit not in slot_map:
        gather = Gather(num_digits=1, action="/got-slot", method="POST", timeout=10)
        gather.say("Sorry, please press 1 for 11 AM or press 2 for 12 PM.", voice="alice")
        response.append(gather)
        return Response(str(response), mimetype="text/xml")

    if caller in bookings:
        bookings[caller]["slot"] = slot_map[digit]

    gather = Gather(num_digits=10, action="/got-phone", method="POST", timeout=15)
    gather.say(
        "Perfect. Please enter the 10-digit cell phone number where you would like to receive "
        "your confirmation text. Enter all 10 digits and press pound when done.",
        voice="alice"
    )
    response.append(gather)
    return Response(str(response), mimetype="text/xml")


# ── Step 4: Got phone number — confirm + send texts + email ─────────────────
@app.route("/got-phone", methods=["POST"])
def got_phone():
    digits = request.form.get("Digits", "")
    caller = request.form.get("From", "")
    response = VoiceResponse()

    booking = bookings.get(caller, {})
    date_raw = booking.get("date_raw", "")
    slot     = booking.get("slot", "your chosen time")

    # Format date nicely
    try:
        mm = date_raw[0:2]
        dd = date_raw[2:4]
        yy = date_raw[4:6]
        date_str = f"{mm}/{dd}/20{yy}"
    except Exception:
        date_str = "your wedding date"

    recipient_phone = f"+1{digits}" if len(digits) == 10 else caller

    # Voice confirmation
    response.say(
        f"You are all set! Your bridal suitcase is confirmed for {date_str} "
        f"with a {slot} pickup at {PICKUP_ADDRESS}. "
        "Please return the suitcase between 7 and 9 PM within 48 hours of your wedding. "
        "You will receive a confirmation text shortly. "
        "Mazal tov and have a beautiful simcha!",
        voice="alice"
    )

    # Send texts and email in background
    try:
        send_confirmation_text(recipient_phone, date_str, slot)
        send_mil_email(date_str, slot, recipient_phone)
        schedule_reminders(recipient_phone, date_str, slot)
    except Exception as e:
        print(f"Notification error: {e}")

    # Clean up session
    bookings.pop(caller, None)

    return Response(str(response), mimetype="text/xml")


# ── Texts ────────────────────────────────────────────────────────────────────
def send_confirmation_text(to, date_str, slot):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(
        to=to,
        from_=TWILIO_PHONE,
        body=(
            f"Mazal tov! Your bridal suitcase is confirmed.\n\n"
            f"📅 Date: {date_str}\n"
            f"⏰ Pickup: {slot}\n"
            f"📍 Address: {PICKUP_ADDRESS}\n\n"
            f"Please return between 7–9 PM within 48 hours of your wedding.\n"
            f"Questions? Reply to this message."
        )
    )

def schedule_reminders(to, date_str, slot):
    """
    Sends the day-before reminder and return reminder.
    For the test run these fire immediately after booking so you can verify they work.
    In production, replace with Twilio Messaging Schedules or a cron job.
    """
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    # Day-before reminder
    client.messages.create(
        to=to,
        from_=TWILIO_PHONE,
        body=(
            f"Reminder: Your bridal suitcase pickup is tomorrow!\n"
            f"📍 {PICKUP_ADDRESS} at {slot}\n"
            f"Please remember to return it between 7–9 PM within 48 hours. Mazal tov!"
        )
    )

    # Return reminder
    client.messages.create(
        to=to,
        from_=TWILIO_PHONE,
        body=(
            f"Hi! Just a reminder that the bridal suitcase is due back between "
            f"7–9 PM at {PICKUP_ADDRESS}. "
            f"Thank you so much — and mazal tov on the simcha! 🎉"
        )
    )

    # Late warning
    client.messages.create(
        to=to,
        from_=TWILIO_PHONE,
        body=(
            f"Hi, we noticed the suitcase hasn't been returned yet. "
            f"Please bring it back to {PICKUP_ADDRESS} as soon as possible. "
            f"If there's an issue, please reply to let us know. Thank you!"
        )
    )


# ── Email to mother-in-law ───────────────────────────────────────────────────
def send_mil_email(date_str, slot, caller_phone):
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        message = Mail(
            from_email="bookings@bridalchesed.com",  # change to any email you own
            to_emails=MIL_EMAIL,
            subject=f"New Suitcase Booking — {date_str}",
            plain_text_content=(
                f"New suitcase booking!\n\n"
                f"Wedding date: {date_str}\n"
                f"Pickup time: {slot}\n"
                f"Pickup location: {PICKUP_ADDRESS}\n"
                f"Return deadline: 7–9 PM within 48 hours\n"
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
