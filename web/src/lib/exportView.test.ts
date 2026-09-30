import { afterEach, describe, expect, it, vi } from "vitest";
import { createExportView, exportDashboardPng } from "./exportView";

vi.mock("html-to-image", () => ({ toCanvas: vi.fn() }));

afterEach(() => {
  document.body.replaceChildren();
  window.history.replaceState({}, "", "/");
});

function dashboard() {
  document.body.innerHTML = `<div class="app"><header>Export controls</header>
    <form class="filters"><label>Project<select name="project"><option>demo</option><option selected>cloud-project</option></select></label></form>
    <main id="main" class="main"><h1>Sessions</h1><input value="demo"><a href="/projects?project=demo" title="cloud-project">demo and cloud-project</a>
    <table><tr><th><button>Project</button></th></tr><tr><td>demo</td></tr></table><button>Reload</button></main>
    <footer class="footer">Local logs · cloud-reported cost</footer></div>`;
  return document.querySelector<HTMLElement>(".app")!;
}

describe("dashboard PNG export", () => {
  it("masks local and cloud project names in text, active inputs and attributes without changing the live view", () => {
    const app = dashboard();
    const capture = createExportView(app, { projects: ["demo", "cloud-project"], includeProjectNames: false });
    expect(capture.element.outerHTML).not.toContain("demo");
    expect(capture.element.outerHTML).not.toContain("cloud-project");
    expect(capture.element.textContent).toContain("Project 1");
    expect(capture.element.textContent).toContain("Project 2");
    expect(capture.element.textContent).toContain("Local logs · cloud-reported cost");
    expect(capture.element.querySelector("button, input, select")).toBeNull();
    expect(app.textContent).toContain("cloud-project");
    capture.cleanup();
  });

  it("keeps project names only when explicitly selected", () => {
    const capture = createExportView(dashboard(), { projects: undefined, includeProjectNames: true });
    expect(capture.element.textContent).toContain("cloud-project");
    capture.cleanup();
  });

  it("records the selected account and active header search with default masking", () => {
    const app = dashboard();
    window.history.replaceState({}, "", "/sessions?q=demo");
    app.querySelector("header")!.innerHTML = '<form class="header-search"><input type="search" value="draft"></form><select aria-label="Account"><option value="unknown" selected>Unknown account</option></select>';
    const capture = createExportView(app, { projects: ["demo", "cloud-project"], includeProjectNames: false });
    expect(capture.element.textContent).toContain("Account: Unknown account");
    expect(capture.element.textContent).toContain("Session search: Project 2");
    expect(capture.element.outerHTML).not.toContain("demo");
    expect(capture.element.textContent).not.toContain("draft");
    capture.cleanup();
  });

  it("fails closed when project facets are unavailable", () => {
    expect(() => createExportView(dashboard(), { projects: undefined, includeProjectNames: false })).toThrow(/project names could not be loaded/i);
    expect(document.querySelector(".dashboard-export-host")).toBeNull();
  });

  it("preserves theme styling on SVG descendants that the renderer clones without CSS", () => {
    const app = dashboard();
    app.querySelector("main")!.insertAdjacentHTML("beforeend", '<style>.export-test-bar { fill: rgb(42, 120, 214); stroke: rgb(228, 227, 222); }</style><svg><rect class="export-test-bar" /></svg>');
    const capture = createExportView(app, { projects: [], includeProjectNames: false });
    expect(capture.element.querySelector<SVGElement>("rect")!.style.fill).toBe("rgb(42, 120, 214)");
    capture.cleanup();
  });

  it("rejects huge captures before allocating a canvas and removes the clone", async () => {
    const app = dashboard();
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(40_000);
    await expect(exportDashboardPng(app, { projects: [], includeProjectNames: false })).rejects.toThrow(/too large/i);
    expect(document.querySelector(".dashboard-export-host")).toBeNull();
    vi.restoreAllMocks();
  });
});
