import { useCallback, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "ccburn-theme";

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function useTheme(): [ThemeChoice, () => void] {
  const [choice, setChoice] = useState<ThemeChoice>(read);
  const cycle = useCallback(() => {
    setChoice((prev) => {
      const next: ThemeChoice = prev === "system" ? "light" : prev === "light" ? "dark" : "system";
      const root = document.documentElement;
      if (next === "system") delete root.dataset.theme;
      else root.dataset.theme = next;
      try {
        if (next === "system") localStorage.removeItem(KEY);
        else localStorage.setItem(KEY, next);
      } catch {
        // Private windows can throw; the in-memory choice still applies.
      }
      return next;
    });
  }, []);
  return [choice, cycle];
}
