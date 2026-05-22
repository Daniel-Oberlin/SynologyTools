#!/usr/bin/env python3
"""Synology SMART Ground Truth Auditor (Python rewrite)."""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import smtplib
import socket
import ssl
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

# Configuration
RECEIVER_EMAIL = "dmo.notify@gmail.com"
DRIVES = ["/dev/sata1", "/dev/sata2", "/dev/sata3", "/dev/sata4", "/dev/sata5", "/dev/sata6"]
LOG_TAG = "smart-auditor"

# Thresholds
MAX_REALLOC = 0
MAX_PENDING = 0
MAX_OFFLINE = 0


def load_credentials(credentials_path: Path) -> tuple[str, str]:
    lines = credentials_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise ValueError(".credentials must contain at least two lines: sender email and app password")
    return lines[0].strip(), lines[1].strip()


def logger_available() -> bool:
    return shutil.which("logger") is not None


def emit_log(priority: str, message: str) -> None:
    if logger_available():
        subprocess.run(["logger", "-t", LOG_TAG, "-p", priority, "--", message], check=False)


def send_email_alert(subject: str, body: str, sender_email: str, app_password: str) -> None:
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = RECEIVER_EMAIL

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender_email, app_password)
        server.send_message(msg)


def to_int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    return None


def extract_raw_value(smart_data: str, attribute_name: str) -> str | None:
    pattern = re.compile(rf"^\s*\d+\s+{re.escape(attribute_name)}\s+.*?\s(\S+)\s*$")
    for line in smart_data.splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def run_self_test(hostname: str) -> int:
    if not logger_available():
        print("logger is not installed or not in PATH", file=sys.stderr)
        return 2

    emit_log("user.warning", f"event=self_test_warning host={hostname} mode=manual")
    emit_log("user.crit", f"event=self_test_critical host={hostname} mode=manual")
    print(f"Self-test complete: emitted warning and critical log events with tag {LOG_TAG}")
    return 0


def run_self_test_full(hostname: str) -> int:
    if not logger_available():
        print("logger is not installed or not in PATH", file=sys.stderr)
        return 2

    emit_log("user.warning", f"event=self_test_warning host={hostname} mode=manual_full")
    emit_log("user.crit", f"event=self_test_critical host={hostname} mode=manual_full")
    emit_log("user.warning", f"event=smartctl_query_failed host={hostname} drive=/dev/sataX rc=2 test=1")
    emit_log(
        "user.warning",
        "event=smart_parse_failed "
        f"host={hostname} drive=/dev/sataX "
        "realloc_raw=missing uncorrect_raw=missing pending_raw=missing offline_raw=missing test=1",
    )
    emit_log(
        "user.crit",
        "event=smart_threshold_failed "
        f"host={hostname} drive=/dev/sataX realloc=1 uncorrect=1 pending=1 offline=1 "
        "max_realloc=0 max_pending=0 max_offline=0 test=1",
    )
    emit_log("user.warning", f"event=notify_fallback host={hostname} backend=smtp result=failed test=1")
    print(f"Full self-test complete: emitted all smart-auditor event types with tag {LOG_TAG}")
    return 0


