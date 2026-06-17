#!/bin/bash
# PoC harness: native policy hook fails open when the policy server does not
# return a clean verdict. A DENY policy is configured server-side; the agent
# attempts a gated `Bash` rm. We run the faithful hook reproduction against a
# mock policy server in each failure mode and report whether the deny was
# enforced or silently bypassed.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT=8799
PAYLOAD='{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf /tmp/poc-target"}}'

SRV=""
start_server() {
  python3 "$HERE/mock_policy_server.py" "$PORT" "$1" >/dev/null 2>&1 &
  SRV=$!
  for _ in $(seq 1 50); do
    if python3 -c "import socket,sys; s=socket.socket(); s.settimeout(0.2); sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)" 2>/dev/null; then
      return 0
    fi
    sleep 0.1
  done
  return 1
}
stop_server() { [ -n "$SRV" ] && kill "$SRV" 2>/dev/null; wait "$SRV" 2>/dev/null; SRV=""; }

cfg="$(mktemp)"
printf '{"ap_server_url":"http://127.0.0.1:%s","session_id":"conv_poc"}' "$PORT" > "$cfg"

run_mode() {
  local mode="$1"
  if ! start_server "$mode"; then echo "   (server failed to start for mode $mode)"; return; fi
  out="$(printf '%s' "$PAYLOAD" | python3 "$HERE/policy_hook_repro.py" --config "$cfg" 2>/dev/null)"
  rc=$?
  stop_server
  if printf '%s' "$out" | grep -q '"permissionDecision": *"deny"'; then
    verdict="ENFORCED  (deny emitted, tool blocked)"
  elif [ -z "$out" ]; then
    verdict=">> BYPASSED (no output -> harness proceeds, DENY policy dropped)"
  else
    verdict="other: $out"
  fi
  printf "── policy server: %-8s | hook exit: %s | %s\n" "$mode" "$rc" "$verdict"
}

echo "=================================================================="
echo " PoC: native policy hook fail-open (DENY policy on Bash 'rm')"
echo "=================================================================="
echo
run_mode ok
run_mode 503
run_mode nonjson
run_mode empty
run_mode hang
rm -f "$cfg"
echo
echo "Healthy server (ok) enforces the deny; every failure mode silently"
echo "drops it (fail-open) -> the gated command would execute."
