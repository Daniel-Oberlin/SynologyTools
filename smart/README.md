# SMART Tools

## smart-auditor.py

This is a SMART drive auditor. It checks key SMART attributes on configured drives and prints the report to standard output.

It also maintains run history in `smart-auditor.log` which can be used detect changes since the previous run, or provide history over time.

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

### Log Workflow

For each run, the script:

1. Runs `smartctl` for each configured drive.
2. Writes the current run to `smart-auditor.log.tmp` in this format:
	1. First line: run datetime (UTC)
	2. Then for each drive:
		1. One line with the drive path (for example `/dev/sata1`)
		2. Full `smartctl` output for that drive
3. Parses `smart-auditor.log.tmp` to build the current run report.
4. Parses the first entry in `smart-auditor.log` (if present) as the previous run.
5. Appends old `smart-auditor.log` content to the end of `.tmp`.
6. Deletes the old `.log` and renames `.tmp` to `smart-auditor.log`.

### Options

```bash
python3 smart-auditor.py --send-email you@example.com
python3 smart-auditor.py --alert-on-increase-only
python3 smart-auditor.py --alert-on-increase-only --send-email you@example.com
python3 smart-auditor.py --help
```

Option details:

- `--send-email ADDRESS`: sends the same report to the specified receiver email address
- `--alert-on-increase-only`: only triggers an alarm when one or more tracked values increased versus the previous run
	- When a value increases, the corresponding line is annotated inline as `(increased from n)`

Useful combinations:

- Print normal alert behavior and send if needed: `python3 smart-auditor.py --send-email you@example.com`
- Alert only on increases and send email: `python3 smart-auditor.py --alert-on-increase-only --send-email you@example.com`

### Return Codes

- `0`: Completed successfully and no alarm condition detected
- `1`: One or more alarm conditions were detected
	- With `--alert-on-increase-only`, this means one or more tracked values increased versus the previous run
- `64`: Failed to send email report
- `65`: Configuration or dependency error (for example: missing `.credentials` when `--send-email` is used, or missing `smartctl`)

### Deploy To Synology Scripts Volume

This workspace includes a VS Code task named `Deploy SMART Auditor` that runs:

```bash
cp ${workspaceFolder}/smart/smart-auditor.py /Volumes/Scripts/smart-auditor.py
```

Use Command Palette -> `Tasks: Run Task` -> `Deploy SMART Auditor`.
