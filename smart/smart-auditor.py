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


def send_or_print_email(
    subject: str,
    body: str,
    sender_email: str,
    app_password: str,
    print_email: bool,
) -> None:
    if print_email:
        print("----- BEGIN EMAIL PREVIEW -----")
        print(f"From: {sender_email}")
        print(f"To: {RECEIVER_EMAIL}")
        print(f"Subject: {subject}")
        print("")
        print(body)
        print("----- END EMAIL PREVIEW -----")
        return
    send_email_alert(subject, body, sender_email, app_password)


def parse_raw_integer(raw_value: str) -> int | None:
    match = re.search(r"\b(0x[0-9a-fA-F]+|\d+)\b", raw_value)
    if not match:
        return None
    token = match.group(1)
    try:
        return int(token, 0)
    except ValueError:
        return None


def parse_smart_attributes(smart_data: str) -> tuple[dict[str, int], dict[str, int]]:
    values_by_id: dict[str, int] = {}
    values_by_name: dict[str, int] = {}

    for line in smart_data.splitlines():
        parts = line.split(maxsplit=9)
        if len(parts) < 10:
            continue

        attr_id, attr_name = parts[0], parts[1]
        raw_field = parts[9]

        if not attr_id.isdigit():
            continue

        parsed = parse_raw_integer(raw_field)
        if parsed is None:
            continue

        values_by_id[attr_id] = parsed
        values_by_name[attr_name] = parsed

    return values_by_id, values_by_name


def get_attr_value(values_by_id: dict[str, int], values_by_name: dict[str, int], attr_id: str, attr_name: str) -> int | None:
    if attr_id in values_by_id:
        return values_by_id[attr_id]
    return values_by_name.get(attr_name)


def run_self_test(hostname: str, sender_email: str, app_password: str, print_email: bool) -> int:
    if logger_available():
        emit_log("user.warning", f"event=self_test_warning host={hostname} mode=manual")
        emit_log("user.crit", f"event=self_test_critical host={hostname} mode=manual")
    else:
        print("[!] logger is not installed or not in PATH; skipping log emission", file=sys.stderr)

    subject = f"[TEST] Synology SMART Auditor Self-Test - {hostname}"
    body = (
        "Self-test event notification\n\n"
        f"Host: {hostname}\n"
        "Mode: --self-test\n"
        f"Tag: {LOG_TAG}\n"
        "Events: self_test_warning, self_test_critical\n"
    )

    try:
        send_or_print_email(subject, body, sender_email, app_password, print_email)
        if print_email:
            print("Self-test complete: printed email preview.")
        else:
            print("Self-test complete: emitted log events and sent test email.")
        return 0
    except Exception as exc:
        emit_log(
            "user.warning",
            f"event=notify_fallback host={hostname} backend=smtp result=failed error={str(exc).replace(' ', '_')} test=1",
        )
        print(f"[!] Failed to send self-test email: {exc}", file=sys.stderr)
        return 1


def run_self_test_full(hostname: str, sender_email: str, app_password: str, print_email: bool) -> int:
    if logger_available():
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
    else:
        print("[!] logger is not installed or not in PATH; skipping log emission", file=sys.stderr)

    subject = f"[TEST] Synology SMART Auditor Full Self-Test - {hostname}"
    body = (
        "Full self-test event notification\n\n"
        f"Host: {hostname}\n"
        "Mode: --self-test-full\n"
        f"Tag: {LOG_TAG}\n"
        "Events: self_test_warning, self_test_critical, smartctl_query_failed, "
        "smart_parse_failed, smart_threshold_failed, notify_fallback\n"
    )

    try:
        send_or_print_email(subject, body, sender_email, app_password, print_email)
        if print_email:
            print("Full self-test complete: printed email preview.")
        else:
            print("Full self-test complete: emitted events and sent test email.")
        return 0
    except Exception as exc:
        emit_log(
            "user.warning",
            f"event=notify_fallback host={hostname} backend=smtp result=failed error={str(exc).replace(' ', '_')} test=1",
        )
        print(f"[!] Failed to send full self-test email: {exc}", file=sys.stderr)
        return 1


