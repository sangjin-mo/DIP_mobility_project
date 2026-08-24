#!/usr/bin/env bash
# pi-net-watchdog -- keeps the Pi's Wi-Fi link up when NetworkManager doesn't.
#
# Runs ON the Raspberry Pi (not on the Mac), started at boot by
# pi-net-watchdog.service. Two jobs:
#
#   1. Boot. NetworkManager sometimes finishes starting without actually
#      bringing the Wi-Fi connection online. The first health check runs
#      immediately, and a failure on that first check is repaired at once
#      instead of waiting out FAIL_THRESHOLD misses.
#   2. Steady state. Re-check on every tick and escalate through
#      increasingly blunt repairs until the link comes back.
#
# Health = a default route exists AND the gateway (or a fallback) answers.
# The gateway is the probe target deliberately: probing an application peer
# such as the dashboard PC would report "down" whenever that machine changes
# networks, and the watchdog would then bounce a perfectly healthy link.
#
# Needs root (nmcli device reconnect, systemctl restart).

set -uo pipefail   # deliberately not -e: a failing probe must not kill the loop

IFACE="${IFACE:-}"                                  # blank = auto-detect
CHECK_INTERVAL_SEC="${CHECK_INTERVAL_SEC:-60}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-2}"               # consecutive misses before acting
GRACE_SEC="${GRACE_SEC:-20}"                        # settle time after a repair
PING_COUNT="${PING_COUNT:-2}"
PING_TIMEOUT_SEC="${PING_TIMEOUT_SEC:-2}"
FALLBACK_TARGETS="${FALLBACK_TARGETS:-1.1.1.1}"     # used only if the gateway ignores ICMP
DISABLE_POWER_SAVE="${DISABLE_POWER_SAVE:-1}"       # brcmfmac power save is a common cause
REBOOT_ENABLED="${REBOOT_ENABLED:-0}"               # last resort, off by default
REBOOT_AFTER_FAILED_CYCLES="${REBOOT_AFTER_FAILED_CYCLES:-10}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

detect_iface() {
    [ -n "$IFACE" ] && { echo "$IFACE"; return; }
    local dev
    dev="$(nmcli -t -f DEVICE,TYPE device 2>/dev/null | awk -F: '$2=="wifi"{print $1; exit}')"
    [ -n "$dev" ] && { echo "$dev"; return; }
    echo "wlan0"
}

gateway_of() { ip route 2>/dev/null | awk '/^default/{print $3; exit}'; }

# A missing default route is itself the malformed-boot signature, so it counts
# as unhealthy without any packet being sent.
healthy() {
    local gw target
    gw="$(gateway_of)"
    if [ -z "$gw" ]; then
        log "  no default route"
        return 1
    fi
    if ping -c "$PING_COUNT" -W "$PING_TIMEOUT_SEC" "$gw" >/dev/null 2>&1; then
        return 0
    fi
    for target in $FALLBACK_TARGETS; do
        if ping -c "$PING_COUNT" -W "$PING_TIMEOUT_SEC" "$target" >/dev/null 2>&1; then
            log "  gateway $gw silent but $target answered (gateway may drop ICMP)"
            return 0
        fi
    done
    log "  gateway $gw and fallbacks unreachable"
    return 1
}

power_save_off() {
    [ "$DISABLE_POWER_SAVE" = "1" ] || return 0
    command -v iw >/dev/null 2>&1 || return 0
    iw dev "$1" set power_save off >/dev/null 2>&1 \
        && log "  power_save off on $1"
}

# Escalation ladder. Each level is tried once per failed cycle; the level only
# advances when the previous one failed to restore the link, and resets to 1 on
# recovery. Re-running the same remedy every tick would itself keep the link
# down -- reassociation needs 5-15s.
remediate() {
    local level="$1" iface="$2"
    case "$level" in
        1)
            log "  L1: nmcli device reconnect $iface"
            nmcli device reconnect "$iface" >/dev/null 2>&1
            ;;
        2)
            log "  L2: bounce radio"
            nmcli radio wifi off >/dev/null 2>&1
            sleep 3
            nmcli radio wifi on >/dev/null 2>&1
            sleep 2
            nmcli device connect "$iface" >/dev/null 2>&1
            ;;
        *)
            log "  L3: systemctl restart NetworkManager"
            systemctl restart NetworkManager >/dev/null 2>&1
            ;;
    esac
    sleep "$GRACE_SEC"
    power_save_off "$iface"
}

main() {
    if [ "${EUID:-$(id -u)}" -ne 0 ]; then
        log "FATAL: must run as root (needs nmcli/systemctl)"
        exit 1
    fi
    command -v nmcli >/dev/null 2>&1 || log "WARNING: nmcli not found -- only the NetworkManager restart level will work"

    local iface fails=0 level=1 failed_cycles=0 down_since="" first_check=1
    iface="$(detect_iface)"
    log "watchdog start: iface=$iface interval=${CHECK_INTERVAL_SEC}s threshold=$FAIL_THRESHOLD reboot=$REBOOT_ENABLED"
    power_save_off "$iface"

    while true; do
        if healthy; then
            if [ -n "$down_since" ]; then
                log "RECOVERED after $(( $(date +%s) - down_since ))s"
                down_since=""
            fi
            fails=0
            level=1
            failed_cycles=0
        else
            fails=$(( fails + 1 ))
            [ -z "$down_since" ] && down_since="$(date +%s)"
            # A link that is already down on the first check is the malformed-boot
            # case: repair it now rather than waiting out FAIL_THRESHOLD misses.
            local threshold="$FAIL_THRESHOLD"
            [ "$first_check" = "1" ] && { threshold=1; log "  first check after boot -- repairing immediately"; }
            log "unhealthy ($fails/$threshold)  uptime=$(uptime -p 2>/dev/null)  throttled=$(vcgencmd get_throttled 2>/dev/null || echo n/a)"

            if [ "$fails" -ge "$threshold" ]; then
                failed_cycles=$(( failed_cycles + 1 ))
                remediate "$level" "$iface"
                if healthy; then
                    log "RECOVERED via L$level after $(( $(date +%s) - down_since ))s"
                    down_since=""; fails=0; level=1; failed_cycles=0
                else
                    [ "$level" -lt 3 ] && level=$(( level + 1 ))
                    if [ "$REBOOT_ENABLED" = "1" ] && [ "$failed_cycles" -ge "$REBOOT_AFTER_FAILED_CYCLES" ]; then
                        log "L4: rebooting after $failed_cycles failed cycles"
                        sync; reboot
                    fi
                fi
            fi
        fi
        first_check=0
        sleep "$CHECK_INTERVAL_SEC"
    done
}

main "$@"
