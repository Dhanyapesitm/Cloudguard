import os
from pathlib import Path

import resend
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

api_key = os.getenv("RESEND_API_KEY")

if not api_key:
    raise RuntimeError("RESEND_API_KEY is missing from backend/.env")

resend.api_key = api_key

params = {
    "from": "CloudGuard Test <onboarding@resend.dev>",
    "to": ["delivered@resend.dev"],
    "subject": "CloudGuard Resend Test",
    "text": "This is a test email from CloudGuard.",
}

try:
    result = resend.Emails.send(params)
    print("Resend accepted the test email.")
    print("Response:", result)
except Exception as exc:
    print("Email test failed.")
    print("Error:", str(exc))