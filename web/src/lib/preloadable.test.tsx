import { act, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { preloadable } from "./preloadable";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function Page({ label }: { label: string }) {
  return <p>{label}</p>;
}

describe("preloadable routes", () => {
  it("renders a preloaded route without showing the Suspense fallback", async () => {
    const Route = preloadable(() => Promise.resolve(Page));
    await Route.preload();
    const host = document.createElement("div");
    const root = createRoot(host);
    act(() => root.render(<Suspense fallback={<p>Loading view…</p>}><Route label="Sessions" /></Suspense>));
    expect(host.textContent).toBe("Sessions");
    act(() => root.unmount());
  });

  it("still suspends until a route that was never preloaded arrives", async () => {
    let resolve: (component: typeof Page) => void = () => undefined;
    const Route = preloadable(() => new Promise<typeof Page>(done => { resolve = done; }));
    const host = document.createElement("div");
    const root = createRoot(host);
    act(() => root.render(<Suspense fallback={<p>Loading view…</p>}><Route label="Wrapped" /></Suspense>));
    expect(host.textContent).toBe("Loading view…");
    await act(async () => resolve(Page));
    expect(host.textContent).toBe("Wrapped");
    act(() => root.unmount());
  });
});
