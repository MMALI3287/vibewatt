import { useEffect } from "react";

/** Each view names itself in the tab and history, not just "vibewatt" (A-085). */
export function useTitle(view: string) {
  useEffect(() => {
    document.title = `${view} · vibewatt`;
  }, [view]);
}
