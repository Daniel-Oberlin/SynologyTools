#!/usr/bin/env python3
"""Synology SMART Ground Truth Auditor."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
DRIVES = ["/dev/sata1", "/dev/sata2", "/dev/sata3", "/dev/sata4", "/dev/sata5", "/dev/sata6"]
LOG_FILE_NAME = "smart-auditor.log"
TMP_LOG_FILE_NAME = "smart-auditor.log.tmp"
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Thresholds
MAX_REALLOC = 0
MAX_PENDING = 0
MAX_OFFLINE = 0


def load_credentials(credentials_path: Path) -> tuple[str, str]:
    lines = credentials_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise ValueError(".credentials must contain at least two lines: sender email and app password")
    return lines[0].strip(), lines[1].strip()


def send_email_alert(subject: str, body: str, sender_email: str, app_password: str, receiver_email: str) -> None:
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = receiver_email

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender_email, app_password)
        server.send_message(msg)


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


def current_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_smartctl_for_drives() -> tuple[str, dict[str, int], dict[str, str]]:
    run_at = current_timestamp()
    rc_by_drive: dict[str, int] = {}
    output_by_drive: dict[str, str] = {}

    for drive in DRIVES:
        cmd = ["smartctl", "-A", "-d", "sat", drive]
        result = subprocess.run(cmd, capture_output=True, text=True)
        rc_by_drive[drive] = result.returncode
        output_by_drive[drive] = ((result.stdout or "") + (result.stderr or "")).rstrip("\n")

    return run_at, rc_by_drive, output_by_drive


def write_tmp_log(log_tmp_path: Path, run_at: str, output_by_drive: dict[str, str]) -> None:
    lines: list[str] = [run_at]
    for drive in DRIVES:
        lines.append(drive)
        drive_output = output_by_drive.get(drive, "")
        if drive_output:
            lines.extend(drive_output.splitlines())
    log_tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_first_run_outputs(log_text: str) -> tuple[str | None, dict[str, str]]:
    lines = log_text.splitlines()
    if not lines:
        return None, {}

    run_at = lines[0].strip()
    outputs: dict[str, str] = {}
    current_drive: str | None = None
    current_lines: list[str] = []

    for raw_line in lines[1:]:
        line = raw_line.strip()

        if TIMESTAMP_RE.match(line):
            break

        if line in DRIVES:
            if current_drive is not None:
                outputs[current_drive] = "\n".join(current_lines).strip()
            current_drive = line
            current_lines = []
            continue

        if current_drive is not None:
            current_lines.append(raw_line)

    if current_drive is not None:
        outputs[current_drive] = "\n".join(current_lines).strip()

    return run_at, outputs


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


def rotate_logs(log_path: Path, log_tmp_path: Path) -> None:
    if log_path.exists():
        previous = log_path.read_text(encoding="utf-8")
        with log_tmp_path.open("a", encoding="utf-8") as tmp_handle:
            tmp_handle.write(previous)
        log_path.unlink()
    log_tmp_path.rename(log_path)


def run_audit(
    hostname: str,
    sender_email: str | None,
    app_password: str | None,
    receiver_email: str | None,
    alert_on_increase_only: bool,
) -> int:
    script_dir = Path(__file__).resolve().parent
    log_path = script_dir / LOG_FILE_NAME
    log_tmp_path = script_dir / TMP_LOG_FILE_NAME

    run_at, rc_by_drive, raw_output_by_drive = run_smartctl_for_drives()
    write_tmp_log(log_tmp_path, run_at, raw_output_by_drive)

    current_run_at, current_outputs = parse_first_run_outputs(log_tmp_path.read_text(encoding="utf-8"))

    previous_run_at: str | None = None
    previous_outputs: dict[str, str] = {}
    if log_path.exists():
        previous_run_at, previous_outputs = parse_first_run_outputs(log_path.read_text(encoding="utf-8"))

    rotate_logs(log_path, log_tmp_path)

    alarm = False
    increase_alarm = False
    report_lines: list[str] = []
    report_lines.append(f"Run timestamp: {current_run_at or run_at}")
    if previous_run_at:
        report_lines.append(f"Previous run timestamp: {previous_run_at}")
    else:
        report_lines.append("Previous run timestamp: none")
    report_lines.append("--------------------------------------")

    for drive in DRIVES:
        data = current_outputs.get(drive, "")

        if rc_by_drive.get(drive, 1) != 0:
            alarm = True
            report_lines.append(f"[!] ALERT: Drive {drive} (smartctl query failed, rc={rc_by_drive.get(drive)})")
            report_lines.append(f"    - Command: {shlex.join(['smartctl', '-A', '-d', 'sat', drive])}")
            report_lines.append(f"    - Output: {data.strip()}")
            report_lines.append("    --------------------------------------")
            continue

        realloc, pending, uncorrect, offline = parse_drive_metrics(data)

        if realloc is None or pending is None:
            alarm = True
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
            alarm = True
            report_lines.append(f"[!] ALERT: Drive {drive} (Health Check Failed)")
            report_lines.append(f"    - Reallocated Sectors: {realloc}")
            report_lines.append(f"    - Reported Uncorrectable: {uncorrect}")
            report_lines.append(f"    - Current Pending: {pending}")
            report_lines.append(f"    - Offline Uncorrectable: {offline}")
            report_lines.append("    --------------------------------------")
        else:
            report_lines.append(f"[OK] Drive {drive} (Health Check Passed)")
            report_lines.append(f"    - Reallocated Sectors: {realloc}")
            report_lines.append(f"    - Reported Uncorrectable: {uncorrect}")
            report_lines.append(f"    - Current Pending: {pending}")
            report_lines.append(f"    - Offline Uncorrectable: {offline}")
            report_lines.append("    --------------------------------------")

        prev_output = previous_outputs.get(drive)
        if prev_output:
            prev_realloc, prev_pending, prev_uncorrect, prev_offline = parse_drive_metrics(prev_output)
            increases: list[str] = []
            if prev_realloc is not None and realloc is not None and realloc > prev_realloc:
                increases.append(f"Reallocated {prev_realloc} -> {realloc}")
            if prev_pending is not None and pending is not None and pending > prev_pending:
                increases.append(f"Pending {prev_pending} -> {pending}")
            if prev_uncorrect is not None and uncorrect is not None and uncorrect > prev_uncorrect:
                increases.append(f"Reported_Uncorrect {prev_uncorrect} -> {uncorrect}")
            if prev_offline is not None and offline is not None and offline > prev_offline:
                increases.append(f"Offline_Uncorrectable {prev_offline} -> {offline}")

            if increases:
                increase_alarm = True
                report_lines.append("    - Increases vs previous run: " + "; ".join(increases))
                report_lines.append("    --------------------------------------")
        else:
            report_lines.append("    - Previous run data: missing")
            report_lines.append("    --------------------------------------")

    if not report_lines:
        report_lines.append("No SMART data was collected.")

    effective_alarm = increase_alarm if alert_on_increase_only else alarm

    if effective_alarm:
        subject = f"[URGENT] Synology Drive Health Alert - {hostname}"
    else:
        subject = f"[INFO] Synology Drive Health Report - {hostname}"

    if alert_on_increase_only:
        report_lines.append(
            f"Alert mode: increase-only ({'triggered' if increase_alarm else 'not triggered'})"
        )
        report_lines.append("--------------------------------------")

    body = "Automated SMART Audit Results:\n\n" + "\n".join(report_lines)
    print_report(subject, body)

    if receiver_email:
        try:
            if sender_email is None or app_password is None:
                print("[!] Missing SMTP credentials; cannot send email.", file=sys.stderr)
                return 65
            send_email_alert(subject, body, sender_email, app_password, receiver_email)
            print("Report email sent.")
        except Exception as exc:
            print(f"[!] Failed to send alert email: {exc}", file=sys.stderr)
            return 64
    else:
        print("No email sent (use --send-email <address> to enable).")

    if effective_alarm:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smart-auditor.py",
        description="Run SMART checks on configured SATA drives, always print a report, and optionally send email.",
    )
    parser.add_argument(
        "--send-email",
        metavar="ADDRESS",
        help="Receiver email address. If omitted, email is not sent.",
    )
    parser.add_argument(
        "--alert-on-increase-only",
        action="store_true",
        help="Only raise alarm when one or more tracked values increased vs previous run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    hostname = socket.gethostname()

    sender_email: str | None = None
    app_password: str | None = None

    if args.send_email:
        credentials_file = Path(__file__).with_name(".credentials")
        try:
            sender_email, app_password = load_credentials(credentials_file)
        except Exception as exc:
            print(f"Failed to load credentials from {credentials_file}: {exc}", file=sys.stderr)
            return 65

    if shutil.which("smartctl") is None:
        print("smartctl is not installed or not in PATH", file=sys.stderr)
        return 65

    return run_audit(
        hostname,
        sender_email,
        app_password,
        args.send_email,
        args.alert_on_increase_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
