# RuView — fix the broken Python Quick Start (import facade)

## The defect
The README documents this entry point:

```python
from wifi_densepose import WiFiDensePose
system = WiFiDensePose()
system.start()
```

As shipped, `WiFiDensePose().start()` always fails with `ModuleNotFoundError:
No module named 'src'` (re-raised as a misleading "Core dependencies not
found" `ImportError`).

## Root cause
`wifi_densepose/__init__.py` adds the v1 source tree to `sys.path` so the
facade can `from src.config.settings import get_settings` /
`from src.services.orchestrator import ServiceOrchestrator`. But it points at
`<repo>/v1`, which does not exist — the v1 tree (which still contains the
`src` package) was relocated to `<repo>/archive/v1/`:

```python
# before (broken):
_v1_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "v1")
if os.path.isdir(_v1_src) and _v1_src not in sys.path:
    sys.path.insert(0, _v1_src)
```

`<repo>/v1` isn't a directory, so nothing is added to `sys.path` and the
`src` package is never importable.

## The fix
Probe `archive/v1` first, keep the legacy `v1` as a fallback. See
`fix-v1-src-path.patch`:

```python
_repo_root = os.path.dirname(os.path.dirname(__file__))
for _v1_src in (
    os.path.join(_repo_root, "archive", "v1"),
    os.path.join(_repo_root, "v1"),
):
    if os.path.isdir(_v1_src) and _v1_src not in sys.path:
        sys.path.insert(0, _v1_src)
        break
```

## Verification (reproduced + fixed locally)
- Before: `WiFiDensePose().start()` → `ModuleNotFoundError: No module named 'src'`.
- After the patch, with only the light config deps installed
  (`pip install pydantic pydantic-settings`):
  - `archive/v1` is on `sys.path`, and the exact import that used to fail now
    succeeds: `from src.config.settings import get_settings` → `Settings`.
  - `WiFiDensePose().start()` no longer fails on `'src'`; it now fails on
    `No module named 'psutil'` — i.e. it is past the packaging bug and into
    the normal runtime-dependency install step.

## Honest scope
This patch fixes the *packaging/path defect* that makes the documented Quick
Start unusable. Actually running the full v1 server still requires the heavy
runtime stack (`pip install -r requirements.txt` → torch, opencv, fastapi,
sqlalchemy, etc.) plus infrastructure (Postgres/Redis) and, for live sensing,
ESP32 hardware. Those are environmental/hardware requirements, not code bugs,
and are out of scope for this fix. The Rust engine (`v2/`) and ESP32 firmware
are separate, working code paths and are unaffected.

## Apply
```bash
cd RuView
git apply path/to/fix-v1-src-path.patch
```
