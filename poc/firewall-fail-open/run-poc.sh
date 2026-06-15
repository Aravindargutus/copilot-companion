#!/bin/bash
# PoC harness for the init-firewall.sh fail-open bug.
#
# We run the REAL upstream script (and a fixed variant) unmodified, with the
# privileged commands (iptables/ipset/curl/dig/ip/aggregate) replaced by mocks
# on PATH. No real iptables is touched, so this is safe to run anywhere.
#
# Fidelity: a fresh container's built-in chains default to policy ACCEPT, and
# `iptables -F` flushes rules WITHOUT resetting that policy. The mock models
# exactly that: policy starts ACCEPT and only `iptables -P` changes it.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HERE/mockbin:$PATH"

run_case() {
  local label="$1" script="$2" scenario="$3"
  local state; state="$(mktemp -d)"
  export POC_STATE="$state"
  export POC_SCENARIO="$scenario"
  # Fresh container: OUTPUT chain default policy is ACCEPT after flush.
  echo "ACCEPT" > "$state/policy_OUTPUT"
  echo "ACCEPT" > "$state/policy_INPUT"
  echo "ACCEPT" > "$state/policy_FORWARD"

  bash "$script" >"$state/out.log" 2>&1
  local rc=$?
  local pol; pol="$(cat "$state/policy_OUTPUT")"

  echo "── $label"
  echo "   scenario        : $scenario"
  echo "   script exit code: $rc"
  echo "   final OUTPUT pol : $pol"
  if [ "$pol" = "ACCEPT" ]; then
    echo "   >> EGRESS UNRESTRICTED (fail-OPEN) — all outbound traffic allowed"
  else
    echo "   >> EGRESS DEFAULT-DENY (fail-closed)"
  fi
  echo "   last log line   : $(tail -n1 "$state/out.log")"
  echo
  rm -rf "$state"
}

echo "=========================================================="
echo " PoC: init-firewall.sh fail-open on transient fetch error"
echo "=========================================================="
echo
echo "### UPSTREAM script (anthropics/claude-code)"
run_case "Happy path (GitHub meta reachable)" "$HERE/init-firewall.upstream.sh" success
run_case "GitHub meta fetch fails (rate-limit/outage)" "$HERE/init-firewall.upstream.sh" fail_meta

echo "### FIXED script (default-deny set before fetches + ERR/EXIT trap)"
run_case "GitHub meta fetch fails (rate-limit/outage)" "$HERE/init-firewall.fixed.sh" fail_meta
