const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("en-US");

// A priced cost under a cent is not $0.00: that reads as free or unpriced (A-079).
export const fmtUsd = (v: number) => (v > 0 && v < 0.01 ? "<$0.01" : usd.format(v));
export const fmtCompact = (v: number) => compact.format(v);
export const fmtInt = (v: number) => whole.format(v);
export const fmtPct = (v: number) => `${Math.round(v * 100)}%`;

import type { Bucket } from "../api/client";

// Mirrors Bucket.total_tokens in vibewatt/aggregate.py; thinking is already inside output.
export function bucketTokens(b: Bucket): number {
  return b.input + b.cache_write_5m + b.cache_write_1h + b.cache_read + b.output;
}
