import { act, Profiler } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Phase6Panels } from "./Phase6Panels";
import { AccountScope } from "./AccountScope";
import { selectAccount } from "../lib/account";
import { useFilters } from "../lib/filters";
import { getConcierge } from "../api/client";

vi.mock("../api/client", () => ({
  getConcierge: vi.fn(), getServiceStatus: async () => null,
  getHealth: async () => ({}), getAlerts: async () => ({ alerts: [], notes: [] }),
  generateWeeklySummary: vi.fn(),
}));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let root: Root;
let element: HTMLDivElement;
let renderedText: string[];
const clipboard = vi.fn<(text: string) => Promise<void>>();
function Projects() {
  const [, update] = useFilters();
  return <>{["A", "B", ""].map(project => <button key={project} onClick={() => update({ project: project || null })}>Select {project || "none"}</button>)}<Phase6Panels /></>;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const brief = (text: string) => ({ project: text, text, notes: [] });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function click(label: string) {
  const button = [...element.querySelectorAll("button")].find(button => button.textContent === label);
  expect(button, label).toBeDefined();
  await act(async () => button!.click()); await settle();
}
const text = () => element.querySelector("textarea")?.value;
beforeEach(async () => {
  renderedText = [];
  localStorage.clear(); selectAccount(null); vi.mocked(getConcierge).mockReset(); clipboard.mockReset().mockResolvedValue();
  vi.stubGlobal("navigator", { clipboard: { writeText: clipboard } });
  vi.mocked(getConcierge).mockImplementation(async project => brief(project));
  element = document.createElement("div"); document.body.append(element); root = createRoot(element);
  await act(async () => root.render(<Profiler id="brief" onRender={() => renderedText.push(element.textContent ?? "")}><MemoryRouter initialEntries={["/?project=A"]}><AccountScope><Projects /></AccountScope></MemoryRouter></Profiler>));
  await settle();
});
afterEach(async () => { await act(async () => root.unmount()); element.remove(); vi.unstubAllGlobals(); });

it("resets copy feedback when switching to and returning from a cached project", async () => {
  expect(getConcierge).not.toHaveBeenCalled();
  await click("Prepare brief for A"); await click("Copy brief");
  expect(clipboard).toHaveBeenCalledWith("A");
  await click("Select B"); expect(text()).toBeUndefined();
  await click("Prepare brief for B");
  expect(element.textContent).not.toContain("Copied");
  await click("Copy brief"); await click("Select A");
  expect(text()).toBe("A"); expect(element.textContent).not.toContain("Copied");
});
it("hides old text and clears manual-copy feedback while preparing an identical replacement", async () => {
  await click("Prepare brief for A"); clipboard.mockRejectedValueOnce(new Error("denied")); await click("Copy brief");
  expect(element.textContent).toContain("Select the brief text to copy it.");
  const next = deferred<Awaited<ReturnType<typeof getConcierge>>>();
  vi.mocked(getConcierge).mockReturnValueOnce(next.promise);
  await click("Prepare brief for A"); expect(text()).toBeUndefined();
  expect(element.textContent).not.toContain("Select the brief text to copy it.");
  await act(async () => next.resolve(brief("A"))); await settle();
  expect(text()).toBe("A"); expect(element.textContent).not.toContain("Select the brief text to copy it.");
});
for (const outcome of ["resolve", "reject"] as const) {
  it(`ignores delayed clipboard ${outcome} after leaving and returning to a project`, async () => {
    await click("Prepare brief for A"); const pending = deferred<void>(); clipboard.mockReturnValueOnce(pending.promise);
    await click("Copy brief"); await click("Select B"); await click("Select A");
    await act(async () => { if (outcome === "resolve") pending.resolve(); else pending.reject(new Error("denied")); });
    expect(text()).toBe("A"); expect(element.textContent).not.toContain("Copied");
    expect(element.textContent).not.toContain("Select the brief text to copy it.");
  });
}
it("keeps account reset and manual text selection available", async () => {
  await click("Prepare brief for A"); await click("Copy brief");
  await act(async () => selectAccount("other")); await settle(); expect(text()).toBeUndefined();
  await click("Prepare brief for A"); clipboard.mockRejectedValueOnce(new Error("denied")); await click("Copy brief");
  const textarea = element.querySelector("textarea")!; textarea.focus(); textarea.select();
  expect(textarea.readOnly).toBe(true); expect(textarea.selectionEnd).toBe(1);
  expect(element.textContent).toContain("Select the brief text to copy it.");
});
it("ignores a pending copy when new text replaces the brief", async () => {
  await click("Prepare brief for A"); const pending = deferred<void>(); clipboard.mockReturnValueOnce(pending.promise);
  await click("Copy brief"); vi.mocked(getConcierge).mockResolvedValueOnce(brief("replacement"));
  await click("Prepare brief for A"); expect(text()).toBe("replacement");
  await act(async () => pending.resolve()); expect(element.textContent).not.toContain("Copied");
  await click("Copy brief"); expect(clipboard).toHaveBeenLastCalledWith("replacement");
});
it("ignores a pending copy after an account switch", async () => {
  await click("Prepare brief for A"); const pending = deferred<void>(); clipboard.mockReturnValueOnce(pending.promise);
  await click("Copy brief"); await act(async () => selectAccount("other")); await settle();
  await click("Prepare brief for A"); await act(async () => pending.reject(new Error("denied")));
  expect(element.textContent).not.toContain("Select the brief text to copy it.");
});
it("clears feedback after deselection and after a failed copy following success", async () => {
  await click("Prepare brief for A"); await click("Copy brief");
  clipboard.mockRejectedValueOnce(new Error("denied")); await click("Copied");
  expect(element.textContent).not.toContain("Copied");
  await click("Select none"); expect(text()).toBeUndefined(); await click("Select A");
  expect(element.textContent).not.toContain("Select the brief text to copy it.");
});
it("does not present old text after replacement preparation fails", async () => {
  await click("Prepare brief for A"); await click("Copy brief");
  vi.mocked(getConcierge).mockRejectedValue(new Error("unavailable"));
  await click("Prepare brief for A");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1200)); });
  expect(element.textContent).toContain("Could not prepare brief: unavailable");
  expect(text()).toBeUndefined(); expect(element.textContent).not.toContain("Copied");
  vi.mocked(getConcierge).mockResolvedValue(brief("recovered")); await click("Prepare brief for A");
  expect(text()).toBe("recovered"); expect(element.textContent).not.toContain("Copied");
});

it("clears feedback when identical preparation completes without a loading render", async () => {
  await click("Prepare brief for A"); await click("Copy brief");
  expect(element.textContent).toContain("Copied");
  const prepare = [...element.querySelectorAll("button")].find(button => button.textContent === "Prepare brief for A")!;
  renderedText = [];
  // Keep the immediate response and query notification in one React batch.
  await act(async () => { prepare.click(); await new Promise(resolve => setTimeout(resolve, 20)); });
  expect(renderedText.length).toBeGreaterThan(0);
  expect(renderedText.some(text => text.includes("Preparing…"))).toBe(false);
  expect(text()).toBe("A");
  expect(element.textContent).not.toContain("Copied");
  expect(element.textContent).not.toContain("Select the brief text to copy it.");
  await click("Copy brief"); expect(clipboard).toHaveBeenLastCalledWith("A");
});
