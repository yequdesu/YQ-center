import { useState, useEffect, createContext, useContext, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface ToastItem {
  id: string;
  message: string;
  variant: "success" | "error" | "info";
}

interface ToastContextType {
  toast: (message: string, variant?: "success" | "error" | "info") => void;
  addToast?: (message: string, variant?: "success" | "error" | "info") => void;
  show?: (message: string, variant?: "success" | "error" | "info") => void;
  success?: (message: string) => void;
  error?: (message: string) => void;
  info?: (message: string) => void;
}

const ToastContext = createContext<ToastContextType>({
  toast: () => {},
  addToast: () => {},
  show: () => {},
  success: () => {},
  error: () => {},
  info: () => {},
});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const addToast = useCallback((message: string, variant: "success" | "error" | "info" = "info") => {
    const id = Date.now().toString();
    setToasts((prev) => [...prev, { id, message, variant }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3000);
  }, []);

  return (
    <ToastContext.Provider value={{
      toast: addToast,
      addToast,
      show: addToast,
      success: (msg) => addToast(msg, "success"),
      error: (msg) => addToast(msg, "error"),
      info: (msg) => addToast(msg, "info"),
    }}>
      {children}
      <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2">
        <AnimatePresence>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 16, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.95 }}
              transition={{ type: "spring", stiffness: 500, damping: 30 }}
              className={`px-4 py-2.5 rounded-sm text-sm font-medium shadow-md ${
                t.variant === "success"
                  ? "bg-[var(--success-bg)] text-[var(--success)] border border-[var(--success)]"
                  : t.variant === "error"
                  ? "bg-[var(--danger-bg)] text-[var(--danger)] border border-[var(--danger)]"
                  : "bg-[var(--info-bg)] text-[var(--info)] border border-[var(--info)]"
              }`}
            >
              {t.message}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}
