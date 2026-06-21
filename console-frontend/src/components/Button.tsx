import type { ButtonHTMLAttributes, ReactNode } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost" | "danger";
  size?: "sm" | "md";
  children: ReactNode;
}

export function Button({ variant = "ghost", size = "md", className = "", children, ...props }: ButtonProps) {
  const variantClass = {
    primary: "bg-[var(--accent)] text-white shadow-sm hover:brightness-95",
    ghost: "border border-[var(--border)] bg-white/70 text-[var(--text)] hover:bg-white",
    danger: "bg-[var(--danger-muted)] text-[var(--danger)] hover:brightness-95",
  }[variant];
  const sizeClass = size === "sm" ? "px-2.5 py-1.5 text-[12px]" : "px-3 py-2 text-[14px]";

  return (
    <button
      className={`inline-flex items-center justify-center gap-1.5 rounded-[var(--radius-sm)] font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${variantClass} ${sizeClass} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
