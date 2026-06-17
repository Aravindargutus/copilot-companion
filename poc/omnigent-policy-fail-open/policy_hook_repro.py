#!/usr/bin/env python3
"""Faithful reproduction of the control flow of
omnigent/claude_native_hook.py::_main_evaluate_policy.

Isolated to the Python standard library so it runs WITHOUT installing the
omnigent package. The security-decisive branches — every `return 0` with no
stdout (the fail-open paths) and the response->hook-output mapping — mirror
the upstream code. Only two non-decisive things are substituted:

  * transport client: urllib instead of httpx (urllib raises HTTPError on
    non-2xx, matching httpx's resp.raise_for_status();
    timeouts/connection errors raise URLError, matching httpx.HTTPError);
  * config source: a JSON file (--config) instead of the bridge dir helpers
    read_active_session_id() / read_permission_hook_config().

A `0` exit with EMPTY stdout == "no opinion" to the harness == the tool call
PROCEEDS. A deny is expressed by writing a permissionDecision JSON to stdout.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

# Upstream uses _EVALUATE_POLICY_TIMEOUT_S; shortened here so 'hang' is observable.
TIMEOUT_S = 2.0


def evaluation_response_to_hook_output(hook_event, eval_response):
    """Mirror of upstream mapping: DENY->deny, ASK->deny (fail-closed at the
    native boundary), ALLOW-> no output ("no opinion")."""
    if hook_event != "PreToolUse":
        return None
    action = str(eval_response.get("action", "ALLOW")).upper()
    if action == "DENY":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": eval_response.get("reason", "denied"),
            }
        }
    if action == "ASK":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "approval required",
            }
        }
    return None  # ALLOW -> no opinion


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        print(f"hook: malformed JSON: {exc}", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        print("hook: expected JSON object", file=sys.stderr)
        return 0

    try:
        with open(args.config) as fh:
            config = json.load(fh)
    except Exception:
        return 0  # missing/corrupt bridge config -> proceed (upstream: return 0)

    ap_server_url = config.get("ap_server_url")
    if not isinstance(ap_server_url, str) or not ap_server_url:
        return 0
    session_id = config.get("session_id")
    if not session_id:
        return 0

    hook_event = payload.get("hook_event_name", "")
    url = f"{ap_server_url.rstrip('/')}/v1/sessions/{session_id}/policies/evaluate"
    data = json.dumps({"hook_event": hook_event, "payload": payload}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:  # non-2xx == raise_for_status()
        print(f"hook: request failed: {exc}", file=sys.stderr)
        return 0
    except Exception as exc:  # timeout / connection error == httpx.HTTPError
        print(f"hook: request failed: {exc}", file=sys.stderr)
        return 0

    if not body:
        return 0  # empty body -> proceed
    try:
        eval_response = json.loads(body)
    except json.JSONDecodeError:
        return 0  # malformed JSON -> proceed

    hook_output = evaluation_response_to_hook_output(hook_event, eval_response)
    if hook_output is not None:
        sys.stdout.write(json.dumps(hook_output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
