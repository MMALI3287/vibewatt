import type { components } from "../api/schema";

export type NotificationAlert = components["schemas"]["AlertOut"];
export const NOTIFICATION_COOLDOWN = 6 * 60 * 60 * 1000;

export interface NotificationState {
  enabled: boolean;
  seen: string[];
  kinds: Record<string, number>;
}

export function emptyNotificationState(): NotificationState {
  return { enabled: false, seen: [], kinds: {} };
}

export function notificationStorageKey(accountId: string): string {
  return `vibewatt-notifications-v1:${accountId}`;
}

export function readNotificationState(accountId: string): NotificationState {
  const raw = localStorage.getItem(notificationStorageKey(accountId));
  if (raw === null) return emptyNotificationState();
  const value: unknown = JSON.parse(raw);
  if (typeof value !== "object" || value === null || !("enabled" in value) || typeof value.enabled !== "boolean" ||
    !("seen" in value) || !Array.isArray(value.seen) || !value.seen.every(id => typeof id === "string") ||
    !("kinds" in value) || typeof value.kinds !== "object" || value.kinds === null || Array.isArray(value.kinds) ||
    !Object.values(value.kinds).every(time => typeof time === "number" && Number.isFinite(time) && time >= 0)) {
    throw new Error("Invalid notification receipts");
  }
  return { enabled: value.enabled, seen: value.seen, kinds: value.kinds as Record<string, number> };
}

export function saveNotificationState(accountId: string, state: NotificationState): void {
  localStorage.setItem(notificationStorageKey(accountId), JSON.stringify(state));
}

export function selectNotifications(alerts: NotificationAlert[], state: NotificationState, now: number): NotificationAlert[] {
  const seen = new Set(state.seen);
  const kinds = new Set<string>();
  return alerts.filter(alert => {
    if ((alert.severity !== "warning" && alert.severity !== "serious") || seen.has(alert.id) || kinds.has(alert.kind)) return false;
    const previous = state.kinds[alert.kind];
    if (previous !== undefined && now - previous < NOTIFICATION_COOLDOWN) return false;
    seen.add(alert.id);
    kinds.add(alert.kind);
    return true;
  });
}

export function deliverNotifications(alerts: NotificationAlert[], state: NotificationState, now: number,
  deliver: (alert: NotificationAlert) => boolean): NotificationState {
  const next = { ...state, seen: [...state.seen], kinds: { ...state.kinds } };
  for (const alert of selectNotifications(alerts, state, now)) {
    if (!deliver(alert)) continue;
    next.seen.push(alert.id);
    next.kinds[alert.kind] = now;
  }
  return next;
}

export function failedNotification(state: NotificationState, alert: NotificationAlert, attemptedAt: number,
  previousKind: number | undefined): NotificationState {
  const next = { enabled: false, seen: state.seen.filter(id => id !== alert.id), kinds: { ...state.kinds } };
  if (next.kinds[alert.kind] === attemptedAt) {
    if (previousKind === undefined) delete next.kinds[alert.kind];
    else next.kinds[alert.kind] = previousKind;
  }
  return next;
}
