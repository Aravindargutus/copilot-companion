# PoC: `init-firewall.sh` fails open on a transient fetch/resolve error

**Target:** `.devcontainer/init-firewall.sh` in `anthropics/claude-code`.

**Claim under test:** the devcontainer firewall enforces default-deny egress, making
it "safe" to run Claude Code with `--dangerously-skip-permissions`.

**Bug:** the script flushes iptables (built-in chains revert to their default
`ACCEPT` policy), adds its `ACCEPT` allow-rules, then performs network calls that
can fail (`curl https://api.github.com/meta`, `jq`, a `dig` loop, `ipset add`) and
**only sets `iptables -P OUTPUT DROP` at the very end**. Under `set -euo pipefail`,
any failure in that middle section aborts the script *before* the DROP policy is
applied. Because the post-flush default is `ACCEPT`, the container comes up with
**no egress restriction at all** — and it never reaches the `example.com`
verification that would have caught it. No attacker is required; an ordinary
GitHub API rate-limit or outage is enough.

## Why this is a safe, faithful test

A default-policy ordering bug is a control-flow property, not a kernel property,
so we test it without touching real iptables:

- The **real upstream script runs unmodified** (`init-firewall.upstream.sh`, copied
  verbatim).
- The privileged commands are replaced by mocks on `PATH` (`mockbin/`).
- Fidelity: a fresh container's chains default to `ACCEPT`, and `iptables -F`
  flushes *rules* without resetting the *policy*. The mock models exactly that —
  policy starts `ACCEPT` and only `iptables -P` changes it.
- The failure is injected the way it happens in production: the mocked
  `curl .../meta` returns an empty body (rate-limit/outage), driving the script's
  own `if [ -z "$gh_ranges" ]; then ... exit 1` path.

No real networking is modified, so this runs anywhere.

## Run

```bash
./run-poc.sh
```

## Result

| Script | Scenario | Exit | Final OUTPUT policy | Egress |
|--------|----------|------|---------------------|--------|
| upstream | meta reachable | 0 | `DROP` | default-deny (correct) |
| upstream | **meta fetch fails** | 1 | **`ACCEPT`** | **UNRESTRICTED (fail-open)** |
| fixed | meta fetch fails | 1 | `DROP` | default-deny (correct) |

The upstream script leaves egress wide open when the GitHub meta fetch fails,
while a user believes they are sandboxed.

## Fix (in `init-firewall.fixed.sh`)

Set default-deny **before** any allow-rules or network fetches, and re-assert it
on any error/early exit:

```bash
# immediately after the flush block:
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT DROP
trap 'iptables -P INPUT DROP; iptables -P FORWARD DROP; iptables -P OUTPUT DROP' ERR EXIT
# ... clear the trap after the config completes successfully:
trap - ERR EXIT
```

## Honest scope / caveats

- The fail-open is specific to a run where the OUTPUT policy starts at `ACCEPT`
  — i.e. a freshly built/started container, which is the common case
  (`postStartCommand` runs the script on start). If a *prior* run already set the
  policy to `DROP`, a re-run that aborts early stays `DROP` (fail-closed but with no
  allow-rules, breaking connectivity) rather than fail-open.
- This harness proves the control-flow/ordering defect. It does not (and need not)
  reproduce real netfilter packet handling.
