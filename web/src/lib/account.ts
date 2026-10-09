import { useSyncExternalStore } from "react";

const KEY = "vibewatt-account";
type AccountPreference = { account: string | null; persisted: boolean };

function readPreference(): AccountPreference {
  try {
    return { account: localStorage.getItem(KEY), persisted: true };
  } catch {
    return { account: null, persisted: false };
  }
}

let preference = readPreference();
const listeners = new Set<() => void>();

export function getAccountPreference(): AccountPreference { return preference; }

export function selectAccount(account: string | null): void {
  let persisted = true;
  try {
    if (account === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, account);
  } catch {
    persisted = false;
  }
  preference = { account, persisted };
  listeners.forEach(listener => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useAccountPreference(): AccountPreference {
  return useSyncExternalStore(subscribe, getAccountPreference);
}
