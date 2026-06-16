# Bridal Suitcase Chesed — Booking System

## What this does
- Answers incoming calls automatically
- Asks for wedding date via keypad
- Offers 11AM or 12PM pickup slots
- Collects caller's phone number
- Sends confirmation text immediately
- Sends reminder texts (day before + return reminder)
- Emails your mother-in-law with every booking

## Deploy to Render.com (free, step by step)

### Step 1 — Get your code online
1. Go to github.com and create a free account
2. Click "New repository" — name it `bridal-suitcase`
3. Upload all files from this folder

### Step 2 — Deploy on Render
1. Go to render.com — create a free account
2. Click "New" → "Web Service"
3. Connect your GitHub account and select the `bridal-suitcase` repo
4. Settings:
   - Environment: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
5. Click "Advanced" → "Add Environment Variables" and add all from .env.example with real values
6. Click "Create Web Service"
7. Wait ~2 minutes — you'll get a URL like: https://bridal-suitcase.onrender.com

### Step 3 — Connect to Twilio
1. Go to twilio.com → Phone Numbers → Manage → Active Numbers
2. Click your number
3. Under "Voice Configuration" → "A call comes in":
   - Set to: Webhook
   - URL: https://bridal-suitcase.onrender.com/voice
   - Method: HTTP POST
4. Hit Save

### Step 4 — Test it
Call your Twilio number and go through the flow!

## Environment Variables needed
- TWILIO_ACCOUNT_SID — from twilio.com console
- TWILIO_AUTH_TOKEN — from twilio.com console  
- TWILIO_PHONE — your Twilio number with +1 e.g. +17321234567
- MIL_EMAIL — your mother-in-law's email address
- SENDGRID_API_KEY — free at sendgrid.com (for the email notifications)

## Cost
- Render.com hosting: FREE
- Twilio phone number: ~$1.15/month
- Twilio per call: ~$0.01/minute
- Twilio per text: ~$0.008 each
- SendGrid emails: FREE (up to 100/day)
- Total: ~$3-5/month once live
