#!/bin/sh
# Called by fw3 (include, reload=1) after every firewall reload.
/usr/sbin/netbird-gl fw-reload
exit 0
