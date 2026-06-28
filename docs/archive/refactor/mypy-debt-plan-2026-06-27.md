# Mypy Debt Plan

Date: 2026-06-27

Baseline command:

```bash
mypy src/ > docs/mypy-baseline-2026-06-27.txt
```

Baseline result:

```text
Found 248 errors in 27 files (checked 77 source files)
```

## Error Classes

1. Generic container annotations
   - Missing type arguments for `dict`, `list`, and `tuple`.
   - Mostly low-risk when fields are plain JSON-like payloads.
2. CLI annotations
   - Untyped Click command functions.
   - `_print_json` is untyped, causing `no-untyped-call` in typed command functions.
3. SQLAlchemy typing
   - `Result[Any]` does not expose `rowcount`.
   - `scalars().all()` is inferred as `Sequence[T]` where local APIs expect `list[T]`.
4. Nullable datetime handling
   - Comparisons on values inferred as `datetime | None`.
5. Agent/provider dynamic payloads
   - Raw LLM JSON is currently typed as `object` for too long.
   - Provider result DTOs need clearer conversion boundaries.
6. Route DTO boundaries
   - Pydantic route models and response dicts need explicit JSON object aliases.
7. Third-party typing
   - `jsonschema` stubs are missing.
   - Python 3.11 `tomllib` fallback currently triggers `tomli` import/no-redef issues.

## Execution Order

1. Fix low-risk generic aliases and simple helper annotations.
2. Fix SQLAlchemy result typing and nullable datetime guards.
3. Fix Agent/provider raw payload parsing boundaries.
4. Fix Maintenance dynamic summary/artifact typing.
5. Re-run full verification after each batch.
