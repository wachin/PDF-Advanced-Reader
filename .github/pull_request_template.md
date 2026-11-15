## What changed

<!-- One or two sentences. What behaviour is different, and why? -->

## Why

<!-- Link the issue/phase if there is one. Explain the cause, not just the symptom. -->

## How it was verified

<!-- Quote the exact commands and results. For editing/rendering work, describe
     the real UI path (open -> edit -> save -> reopen), not only unit tests. -->

```bash
PYTHONPATH=src python3 -m pytest tests/ -q
python3 -m ruff check src/ tests/
```

## Checklist

- [ ] The change is focused; no unrelated refactors or reformatting.
- [ ] A test fails without this change and passes with it.
- [ ] Document changes go through an `Edit` with the narrowest `Scope`.
- [ ] No `fitz.Document` is shared across threads; no Qt object is touched
      from a worker.
- [ ] Page handles are re-opened after a mutation.
- [ ] `demo.pdf`, `external/**` and tracked fixtures are untouched.
- [ ] New fixtures are generated or license-clean (attribution in `CREDITS.md`).
- [ ] `docs/developers/status.md` updated if the phase state changed.
- [ ] Nothing was pushed to a shared branch or force-pushed.
