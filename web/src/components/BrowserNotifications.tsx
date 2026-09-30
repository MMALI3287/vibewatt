import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getAlerts } from "../api/client";
import { deliverNotifications, emptyNotificationState, failedNotification, notificationStorageKey, readNotificationState, saveNotificationState } from "../lib/notifications";
import "./BrowserNotifications.css";

export function BrowserNotifications({ accountId }: { accountId?: string | null }) {
  const supported = typeof Notification !== "undefined";
  const [loadedAccount, setLoadedAccount] = useState<string | null>(null);
  const [state, setState] = useState(emptyNotificationState);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState(() => document.visibilityState === "visible" && navigator.onLine);

  useEffect(() => {
    const update = () => setActive(document.visibilityState === "visible" && navigator.onLine);
    document.addEventListener("visibilitychange", update);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      document.removeEventListener("visibilitychange", update);
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  useEffect(() => {
    function load() {
      setStatus("");
      setLoadedAccount(accountId ?? null);
      try { setState(accountId ? readNotificationState(accountId) : emptyNotificationState()); }
      catch { setState(emptyNotificationState()); setStatus("Notifications off: browser storage unavailable."); }
    }
    load();
    const changed = (event: StorageEvent) => { if (accountId && (event.key === null || event.key === notificationStorageKey(accountId))) load(); };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, [accountId]);

  const enabled = supported && Notification.permission === "granted" && state.enabled && loadedAccount === accountId;
  const query = useQuery({ queryKey: ["alerts"], queryFn: getAlerts, enabled: enabled && active,
    staleTime: 60_000, refetchOnWindowFocus: false, refetchOnReconnect: false });

  useEffect(() => {
    if (!enabled || !active || !accountId || !query.data || query.isFetching) return;
    const alerts = query.data.alerts;
    let cancelled = false;
    const send = () => {
      if (cancelled || document.visibilityState !== "visible" || !navigator.onLine || Notification.permission !== "granted") return;
      try {
        const current = readNotificationState(accountId);
        if (!current.enabled) return;
        // Verify writable storage before a toast so receipt failures do not create a repeat loop.
        saveNotificationState(accountId, current);
        let failed = false;
        const attemptedAt = Date.now();
        const next = deliverNotifications(alerts, current, attemptedAt, alert => {
          try {
            const notification = new Notification(`vibewatt: ${alert.title}`, { body: alert.detail, tag: `${accountId}:${alert.id}` });
            notification.onerror = () => {
              const undo = () => {
                try {
                  const restored = failedNotification(readNotificationState(accountId), alert, attemptedAt, current.kinds[alert.kind]);
                  saveNotificationState(accountId, restored);
                  setState(restored);
                } catch { setState(emptyNotificationState()); }
                setStatus("Notifications off: browser delivery unavailable.");
              };
              if (navigator.locks) void navigator.locks.request(notificationStorageKey(accountId), undo).catch(undo);
              else undo();
            };
            return true;
          }
          catch { failed = true; setStatus("Notifications off: browser delivery unavailable."); return false; }
        });
        if (failed) { next.enabled = false; setState(next); }
        saveNotificationState(accountId, next);
      } catch { setState(emptyNotificationState()); setStatus("Notifications off: browser storage unavailable."); }
    };
    // Tabs share receipts. The lock prevents two visible tabs racing to show the same alert.
    if (navigator.locks) void navigator.locks.request(notificationStorageKey(accountId), send).catch(() => {
      setState(emptyNotificationState()); setStatus("Notifications off: browser delivery unavailable.");
    });
    else send();
    return () => { cancelled = true; };
  }, [enabled, active, accountId, query.data, query.isFetching]);

  async function toggle() {
    if (!accountId) return;
    setStatus("");
    setBusy(true);
    try {
      const current = readNotificationState(accountId);
      if (current.enabled && Notification.permission === "granted") {
        const next = { ...current, enabled: false };
        saveNotificationState(accountId, next); setState(next); return;
      }
      // Permission is requested only by this explicit button click.
      const permission = await Notification.requestPermission();
      if (permission !== "granted") { setStatus("Notifications blocked. Allow them in browser settings to enable."); return; }
      const next = { ...readNotificationState(accountId), enabled: true };
      saveNotificationState(accountId, next); setState(next);
    } catch { setState(emptyNotificationState()); setStatus("Notifications off: browser permission or storage unavailable."); }
    finally { setBusy(false); }
  }

  return <span className="browser-notifications">
    <button type="button" aria-label={`Browser notifications: ${enabled ? "on" : "off"}`} aria-pressed={enabled}
      title="Warning alerts while this dashboard tab is visible and online. At most one per kind every six hours."
      disabled={!supported || !accountId || busy} onClick={() => void toggle()}>
      {busy ? "Requesting…" : !supported ? "Notifications unavailable" : `Notifications: ${enabled ? "on" : "off"}`}
    </button>
    {status && <span role="status">{status}</span>}
  </span>;
}
