# SMART Tools

## smart-auditor.py

This is a SMART drive auditor. It checks key SMART attributes on configured drives and prints the report to standard output.

It also maintains run history in `smart-auditor.csv` which is used to detect changes since the previous run and keep historical values over time.

Drive discovery is automatic: each run scans `/dev/sata*` and audits all detected SATA device nodes.

Email sending is optional and only happens when you provide `--send-email <address>`.

Default alert thresholds:

- Reallocated sectors (ID 5) must be `0`
- Current pending sectors (ID 197) must be `0`
- Offline uncorrectable sectors (ID 198) must be `0`
- Reported uncorrectable (ID 187) must be `0` when present

### Requirements

- `python3`
- `smartctl` available in `PATH`
- Synology SATA device nodes available as `/dev/sata*`

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

### CSV History Workflow

For each run, the script:

1. Runs `smartctl` for each configured drive.
2. Parses SMART attributes from each drive output.
3. Builds one CSV row for the current run with `timestamp` and per-drive attribute columns using this naming pattern: `sataX:Attribute_Name`.
4. Loads existing `smart-auditor.csv` rows, appends the new row, and rewrites the entire CSV file.
5. Rebuilds the CSV header each run from all known columns across all rows.
6. Backfills missing values as empty cells for rows that do not have a given column.

Notes:

- Dynamic attributes are supported. If a drive model exposes new SMART attribute names later, new columns are added automatically on the next run.
- Previous-run comparisons are still based on the immediately previous run.
- If no `/dev/sata*` entries are detected, the script exits with code `65`.

### Options

```bash
python3 smart-auditor.py --send-email you@example.com
python3 smart-auditor.py --alert-on-increase-only
python3 smart-auditor.py --alert-on-increase-only --send-email you@example.com
python3 smart-auditor.py --help
```

Option details:

- `--send-email ADDRESS`: sends the same report to one or more receiver email addresses
	- You can provide multiple addresses separated by commas or semicolons
- `--alert-on-increase-only`: only triggers an alert when one or more tracked values increased versus the previous run
	- When a value increases, the corresponding line is annotated inline as `(increased from n)`
	- If no previous CSV row exists yet, absolute health checks are used for that run

Useful combinations:

- Print normal alert behavior and send if needed: `python3 smart-auditor.py --send-email you@example.com`
- Alert only on increases and send email: `python3 smart-auditor.py --alert-on-increase-only --send-email you@example.com`

### Return Codes

- `0`: Completed successfully and no alert condition detected
- `1`: One or more alert conditions were detected
	- With `--alert-on-increase-only`, this means one or more tracked values increased versus the previous run
- `64`: Failed to send email report
- `65`: Configuration or dependency error (for example: missing `.credentials` when `--send-email` is used, or missing `smartctl`)

### Deploy To Synology Scripts Volume

This workspace includes a VS Code task named `Deploy SMART Auditor` that runs:

```bash
cp ${workspaceFolder}/smart/smart-auditor.py /Volumes/Scripts/smart-auditor.py
```

Use Command Palette -> `Tasks: Run Task` -> `Deploy SMART Auditor`.
