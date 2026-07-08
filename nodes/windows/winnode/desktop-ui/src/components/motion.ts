import { motion, type Variants } from "framer-motion";

export const spring = {
  type: "spring",
  stiffness: 420,
  damping: 34,
  mass: 0.8,
} as const;

export const softSpring = {
  type: "spring",
  stiffness: 260,
  damping: 28,
  mass: 0.9,
} as const;

export const pageTransition: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -4 },
};
