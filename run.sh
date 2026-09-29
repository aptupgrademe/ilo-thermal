#!/bin/sh
# Cron wrapper: one collect + report at a time (flock), output to the log.
# The jump host is not always on - @reboot runs it once shortly after boot,
# which also reads everything the iLOs logged in the meantime (IML catch-up).
cd "$(dirname "$0")" || exit 1
exec flock -n .lock sh -c './ilo_thermal.py collect && ./ilo_thermal.py report' >>cron.log 2>&1
