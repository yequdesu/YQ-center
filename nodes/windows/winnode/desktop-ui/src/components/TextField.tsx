import React from "react";

interface TextFieldProps {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  error?: string;
  type?: string;
  disabled?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

export function TextField({
  label,
  value,
  onChange,
  placeholder,
  error,
  type = "text",
  disabled,
  className = "",
  style,
}: TextFieldProps) {
  return (
    <div className={`flex flex-col gap-1.5 ${className}`} style={style}>
      {label && <label className="text-xs font-medium text-[var(--text-muted)]">{label}</label>}
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="w-full px-3 py-2 text-sm rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] placeholder:text-[var(--text-subtle)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      />
      {error && <span className="text-xs text-[var(--danger)]">{error}</span>}
    </div>
  );
}

export function PasswordField(props: TextFieldProps) {
  const [show, setShow] = React.useState(false);
  return (
    <div className={`flex flex-col gap-1.5 ${props.className || ""}`}>
      {props.label && <label className="text-xs font-medium text-[var(--text-muted)]">{props.label}</label>}
      <div className="relative">
        <input
          type={show ? "text" : "password"}
          value={props.value}
          onChange={(e) => props.onChange(e.target.value)}
          placeholder={props.placeholder}
          disabled={props.disabled}
          className="w-full px-3 py-2 pr-10 text-sm rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] placeholder:text-[var(--text-subtle)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)] disabled:opacity-50 transition-colors"
        />
        <button
          type="button"
          onClick={() => setShow(!show)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-[var(--text-muted)] hover:text-[var(--text)] px-1"
        >
          {show ? "Hide" : "Show"}
        </button>
      </div>
      {props.error && <span className="text-xs text-[var(--danger)]">{props.error}</span>}
    </div>
  );
}
