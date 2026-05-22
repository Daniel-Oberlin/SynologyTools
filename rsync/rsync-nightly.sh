#!/bin/bash

# Define the lockfile path - MAKE UNIQUE!
lockfile="/tmp/cron-rsync-nightly.lock"

# Attempt to acquire a lock
if ! (set -o noclobber; echo "$$" > "$lockfile") 2> /dev/null; then
  # Lock acquisition failed, exit
  echo "Failed to acquire lockfile: $lockfile. Held by $(cat $lockfile)"
  exit 1
fi

# Ensure the lockfile is removed when the script exits
trap 'rm -f "$lockfile"; exit $?' INT TERM EXIT

# Work is done here
mv /mnt/DropBox/Camera\ Uploads/* /mnt/SynologyNAS/Archives/In/Photo\ Uploads/Dr
opBox/
mv /mnt/DropBoxTracey/Camera\ Uploads/* /mnt/SynologyNAS/Archives/In/Photo\ Uplo
ads/DropBoxTracey/

if [ -d "/mnt/SynologyNAS/Public/In" ]; then
    rsync -av --exclude='#recycle' --delete /mnt/SynologyNAS/Public/   /mnt/Drobo1/Public/
fi

if [ -d "/mnt/SynologyNAS/Archives/Archives" ]; then
    rsync -av --exclude='#recycle' --delete /mnt/SynologyNAS/Archives/ /mnt/Drobo1/Archives/
fi

if [ -d "/mnt/SynologyNAS/Library/Library" ]; then
    rsync -av --exclude='#recycle' --delete /mnt/SynologyNAS/Library/  /mnt/Drobo1/Library/
fi

if [ -d "/mnt/SynologyNAS/Misc/Library" ] && [ -d "/mnt/SynologyNAS/Misc/Archives" ]; then
    rsync -av --exclude='#recycle' --delete /mnt/SynologyNAS/Misc/     /mnt/Drobo1/Misc/
fi

# Cleanup
rm -f "$lockfile"
trap - INT TERM EXIT
echo "rsync-nighty job completed"