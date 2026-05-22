#!/bin/bash
# Synology SMART "Ground Truth" Auditor
# Targets critical sector failure IDs

# CONFIGURATION
EMAIL="dmo.notify@gmail.com"
HOSTNAME=$(hostname)
DRIVES=("/dev/sata1" "/dev/sata2" "/dev/sata3" "/dev/sata4" "/dev/sata5" "/dev/sata6")
REPORT=""
ALARM=0

# THRESHOLDS
MAX_REALLOC=0    # Strict: Any reallocation is a warning
MAX_PENDING=0    # Critical: These cause the kernel hangs you experienced
MAX_OFFLINE=0    # Treat offline-uncorrectable sectors as a hard warning
DSM_NOTIFY_TARGET="@administrators"  # Synology account/group for DSM notifications
LOG_TAG="smart-auditor"

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
        cat <<'EOF'
Usage: smart-auditor.sh [option]

Options:
    --help, -h        Show this help text and exit.
    --self-test       Emit one warning and one critical logger event, then exit.
    --self-test-full  Emit all smart-auditor event types for rule validation, then exit.

Default behavior (no option):
    Run SMART checks on configured SATA drives and emit alerts/log events on failures.
EOF
        exit 0
fi

if [ "${1:-}" = "--self-test" ]; then
    if ! command -v logger >/dev/null 2>&1; then
        printf 'logger is not installed or not in PATH\n' >&2
        exit 2
    fi

    logger -t "$LOG_TAG" -p user.warning -- "event=self_test_warning host=$HOSTNAME mode=manual"
    logger -t "$LOG_TAG" -p user.crit -- "event=self_test_critical host=$HOSTNAME mode=manual"
    printf 'Self-test complete: emitted warning and critical log events with tag %s\n' "$LOG_TAG"
    exit 0
fi

if [ "${1:-}" = "--self-test-full" ]; then
    if ! command -v logger >/dev/null 2>&1; then
        printf 'logger is not installed or not in PATH\n' >&2
        exit 2
    fi

    logger -t "$LOG_TAG" -p user.warning -- "event=self_test_warning host=$HOSTNAME mode=manual_full"
    logger -t "$LOG_TAG" -p user.crit -- "event=self_test_critical host=$HOSTNAME mode=manual_full"
    logger -t "$LOG_TAG" -p user.warning -- "event=smartctl_query_failed host=$HOSTNAME drive=/dev/sataX rc=2 test=1"
    logger -t "$LOG_TAG" -p user.warning -- "event=smart_parse_failed host=$HOSTNAME drive=/dev/sataX realloc_raw=missing uncorrect_raw=missing pending_raw=missing offline_raw=missing test=1"
    logger -t "$LOG_TAG" -p user.crit -- "event=smart_threshold_failed host=$HOSTNAME drive=/dev/sataX realloc=1 uncorrect=1 pending=1 offline=1 max_realloc=0 max_pending=0 max_offline=0 test=1"
    logger -t "$LOG_TAG" -p user.warning -- "event=notify_fallback host=$HOSTNAME backend=synodsmnotify result=failed test=1"
    printf 'Full self-test complete: emitted all smart-auditor event types with tag %s\n' "$LOG_TAG"
    exit 0
fi

if ! command -v smartctl >/dev/null 2>&1; then
    printf 'smartctl is not installed or not in PATH\n' >&2
    exit 2
fi

if command -v synodsmnotify >/dev/null 2>&1; then
    MAIL_BACKEND="synodsmnotify"
elif command -v sendmail >/dev/null 2>&1; then
    MAIL_BACKEND="sendmail"
elif command -v ssmtp >/dev/null 2>&1; then
    MAIL_BACKEND="ssmtp"
elif command -v logger >/dev/null 2>&1; then
    MAIL_BACKEND="logger"
else
    printf 'No supported notifier found (need one of: synodsmnotify, sendmail, ssmtp, logger)\n' >&2
    exit 2
fi

send_alert() {
    local subject="$1"
    local body="$2"
    local notify_output
    local notify_rc

    case "$MAIL_BACKEND" in
        sendmail)
            printf 'To: %s\nSubject: %s\n\n%b\n' "$EMAIL" "$subject" "$body" | sendmail -t
            ;;
        ssmtp)
            printf 'To: %s\nSubject: %s\n\n%b\n' "$EMAIL" "$subject" "$body" | ssmtp "$EMAIL"
            ;;
        synodsmnotify)
            notify_output=$(/usr/syno/bin/synodsmnotify "$DSM_NOTIFY_TARGET" "$subject" "$body" 2>&1)
            notify_rc=$?
            if [ "$notify_rc" -ne 0 ] || [[ "$notify_output" == *"neither mail string key nor i18n format"* ]]; then
                if command -v logger >/dev/null 2>&1; then
                    logger -t "$LOG_TAG" -p user.crit -- "event=smart_alert host=$HOSTNAME transport=syslog_fallback reason=synodsmnotify_rejected"
                    logger -t "$LOG_TAG" -p user.warning -- "event=notify_fallback host=$HOSTNAME backend=synodsmnotify result=failed"
                else
                    printf 'synodsmnotify failed: %s\n' "$notify_output" >&2
                    return 1
                fi
            fi
            ;;
        logger)
            logger -t "$LOG_TAG" -p user.crit -- "$subject: $body"
            ;;
    esac
}

