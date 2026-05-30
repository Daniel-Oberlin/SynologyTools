#!/usr/bin/env python3
"""Synology SMART Ground Truth Auditor."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import glob
import re
import shlex
import shutil
import smtplib
import socket
import ssl
import subprocess
import sys
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from pathlib import Path

# Configuration
DRIVE_GLOB = "/dev/sata*"
CSV_FILE_NAME = "smart-auditor.csv"

# Exit codes
EXIT_OK = 0
EXIT_ALERT = 1
EXIT_EMAIL_FAILURE = 2
EXIT_CONFIG_OR_DEPENDENCY = 3

# Thresholds
MAX_REALLOC = 0
MAX_PENDING = 0
MAX_OFFLINE = 0


def load_credentials(credentials_path: Path) -> tuple[str, str]:
    lines = credentials_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise ValueError(".credentials must contain at least two lines: sender email and app password")
    return lines[0].strip(), lines[1].strip()


def normalize_email_address(raw_address: str, field_name: str) -> str:
    _, email_address = parseaddr(raw_address.strip())
    if not email_address or "@" not in email_address:
        raise ValueError(f"Invalid {field_name} email address: {raw_address!r}")
    if any(ch in email_address for ch in (" ", "\t", "\r", "\n")):
        raise ValueError(f"Invalid {field_name} email address: {raw_address!r}")
    return email_address


def parse_receiver_addresses(raw_receivers: str) -> list[str]:
    # Accept comma- or semicolon-separated recipient lists.
    cleaned = raw_receivers.replace(";", ",")
    addresses: list[str] = []
    for _, parsed in getaddresses([cleaned]):
        if not parsed:
            continue
        addresses.append(normalize_email_address(parsed, "receiver"))

    if not addresses:
        raise ValueError(f"No valid receiver email addresses found: {raw_receivers!r}")

    # Keep original order but remove duplicates.
    unique_addresses = list(dict.fromkeys(addresses))
    return unique_addresses


def send_gmail_alert(subject: str, body: str, sender_email: str, app_password: str, receiver_email: str) -> None:
    sender_address = normalize_email_address(sender_email, "sender")
    receiver_addresses = parse_receiver_addresses(receiver_email)

    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = sender_address
    msg["To"] = ", ".join(receiver_addresses)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender_address, app_password)
        server.send_message(msg, from_addr=sender_address, to_addrs=receiver_addresses)


def print_report(subject: str, body: str) -> None:
    print("----- BEGIN REPORT -----")
    print(f"Subject: {subject}")
    print("")
    print(body)
    print("----- END REPORT -----")


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


def format_metric_line(label: str, current: int, previous: int | None) -> str:
    if previous is not None and current > previous:
        return f"    - {label}: {current} (increased from {previous})"
    return f"    - {label}: {current}"


def current_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect_sata_drives() -> list[str]:
    drives = [path for path in glob.glob(DRIVE_GLOB) if re.match(r"^/dev/sata\d+$", path)]

    def drive_sort_key(path: str) -> tuple[int, str]:
        suffix = path[len("/dev/sata"):] if path.startswith("/dev/sata") else ""
        return (int(suffix), path) if suffix.isdigit() else (sys.maxsize, path)

    return sorted(drives, key=drive_sort_key)


def run_smartctl_for_drives(drives: list[str]) -> tuple[str, dict[str, int], dict[str, str]]:
    run_at = current_timestamp()
    rc_by_drive: dict[str, int] = {}
    output_by_drive: dict[str, str] = {}

    for drive in drives:
        cmd = ["smartctl", "-A", "-d", "sat", drive]
        result = subprocess.run(cmd, capture_output=True, text=True)
        rc_by_drive[drive] = result.returncode
        output_by_drive[drive] = ((result.stdout or "") + (result.stderr or "")).rstrip("\n")

    return run_at, rc_by_drive, output_by_drive


def drive_label(drive: str) -> str:
    return Path(drive).name


def build_history_row(run_at: str, output_by_drive: dict[str, str], drives: list[str]) -> dict[str, str]:
    row: dict[str, str] = {"timestamp": run_at}
    for drive in drives:
        data = output_by_drive.get(drive, "")
        _, values_by_name = parse_smart_attributes(data)
        prefix = drive_label(drive)
        for param_name, value in values_by_name.items():
            row[f"{prefix}:{param_name}"] = str(value)
    return row


def read_history_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def write_history_rows(csv_path: Path, rows: list[dict[str, str]]) -> None:
    dynamic_columns = sorted(
        {
            key
            for row in rows
            for key in row.keys()
            if key != "timestamp"
        }
    )
    fieldnames = ["timestamp", *dynamic_columns]

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            normalized_row = {column: row.get(column, "") for column in fieldnames}
            writer.writerow(normalized_row)


def update_history_csv(
    csv_path: Path, run_at: str, output_by_drive: dict[str, str], drives: list[str]
) -> dict[str, str] | None:
    rows = read_history_rows(csv_path)
    previous_row = rows[-1] if rows else None
    rows.append(build_history_row(run_at, output_by_drive, drives))
    write_history_rows(csv_path, rows)
    return previous_row


def get_previous_metric(previous_row: dict[str, str] | None, drive: str, metric_name: str) -> int | None:
    if previous_row is None:
        return None
    raw = previous_row.get(f"{drive_label(drive)}:{metric_name}", "").strip()
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def parse_drive_metrics(smart_data: str) -> tuple[int | None, int | None, int | None, int | None]:
    values_by_id, values_by_name = parse_smart_attributes(smart_data)

    realloc = get_attr_value(values_by_id, values_by_name, "5", "Reallocated_Sector_Ct")
    pending = get_attr_value(values_by_id, values_by_name, "197", "Current_Pending_Sector")

    uncorrect = get_attr_value(values_by_id, values_by_name, "187", "Reported_Uncorrect")
    if uncorrect is None:
        uncorrect = 0

    offline = get_attr_value(values_by_id, values_by_name, "198", "Offline_Uncorrectable")
    if offline is None:
        offline = 0

    return realloc, pending, uncorrect, offline


def run_audit(
    hostname: str,
    gmail_sender: str | None,
    gmail_app_password: str | None,
    send_gmail_address: str | None,
    alert_gmail_address: str | None,
    alert_error: bool,
) -> int:
    script_dir = Path(__file__).resolve().parent
    csv_path = script_dir / CSV_FILE_NAME
    drives = detect_sata_drives()

    if not drives:
        print(f"No SATA drives detected matching {DRIVE_GLOB}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEPENDENCY

    run_at, rc_by_drive, raw_output_by_drive = run_smartctl_for_drives(drives)
    previous_row = update_history_csv(csv_path, run_at, raw_output_by_drive, drives)
    previous_run_at = (previous_row or {}).get("timestamp")

    alert = False
    execution_error = False
    increase_alert = False
    report_lines: list[str] = []
    report_lines.append(f"Run timestamp: {run_at}")
    if previous_run_at:
        report_lines.append(f"Previous run timestamp: {previous_run_at}")
    else:
        report_lines.append("Previous run timestamp: none")
    report_lines.append("--------------------------------------")

    for drive in drives:
        data = raw_output_by_drive.get(drive, "")
        prev_realloc = get_previous_metric(previous_row, drive, "Reallocated_Sector_Ct")
        prev_pending = get_previous_metric(previous_row, drive, "Current_Pending_Sector")
        prev_uncorrect = get_previous_metric(previous_row, drive, "Reported_Uncorrect")
        prev_offline = get_previous_metric(previous_row, drive, "Offline_Uncorrectable")

        if rc_by_drive.get(drive, 1) != 0:
            alert = True
            if "permission denied" in data.lower() or "open device" in data.lower():
                execution_error = True
            report_lines.append(f"[!] ALERT: Drive {drive} (smartctl query failed, rc={rc_by_drive.get(drive)})")
            report_lines.append(f"    - Command: {shlex.join(['smartctl', '-A', '-d', 'sat', drive])}")
            report_lines.append(f"    - Output: {data.strip()}")
            report_lines.append("    --------------------------------------")
            continue

        realloc, pending, uncorrect, offline = parse_drive_metrics(data)

        if realloc is None or pending is None:
            alert = True
            values_by_id, _ = parse_smart_attributes(data)
            report_lines.append(f"[!] ALERT: Drive {drive} (SMART parse failure)")
            report_lines.append(f"    - Reallocated_Sector_Ct raw: {values_by_id.get('5', 'missing')}")
            report_lines.append(f"    - Reported_Uncorrect raw: {values_by_id.get('187', 'missing')}")
            report_lines.append(f"    - Current_Pending_Sector raw: {values_by_id.get('197', 'missing')}")
            report_lines.append(f"    - Offline_Uncorrectable raw: {values_by_id.get('198', 'missing')}")
            report_lines.append("    --------------------------------------")
            continue

        if (
            realloc > MAX_REALLOC
            or pending > MAX_PENDING
            or uncorrect > 0
            or offline > MAX_OFFLINE
        ):
            alert = True
            report_lines.append(f"[!] ALERT: Drive {drive} (Health Check Failed)")
            report_lines.append(format_metric_line("Reallocated Sectors", realloc, prev_realloc))
            report_lines.append(format_metric_line("Reported Uncorrectable", uncorrect, prev_uncorrect))
            report_lines.append(format_metric_line("Current Pending", pending, prev_pending))
            report_lines.append(format_metric_line("Offline Uncorrectable", offline, prev_offline))
            report_lines.append("    --------------------------------------")
        else:
            report_lines.append(f"[OK] Drive {drive} (Health Check Passed)")
            report_lines.append(format_metric_line("Reallocated Sectors", realloc, prev_realloc))
            report_lines.append(format_metric_line("Reported Uncorrectable", uncorrect, prev_uncorrect))
            report_lines.append(format_metric_line("Current Pending", pending, prev_pending))
            report_lines.append(format_metric_line("Offline Uncorrectable", offline, prev_offline))
            report_lines.append("    --------------------------------------")

        if previous_row is not None:
            if (
                (prev_realloc is not None and realloc is not None and realloc > prev_realloc)
                or (prev_pending is not None and pending is not None and pending > prev_pending)
                or (prev_uncorrect is not None and uncorrect is not None and uncorrect > prev_uncorrect)
                or (prev_offline is not None and offline is not None and offline > prev_offline)
            ):
                increase_alert = True
        else:
            report_lines.append("    - Previous run data: missing")
            report_lines.append("    --------------------------------------")

    if not report_lines:
        report_lines.append("No SMART data was collected.")

    # Increase-only alerting is always enabled. On first run, there is no prior
    # baseline, so absolute health checks are used.
    if previous_row is not None:
        effective_alert = increase_alert
    else:
        effective_alert = alert

    if effective_alert:
        subject = f"[ALERT] Synology Drive Health Alert - {hostname}"
    else:
        subject = f"[INFO] Synology Drive Health Report - {hostname}"

    if previous_row is None:
        report_lines.append(
            "Alert mode: increase-only (no previous run; using absolute health checks)"
        )
    else:
        report_lines.append(
            f"Alert mode: increase-only ({'triggered' if increase_alert else 'not triggered'})"
        )
    report_lines.append("--------------------------------------")

    body = "Automated SMART Audit Results:\n\n" + "\n".join(report_lines)
    print_report(subject, body)

    email_suppressed = False
    email_target: str | None = None

    if send_gmail_address:
        email_target = send_gmail_address
    elif alert_gmail_address:
        if effective_alert:
            email_target = alert_gmail_address
        else:
            print("No email sent (--alert-gmail set and no alert detected).")
            email_suppressed = True

    if email_target:
        try:
            if gmail_sender is None or gmail_app_password is None:
                print("[!] Missing Gmail credentials; cannot send Gmail.", file=sys.stderr)
                return EXIT_CONFIG_OR_DEPENDENCY
            send_gmail_alert(subject, body, gmail_sender, gmail_app_password, email_target)
            print("Report Gmail sent.")
        except Exception as exc:
            print(f"[!] Failed to send email: {exc}", file=sys.stderr)
            return EXIT_EMAIL_FAILURE
    elif not email_suppressed:
        print(
            "No email sent (use --send-gmail <address> or --alert-gmail <address> to enable)."
        )

    if execution_error:
        return EXIT_CONFIG_OR_DEPENDENCY

    if effective_alert and alert_error:
        return EXIT_ALERT
    return EXIT_OK


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smart-auditor.py",
        description="Run SMART checks on configured SATA drives, always print a report, and optionally send email.",
    )
    email_group = parser.add_mutually_exclusive_group()
    email_group.add_argument(
        "--send-gmail",
        metavar="ADDRESS",
        help="Receiver Gmail address. Sends Gmail on every run.",
    )
    email_group.add_argument(
        "--alert-gmail",
        metavar="ADDRESS",
        help="Receiver Gmail address. Sends Gmail only when an alert is detected.",
    )
    parser.add_argument(
        "--alert-error",
        action="store_true",
        help="Return exit code 1 when an alert is detected. If omitted, alerts still report but return 0.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    hostname = socket.gethostname()

    gmail_sender: str | None = None
    gmail_app_password: str | None = None

    if args.send_gmail or args.alert_gmail:
        credentials_file = Path(__file__).with_name(".credentials")
        try:
            gmail_sender, gmail_app_password = load_credentials(credentials_file)
        except Exception as exc:
            pr
            int(f"Failed to load credentials from {credentials_file}: {exc}", file=sys.stderr)
            return EXIT_CONFIG_OR_DEPENDENCY

    if shutil.which("smartctl") is None:
        print("smartctl is not installed or not in PATH", file=sys.stderr)
        return EXIT_CONFIG_OR_DEPENDENCY

    return run_audit(
        hostname,
        gmail_sender,
        gmail_app_password,
        args.send_gmail,
        args.alert_gmail,
        args.alert_error,
    )


if __name__ == "__main__":
    raise SystemExit(main())
