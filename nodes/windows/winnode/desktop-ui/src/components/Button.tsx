import React from "react";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  loading?: boolean;
  icon?: React.ReactNode;
}

export function Button({
  variant = "secondary",
  loading,
  icon,
  children,
  className = "",
  disabled,
  ...props
}: ButtonProps) {
  const base =
    "inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-sm transition-all duration-150 select-none disabled:opacity-40 disabled:cursor-not-allowed";
  const variants: Record<string, string> = {
    primary:
      "bg-[var(--accent)] text-white hover:bg-[var(--accent-strong)] active:scale-[0.98]",
    secondary:
      "bg-[var(--surface-solid)] text-[var(--text)] border border-[var(--border)] hover:bg-[var(--bg-subtle)] active:scale-[0.98]",
    danger:
      "bg-[var(--danger-bg)] text-[var(--danger)] hover:bg-[var(--danger)] hover:text-white active:scale-[0.98]",
    ghost:
      "text-[var(--text-muted)] hover:bg-[var(--bg-subtle)] hover:text-[var(--text)]",
  };

  return (
    <button
      className={`${base} ${variants[variant]} ${className}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? (
        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      ) : icon ? (
        <span className="w-4 h-4">{icon}</span>
      ) : null}
      {children}
    </button>
  );
}
