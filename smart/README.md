# SMART Tools

## smart-auditor.py

This is a SMART drive auditor. It checks key SMART attributes on configured drives and always prints the report to standard output.

Email sending is optional and only happens when you provide `--send-email <address>`.

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
password
```

### Run

```bash
python3 smart-auditor.py
```

Default behavior prints output only and does not send email.

### Options

```bash
python3 smart-auditor.py --send-email you@example.com
python3 smart-auditor.py --help
```

Option details:

- `--send-email ADDRESS`: sends the same report to the specified receiver email address

Useful combinations:

- Print normal alert behavior and send if needed: `python3 smart-auditor.py --send-email you@example.com`

### Return Codes

- `0`: Completed successfully and no alarm condition detected
- `1`: Completed successfully but one or more alarm conditions were detected
- `64`: Failed to send email report
- `65`: Configuration or dependency error (for example: missing `.credentials` when `--send-email` is used, or missing `smartctl`)

### Deploy To Synology Scripts Volume

This workspace includes a VS Code task named `Deploy SMART Auditor` that runs:

```bash
cp ${workspaceFolder}/smart/smart-auditor.py /Volumes/Scripts/smart-auditor.py
```

Use Command Palette -> `Tasks: Run Task` -> `Deploy SMART Auditor`.
