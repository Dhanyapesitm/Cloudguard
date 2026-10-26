"""
Manual diagnostic script to test real email delivery with Resend.
Never run as part of the automated test suite.

Usage:
    python test_resend_email.py [recipient@example.com]
"""
import os
import sys
from pathlib import Path

import resend
from dotenv import load_dotenv


def main() -> None:
    load_dotenv(Path(__file__).parent / ".env")

    api_key = os.getenv("RESEND_API_KEY")
    if not api_key or not api_key.strip():
        print("ERROR: RESEND_API_KEY is missing from backend/.env or environment.")
        sys.exit(1)

    sender = os.getenv("RESEND_FROM_EMAIL") or "CloudGuard Test <onboarding@resend.dev>"
    recipient = sys.argv[1] if len(sys.argv) > 1 else "delivered@resend.dev"

    resend.api_key = api_key.strip()

    params = {
        "from": sender.strip(),
        "to": [recipient.strip()],
        "subject": "CloudGuard Resend Email Delivery Test",
        "text": (
            "This is a test email from CloudGuard to verify your Resend integration.\n\n"
            "If you are reading this in your inbox, real email delivery via Resend is working successfully!\n\n"
            "Notice: Provider acceptance confirms Resend received the send request. "
            "Delivery to recipient mailboxes depends on recipient domain SPF/DKIM verification."
        ),
        "html": (
            "<div style=\"font-family: sans-serif; max-width: 500px; padding: 20px; line-height: 1.5;\">"
            "<h2 style=\"color: #272550;\">CloudGuard Delivery Test</h2>"
            "<p>This is a test email from CloudGuard to verify your Resend integration.</p>"
            "<p style=\"color: #2e7d32; font-weight: bold;\">✓ Real email delivery via Resend is working!</p>"
            "<hr style=\"border: none; border-top: 1px solid #e0e0e0; margin: 20px 0;\" />"
            "<small style=\"color: #888;\">Provider acceptance confirms Resend received the request. "
            "Inbox delivery depends on domain DNS settings (SPF/DKIM/DMARC).</small>"
            "</div>"
        ),
    }

    print("Initiating test email delivery via Resend...")
    print(f"  From: {sender}")
    print(f"  To:   {recipient}")

    try:
        result = resend.Emails.send(params)
        print("SUCCESS: Resend accepted the test email.")
        print(f"Provider response ID: {result.get('id') if isinstance(result, dict) else getattr(result, 'id', result)}")
        print("Note: Provider acceptance confirms Resend accepted the email. Check the recipient inbox or Resend dashboard logs.")
    except Exception as exc:
        print("FAILURE: Email test failed.")
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()