to_int_or_empty() {
    local val="$1"
    if [[ "$val" =~ ^[0-9]+$ ]]; then
        printf '%s' "$val"
    fi
}

for DRIVE in "${DRIVES[@]}"; do
    # Pull SMART data using the SAT protocol
    DATA=$(smartctl -A -d sat "$DRIVE" 2>&1)
    RC=$?
    if [ "$RC" -ne 0 ]; then
        ALARM=1
        logger -t "$LOG_TAG" -p user.warning -- "event=smartctl_query_failed host=$HOSTNAME drive=$DRIVE rc=$RC"
        REPORT+="\n[!] ALERT: Drive $DRIVE (smartctl query failed, rc=$RC)\n"
        REPORT+="    - Output: $DATA\n"
        REPORT+="    --------------------------------------\n"
        continue
    fi
    
    # Extract RAW_VALUE from the SMART attribute table.
    REALLOC_RAW=$(awk '$2=="Reallocated_Sector_Ct" {print $NF; exit}' <<< "$DATA")
    UNCORRECT_RAW=$(awk '$2=="Reported_Uncorrect" {print $NF; exit}' <<< "$DATA")
    PENDING_RAW=$(awk '$2=="Current_Pending_Sector" {print $NF; exit}' <<< "$DATA")
    OFFLINE_RAW=$(awk '$2=="Offline_Uncorrectable" {print $NF; exit}' <<< "$DATA")

    REALLOC=$(to_int_or_empty "${REALLOC_RAW:-0}")
    UNCORRECT=$(to_int_or_empty "${UNCORRECT_RAW:-0}")
    PENDING=$(to_int_or_empty "${PENDING_RAW:-0}")
    OFFLINE=$(to_int_or_empty "${OFFLINE_RAW:-0}")

    # Any non-numeric parse result is treated as a monitoring failure.
    if [ -z "$REALLOC" ] || [ -z "$UNCORRECT" ] || [ -z "$PENDING" ] || [ -z "$OFFLINE" ]; then
        ALARM=1
        logger -t "$LOG_TAG" -p user.warning -- "event=smart_parse_failed host=$HOSTNAME drive=$DRIVE realloc_raw=${REALLOC_RAW:-missing} uncorrect_raw=${UNCORRECT_RAW:-missing} pending_raw=${PENDING_RAW:-missing} offline_raw=${OFFLINE_RAW:-missing}"
        REPORT+="\n[!] ALERT: Drive $DRIVE (SMART parse failure)\n"
        REPORT+="    - Reallocated_Sector_Ct raw: ${REALLOC_RAW:-missing}\n"
        REPORT+="    - Reported_Uncorrect raw: ${UNCORRECT_RAW:-missing}\n"
        REPORT+="    - Current_Pending_Sector raw: ${PENDING_RAW:-missing}\n"
        REPORT+="    - Offline_Uncorrectable raw: ${OFFLINE_RAW:-missing}\n"
        REPORT+="    --------------------------------------\n"
        continue
    fi

    # Evaluation Logic
    if [ "$REALLOC" -gt "$MAX_REALLOC" ] || [ "$PENDING" -gt "$MAX_PENDING" ] || [ "$UNCORRECT" -gt 0 ] || [ "$OFFLINE" -gt "$MAX_OFFLINE" ]; then
        ALARM=1
        logger -t "$LOG_TAG" -p user.crit -- "event=smart_threshold_failed host=$HOSTNAME drive=$DRIVE realloc=$REALLOC uncorrect=$UNCORRECT pending=$PENDING offline=$OFFLINE max_realloc=$MAX_REALLOC max_pending=$MAX_PENDING max_offline=$MAX_OFFLINE"
        REPORT+="\n[!] ALERT: Drive $DRIVE (Health Check Failed)\n"
        REPORT+="    - Reallocated Sectors: $REALLOC\n"
        REPORT+="    - Reported Uncorrectable: $UNCORRECT\n"
        REPORT+="    - Current Pending: $PENDING\n"
        REPORT+="    - Offline Uncorrectable: $OFFLINE\n"
        REPORT+="    --------------------------------------\n"
    fi
done

# If any drive is failing thresholds, send the email
if [ "$ALARM" -eq 1 ]; then
    send_alert "[URGENT] Synology Drive Health Alert - $HOSTNAME" "Automated SMART Audit Results:$REPORT"
fi