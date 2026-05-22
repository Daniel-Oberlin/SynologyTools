# SMART Tools

## smart-auditor.py

Python rewrite of the SMART drive auditor. It checks key SMART attributes on configured drives and sends an email alert if any failure condition is detected.

### Requirements

- `python3`
- `smartctl` available in `PATH`
- Optional for self-test logging: `logger`

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

### Options

```bash
python3 smart-auditor.py --self-test
python3 smart-auditor.py --self-test-full
python3 smart-auditor.py --help
```

### Cron Example

Run every day at 2:30 AM and append output to a log file:

```cron
30 2 * * * /usr/bin/env python3 /path/to/SynologyTools/smart/smart-auditor.py >> /var/log/smart-auditor.log 2>&1
```

Replace `/path/to/SynologyTools` with your actual absolute project path.
