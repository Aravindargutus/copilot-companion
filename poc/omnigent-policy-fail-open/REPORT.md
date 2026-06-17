# Native-harness policy hook fails open: governance (DENY/ASK) is silently bypassed when the policy server is slow, erroring, or unreachable

**Target:** `omnigent-ai/omnigent`
**Affected:** `omnigent/claude_native_hook.py::_main_evaluate_policy`, `omnigent/codex_native_hook.py` (same pattern)

## Summary

For native harnesses (Claude Code, Codex), Omnigent enforces its "policy-based
governance" via a `PreToolUse`/`PostToolUse` hook subprocess. The hook reads the
tool-call payload from stdin, POSTs an `EvaluationRequest` to
`POST /v1/sessions/{id}/policies/evaluate`, and translates the verdict back to the
harness. **Every error and edge path in this hook returns exit `0` with no stdout**,
which the harness treats as "no opinion" → the tool call proceeds. Any condition
that prevents a clean policy response — server slowness/timeout, a non-2xx status,
an empty body, malformed JSON, or a missing/corrupted bridge config — **silently
disables all DENY and ASK enforcement** for native agents. For native harnesses
this hook is the entire governance layer (it gates `Bash`, `Write`, `Edit`, the
native `Skill` tool, `mcp__github__*`, etc.), so this is a full bypass of the
product's headline safety control, and it is silent (only a `stderr` line).

## Severity

High. **CVSS:3.1** `AV:L/AC:H/PR:L/UI:N/S:C/C:H/I:H/A:N` (~6.x).
Bypassing the gate lets a compromised/prompt-injected native agent run DENY/ASK-
gated tool calls (shell, file writes, git/gh) without approval. `AC:H` because it
requires inducing or exploiting policy-server unavailability/error.

## Root cause (verbatim)

In `_main_evaluate_policy`, each of these paths returns `0` (proceed):

```python
    config = read_permission_hook_config(bridge_dir)
    ap_server_url = config.get("ap_server_url")
    if not isinstance(ap_server_url, str) or not ap_server_url:
        return 0                      # missing/corrupted bridge config -> proceed
    ...
    try:
        with httpx.Client(headers=headers, timeout=httpx.Timeout(_EVALUATE_POLICY_TIMEOUT_S)) as client:
            resp = client.post(url, json=eval_request)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"omnigent evaluate-policy hook: Omnigent request failed: {exc}", file=sys.stderr)
        return 0                      # any non-2xx / network error -> proceed
    if not resp.content:
        return 0                      # empty body -> proceed
    try:
        eval_response = resp.json()
    except json.JSONDecodeError:
        return 0                      # malformed JSON -> proceed
```

The docstring states the intent explicitly:

> "On transport failures the hook returns exit 0 with no output, which Claude Code
> treats as 'no opinion'. Fail-open design ensures a network blip doesn't block
> every tool call."

A `0` exit with empty stdout is "no opinion," so the harness proceeds and the
policy-derived deny/ask verdict is never applied.

## Attack scenario

A native agent runs under Omnigent governance with DENY/ASK policies (e.g. "deny
`Bash` that pushes to git," "ask before `Write` outside workspace"). An attacker
who has prompt-injected the agent — or the agent acting adversarially — wants a
gated action. They cause the policy server to fail to return a clean verdict:

- flood/slow the policy endpoint (the hook errors/times out), or
- act during a server restart/redeploy or a transient 5xx, or
- corrupt the bridge config the hook reads (the agent runs file tools on the same
  host; tampering with `ap_server_url`/`session_id` also yields `return 0`).

During that window every DENY/ASK policy is silently dropped and the gated tool
calls execute. The only signal is a `stderr` line the operator is unlikely to see.

## Proof of concept

Self-contained, stdlib-only, safe to run anywhere (no real agent, no real
payload — the "command" is a marker). It runs a faithful reproduction of the
upstream `_main_evaluate_policy` control flow (the decisive `return 0` fail-open
branches and the response->hook mapping are mirrored; only the transport client,
urllib vs httpx, and the config source, a JSON file vs the bridge dir, are
substituted — neither affects the decision logic) against a mock policy server in
each failure mode.

```
$ ./run-poc.sh
── policy server: ok       | hook exit: 0 | ENFORCED  (deny emitted, tool blocked)
── policy server: 503      | hook exit: 0 | >> BYPASSED (no output -> harness proceeds, DENY policy dropped)
── policy server: nonjson  | hook exit: 0 | >> BYPASSED (no output -> harness proceeds, DENY policy dropped)
── policy server: empty    | hook exit: 0 | >> BYPASSED (no output -> harness proceeds, DENY policy dropped)
── policy server: hang     | hook exit: 0 | >> BYPASSED (no output -> harness proceeds, DENY policy dropped)
```

Files: `policy_hook_repro.py` (faithful hook repro), `mock_policy_server.py`
(modes: `ok`/`503`/`nonjson`/`empty`/`hang`), `run-poc.sh` (driver).

To reproduce against the real code: point the bridge `ap_server_url` at a server
that returns `503` / a non-JSON body / hangs past the client timeout, have the
agent invoke a gated tool, and observe the hook exit `0` with no stdout while the
tool proceeds.

## Expected behavior

A security gate must fail **closed**: if the hook cannot obtain a valid ALLOW
decision for a `PreToolUse` evaluation, it should block (emit a `deny`) — at least
when the deployment has any DENY/ASK policies configured — rather than letting the
call through.

## Actual behavior

On any transport/parse/config error the hook returns `0` with no output, so the
tool call proceeds and all DENY/ASK policies are bypassed for the duration,
silently.

## Suggested fix

Default `PreToolUse` evaluations to fail-closed on error:

```python
    except httpx.HTTPError as exc:
        print(f"omnigent evaluate-policy hook: Omnigent request failed: {exc}", file=sys.stderr)
        if hook_event == "PreToolUse":
            sys.stdout.write(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Omnigent policy service unavailable (failing closed).",
                }
            }))
        return 0
```

Apply the same to the empty-body, JSON-decode, and missing-config paths. To keep
availability without losing safety: (a) make fail-open vs fail-closed a deployment
setting, defaulting closed whenever any DENY/ASK policy exists; (b) fail-open only
after a bounded retry (the codebase already has `_post_hook_with_reattach`); (c)
surface the bypass loudly (UI/audit log), not just `stderr`. `PostToolUse` can stay
observational.

## Honest caveats

- This is a **documented, intentional** trade-off (the docstring calls it
  "fail-open design"). The position here is that fail-open is the wrong default for
  a control whose purpose is to block — not that the behavior is unintended.
- It only triggers when the policy server fails to return a clean verdict; normal
  operation is unaffected (hence `AC:H`).
- The **server-side engine itself is fail-closed** (exceptions → DENY) and the
  native-boundary ASK->`deny` mapping is correct; the weakness is specifically the
  **transport hook in front of the engine** for native harnesses. (A stale docstring
  in `claude_native_hook.py` says ASK->`"defer"` while the executed mapping is the
  fail-closed ASK->`"deny"` — worth fixing separately to prevent a regression.)
