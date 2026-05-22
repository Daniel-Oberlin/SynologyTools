import smtplib
import ssl
from pathlib import Path
from email.message import EmailMessage


def load_credentials(credentials_path: Path) -> tuple[str, str]:
    lines = credentials_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise ValueError(".credentials must contain at least two lines: sender email and app password")
    return lines[0].strip(), lines[1].strip()


# Configuration
credentials_file = Path(__file__).with_name(".credentials")
SENDER_EMAIL, APP_PASSWORD = load_credentials(credentials_file)
RECEIVER_EMAIL = "dmo.notify@gmail.com" # Change to your desired recipient email

# Construct the email
msg = EmailMessage()
msg.set_content("Connection successful! The Synology CLI can now send email directly via Python.")
msg["Subject"] = "Synology Python SMTP Test"
msg["From"] = SENDER_EMAIL
msg["To"] = RECEIVER_EMAIL

print("Connecting to smtp.gmail.com on port 465...")

# Establish secure SSL connection and send
context = ssl.create_default_context()
try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(SENDER_EMAIL, APP_PASSWORD)
        print("Authentication successful. Sending email...")
        server.send_message(msg)
        print(f"Success! Test email sent to {RECEIVER_EMAIL}")
except Exception as e:
    print(f"\n[!] Error sending mail: {e}")