def run_audit(hostname: str, sender_email: str, app_password: str, print_email: bool, full_report: bool) -> int:
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

        values_by_id, values_by_name = parse_smart_attributes(data)

        realloc = get_attr_value(values_by_id, values_by_name, "5", "Reallocated_Sector_Ct")
        pending = get_attr_value(values_by_id, values_by_name, "197", "Current_Pending_Sector")

        # Some drives omit these attributes entirely; treat missing as 0.
        uncorrect = get_attr_value(values_by_id, values_by_name, "187", "Reported_Uncorrect")
        if uncorrect is None:
            uncorrect = 0

        offline = get_attr_value(values_by_id, values_by_name, "198", "Offline_Uncorrectable")
        if offline is None:
            offline = 0

        if realloc is None or pending is None:
            alarm = True
            emit_log(
                "user.warning",
                "event=smart_parse_failed "
                f"host={hostname} drive={drive} "
                f"realloc_raw={values_by_id.get('5', 'missing')} "
                f"uncorrect_raw={values_by_id.get('187', 'missing')} "
                f"pending_raw={values_by_id.get('197', 'missing')} "
                f"offline_raw={values_by_id.get('198', 'missing')}",
            )
            report_lines.append(f"[!] ALERT: Drive {drive} (SMART parse failure)")
            report_lines.append(f"    - Reallocated_Sector_Ct raw: {values_by_id.get('5', 'missing')}")
            report_lines.append(f"    - Reported_Uncorrect raw: {values_by_id.get('187', 'missing')}")
            report_lines.append(f"    - Current_Pending_Sector raw: {values_by_id.get('197', 'missing')}")
            report_lines.append(f"    - Offline_Uncorrectable raw: {values_by_id.get('198', 'missing')}")
            report_lines.append("    --------------------------------------")
            continue

        if full_report:
            report_lines.append(f"[OK] Drive {drive} (Health Check Passed)")
            report_lines.append(f"    - Reallocated Sectors: {realloc}")
            report_lines.append(f"    - Reported Uncorrectable: {uncorrect}")
            report_lines.append(f"    - Current Pending: {pending}")
            report_lines.append(f"    - Offline Uncorrectable: {offline}")
            report_lines.append("    --------------------------------------")

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

    if alarm or full_report:
        if not report_lines:
            report_lines.append("No SMART data was collected.")

        if alarm:
            subject = f"[URGENT] Synology Drive Health Alert - {hostname}"
        else:
            subject = f"[INFO] Synology Drive Health Report - {hostname}"

        body = "Automated SMART Audit Results:\n\n" + "\n".join(report_lines)
        try:
            send_or_print_email(subject, body, sender_email, app_password, print_email)
            if print_email:
                print("Report generated: printed email preview.")
            else:
                print("Report email sent.")
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
    parser.add_argument(
        "--print-email",
        action="store_true",
        help="Print the email content instead of sending it.",
    )
    parser.add_argument(
        "--full-report",
        action="store_true",
        help="Emit a full per-drive report even when no alarms are detected.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    hostname = socket.gethostname()

    credentials_file = Path(__file__).with_name(".credentials")
    try:
        sender_email, app_password = load_credentials(credentials_file)
    except Exception as exc:
        print(f"Failed to load credentials from {credentials_file}: {exc}", file=sys.stderr)
        return 2

    if args.self_test:
        return run_self_test(hostname, sender_email, app_password, args.print_email)

    if args.self_test_full:
        return run_self_test_full(hostname, sender_email, app_password, args.print_email)

    if shutil.which("smartctl") is None:
        print("smartctl is not installed or not in PATH", file=sys.stderr)
        return 2

    return run_audit(hostname, sender_email, app_password, args.print_email, args.full_report)


if __name__ == "__main__":
    raise SystemExit(main())