def run_audit(hostname: str, sender_email: str, app_password: str) -> int:
    alarm = False
    report_lines: list[str] = []

    for drive in DRIVES:
        cmd = ["smartctl", "-A", "-d", "sat", drive]
        result = subprocess.run(cmd, capture_output=True, text=True)
        data = (result.stdout or "") + (result.stderr or "")

        if result.returncode != 0:
            alarm = True
            emit_log(
                "user.warning",
                f"event=smartctl_query_failed host={hostname} drive={drive} rc={result.returncode}",
            )
            report_lines.append(f"[!] ALERT: Drive {drive} (smartctl query failed, rc={result.returncode})")
            report_lines.append(f"    - Command: {shlex.join(cmd)}")
            report_lines.append(f"    - Output: {data.strip()}")
            report_lines.append("    --------------------------------------")
            continue

        realloc_raw = extract_raw_value(data, "Reallocated_Sector_Ct")
        uncorrect_raw = extract_raw_value(data, "Reported_Uncorrect")
        pending_raw = extract_raw_value(data, "Current_Pending_Sector")
        offline_raw = extract_raw_value(data, "Offline_Uncorrectable")

        realloc = to_int_or_none(realloc_raw)
        uncorrect = to_int_or_none(uncorrect_raw)
        pending = to_int_or_none(pending_raw)
        offline = to_int_or_none(offline_raw)

        if realloc is None or uncorrect is None or pending is None or offline is None:
            alarm = True
            emit_log(
                "user.warning",
                "event=smart_parse_failed "
                f"host={hostname} drive={drive} "
                f"realloc_raw={realloc_raw or 'missing'} "
                f"uncorrect_raw={uncorrect_raw or 'missing'} "
                f"pending_raw={pending_raw or 'missing'} "
                f"offline_raw={offline_raw or 'missing'}",
            )
            report_lines.append(f"[!] ALERT: Drive {drive} (SMART parse failure)")
            report_lines.append(f"    - Reallocated_Sector_Ct raw: {realloc_raw or 'missing'}")
            report_lines.append(f"    - Reported_Uncorrect raw: {uncorrect_raw or 'missing'}")
            report_lines.append(f"    - Current_Pending_Sector raw: {pending_raw or 'missing'}")
            report_lines.append(f"    - Offline_Uncorrectable raw: {offline_raw or 'missing'}")
            report_lines.append("    --------------------------------------")
            continue

        if (
            realloc > MAX_REALLOC
            or pending > MAX_PENDING
            or uncorrect > 0
            or offline > MAX_OFFLINE
        ):
            alarm = True
            emit_log(
                "user.crit",
                "event=smart_threshold_failed "
                f"host={hostname} drive={drive} realloc={realloc} uncorrect={uncorrect} "
                f"pending={pending} offline={offline} "
                f"max_realloc={MAX_REALLOC} max_pending={MAX_PENDING} max_offline={MAX_OFFLINE}",
            )
            report_lines.append(f"[!] ALERT: Drive {drive} (Health Check Failed)")
            report_lines.append(f"    - Reallocated Sectors: {realloc}")
            report_lines.append(f"    - Reported Uncorrectable: {uncorrect}")
            report_lines.append(f"    - Current Pending: {pending}")
            report_lines.append(f"    - Offline Uncorrectable: {offline}")
            report_lines.append("    --------------------------------------")

    if alarm:
        body = "Automated SMART Audit Results:\n\n" + "\n".join(report_lines)
        subject = f"[URGENT] Synology Drive Health Alert - {hostname}"
        try:
            send_email_alert(subject, body, sender_email, app_password)
            print("Alert email sent.")
        except Exception as exc:
            emit_log(
                "user.warning",
                f"event=notify_fallback host={hostname} backend=smtp result=failed error={str(exc).replace(' ', '_')}",
            )
            print(f"[!] Failed to send alert email: {exc}", file=sys.stderr)
            return 1

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smart-auditor.py",
        description="Run SMART checks on configured SATA drives and send alerts on failures.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--self-test", action="store_true", help="Emit one warning and one critical logger event, then exit.")
    group.add_argument("--self-test-full", action="store_true", help="Emit all smart-auditor event types for rule validation, then exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    hostname = socket.gethostname()

    if args.self_test:
        return run_self_test(hostname)

    if args.self_test_full:
        return run_self_test_full(hostname)

    if shutil.which("smartctl") is None:
        print("smartctl is not installed or not in PATH", file=sys.stderr)
        return 2

    credentials_file = Path(__file__).with_name(".credentials")
    try:
        sender_email, app_password = load_credentials(credentials_file)
    except Exception as exc:
        print(f"Failed to load credentials from {credentials_file}: {exc}", file=sys.stderr)
        return 2

    return run_audit(hostname, sender_email, app_password)


if __name__ == "__main__":
    raise SystemExit(main())
