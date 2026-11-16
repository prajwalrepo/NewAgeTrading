"""Send a simple SMTP test email to both configured notification inboxes."""

import sys
from datetime import datetime

import scheduler


RECIPIENTS = [
    "basappafamily6@gmail.com",
    "gencinvestor@gmail.com",
]


def main():
    scheduler.load_scheduler_config()
    subject = "[EMAIL TEST] NewAgeTrading"
    body = (
        "Hi,\n\n"
        "This is a test email from emailtest.py.\n"
        f"Sent at: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
    )

    if not scheduler.send_email_notification(subject, body, recipients=RECIPIENTS):
        print("Email test failed. Check the scheduler log for SMTP details.", file=sys.stderr)
        return 1

    print(f"SMTP accepted the test email for: {', '.join(RECIPIENTS)}")
    print("This confirms SMTP submission, not final inbox delivery.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
