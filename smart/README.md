# SMART Tools

## smart-auditor.py

Python rewrite of the SMART drive auditor. It checks key SMART attributes on configured drives and sends an email alert if any failure condition is detected.

Default alert thresholds:

- Reallocated sectors (ID 5) must be `0`
- Current pending sectors (ID 197) must be `0`
- Offline uncorrectable sectors (ID 198) must be `0`
- Reported uncorrectable (ID 187) must be `0` when present

### Requirements

- `python3`
- `smartctl` available in `PATH`

### Credentials

Create a file named `.credentials` in the same folder as `smart-auditor.py` with exactly:

1. Sender Gmail address on line 1
2. Gmail app password on line 2

Example:

```text
sender@example.com
abcd efgh ijkl mnop
```

### Run

```bash
python3 smart-auditor.py
```

Default behavior sends an email only when an alert condition is found.

### Options

```bash
python3 smart-auditor.py --self-test
python3 smart-auditor.py --self-test-full
python3 smart-auditor.py --print-email
python3 smart-auditor.py --full-report
python3 smart-auditor.py --full-report --print-email
python3 smart-auditor.py --help
```

Option details:

- `--self-test`: sends a self-test email
- `--self-test-full`: sends a full self-test email
- `--print-email`: prints the email content instead of sending it
- `--full-report`: emits a full per-drive report even if no alarms are present

Useful combinations:

- Preview normal alert format without sending: `python3 smart-auditor.py --print-email`
- Preview full healthy report without sending: `python3 smart-auditor.py --full-report --print-email`

### Cron Example

Run every day at 2:30 AM and append output to a log file:

```cron
30 2 * * * /usr/bin/env python3 /path/to/SynologyTools/smart/smart-auditor.py >> /var/log/smart-auditor.log 2>&1
```

Replace `/path/to/SynologyTools` with your actual absolute project path.

### Deploy To Synology Scripts Volume

This workspace includes a VS Code task named `Deploy SMART Auditor` that runs:

```bash
cp ${workspaceFolder}/smart/smart-auditor.py /Volumes/Scripts/smart-auditor.py
```

Use Command Palette -> `Tasks: Run Task` -> `Deploy SMART Auditor`.
