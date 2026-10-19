import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useQuery } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccountScope } from "./AccountScope";
import { selectAccount } from "../lib/account";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let root: Root;
let element: HTMLDivElement;
const load = vi.fn<() => Promise<string>>();

function Probe() {
  const query = useQuery({ queryKey: ["account-data"], queryFn: load });
  return <p>{query.data ?? "Loading"}</p>;
}

beforeEach(() => {
  localStorage.clear(); selectAccount(null); load.mockReset();
  element = document.createElement("div"); document.body.append(element);
  root = createRoot(element);
});
afterEach(async () => {
  await act(async () => root.unmount());
  element.remove(); vi.restoreAllMocks();
});
async function settle() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
async function mount() {
  await act(async () => root.render(<AccountScope><Probe /></AccountScope>));
  await settle();
}

it("removes the previous account's cached response before loading the new account", async () => {
  let resolveNext!: (value: string) => void;
  load.mockResolvedValueOnce("Account A").mockImplementationOnce(() => new Promise(resolve => { resolveNext = resolve; }));
  await mount();
  expect(element.textContent).toBe("Account A");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  await act(async () => selectAccount("B"));
  expect(element.textContent).toBe("Loading");
  await act(async () => resolveNext("Account B")); await settle();
  expect(element.textContent).toBe("Account B");
});

it("ignores a previous account's request that completes after switching", async () => {
  let resolvePrevious!: (value: string) => void;
  load.mockImplementationOnce(() => new Promise(resolve => { resolvePrevious = resolve; })).mockResolvedValueOnce("Account B");
  await mount();
  await act(async () => selectAccount("B")); await settle();
  expect(element.textContent).toBe("Account B");
  await act(async () => resolvePrevious("Account A")); await settle();
  expect(element.textContent).toBe("Account B");
});
