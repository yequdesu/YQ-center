import { useMemo } from "react";

const MAX_DEPTH = 5;
const MAX_ARRAY_ITEMS = 20;
const MAX_OBJECT_KEYS = 60;
const MAX_STRING_CHARS = 1800;
const MAX_RENDER_CHARS = 12000;

export function JsonView({ data }: { data: unknown }) {
  const preview = useMemo(() => jsonPreview(data), [data]);
  return (
    <div className="overflow-hidden rounded-[var(--radius-sm)] bg-slate-950/90">
      <pre className="max-h-[260px] overflow-auto p-3 text-[11px] leading-5 text-slate-100">
        {preview.text}
      </pre>
      {preview.truncated && (
        <div className="border-t border-slate-700/70 px-3 py-2 text-[11px] text-slate-300">
          Preview truncated for UI safety. Use a focused tool, artifact, or context ref to inspect
          the full value.
        </div>
      )}
    </div>
  );
}

function jsonPreview(data: unknown): { text: string; truncated: boolean } {
  const state = { truncated: false };
  const compact = compactValue(data, 0, state);
  let text = JSON.stringify(compact, null, 2);
  if (text.length > MAX_RENDER_CHARS) {
    state.truncated = true;
    text = `${text.slice(0, MAX_RENDER_CHARS)}\n... [truncated by console preview]`;
  }
  return { text, truncated: state.truncated };
}

function compactValue(
  value: unknown,
  depth: number,
  state: { truncated: boolean },
): unknown {
  if (value === null || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    if (value.length <= MAX_STRING_CHARS) return value;
    state.truncated = true;
    return {
      $type: "string",
      chars: value.length,
      preview: value.slice(0, MAX_STRING_CHARS),
      omitted_chars: value.length - MAX_STRING_CHARS,
    };
  }
  if (Array.isArray(value)) {
    if (depth >= MAX_DEPTH) {
      state.truncated = true;
      return { $type: "array", length: value.length };
    }
    const items = value
      .slice(0, MAX_ARRAY_ITEMS)
      .map((item) => compactValue(item, depth + 1, state));
    if (value.length > MAX_ARRAY_ITEMS) {
      state.truncated = true;
      items.push({ $omitted_items: value.length - MAX_ARRAY_ITEMS });
    }
    return items;
  }
  if (typeof value === "object") {
    if (depth >= MAX_DEPTH) {
      state.truncated = true;
      return { $type: "object", keys: Object.keys(value as Record<string, unknown>).length };
    }
    const source = value as Record<string, unknown>;
    const entries = Object.entries(source);
    const compact: Record<string, unknown> = {};
    for (const [key, item] of entries.slice(0, MAX_OBJECT_KEYS)) {
      compact[key] = compactValue(item, depth + 1, state);
    }
    if (entries.length > MAX_OBJECT_KEYS) {
      state.truncated = true;
      compact.$omitted_keys = entries.length - MAX_OBJECT_KEYS;
    }
    return compact;
  }
  return String(value);
}
