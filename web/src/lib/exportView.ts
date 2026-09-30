import { toCanvas } from "html-to-image";

export interface ExportOptions {
  projects: string[] | undefined;
  includeProjectNames: boolean;
}

const MAX_DIMENSION = 16_384;
const MAX_PIXELS = 32_000_000;

function maskProjects(element: HTMLElement, projects: string[]) {
  const names = [...new Set(projects.filter(Boolean))].sort();
  const aliases = new Map(names.map((name, index) => [name, `Project ${index + 1}`]));
  const pattern = names.sort((a, b) => b.length - a.length)
    .map(name => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  if (!pattern) return;
  const expression = new RegExp(pattern, "g");
  const mask = (value: string) => value.replace(expression, name => aliases.get(name)!);
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) walker.currentNode.textContent = mask(walker.currentNode.textContent ?? "");
  for (const node of [element, ...element.querySelectorAll("*")]) {
    for (const attribute of [...node.attributes]) {
      if (attribute.name === "title" || attribute.name.startsWith("aria-") || attribute.name === "alt" || attribute.name === "value") {
        node.setAttribute(attribute.name, mask(attribute.value));
      }
      // Navigation and React/chart metadata do not belong in a static export.
      if (attribute.name === "href" && !(node instanceof SVGElement) || attribute.name.startsWith("data-")) node.removeAttribute(attribute.name);
    }
  }
}

export function createExportView(app: HTMLElement, options: ExportOptions) {
  if (!options.includeProjectNames && !options.projects) {
    throw new Error("Project names could not be loaded. Retry before exporting with names masked.");
  }
  const host = document.createElement("div");
  host.className = "dashboard-export-host";
  host.setAttribute("aria-hidden", "true");
  host.inert = true;
  const element = document.createElement("div");
  element.className = "app dashboard-export";
  element.style.width = `${Math.ceil(app.getBoundingClientRect().width || window.innerWidth)}px`;
  const heading = document.createElement("header");
  heading.className = "dashboard-export-heading";
  heading.textContent = document.title || "vibewatt";
  const account = app.querySelector<HTMLSelectElement>('select[aria-label="Account"]')?.selectedOptions[0]?.textContent;
  const search = new URLSearchParams(window.location.search).get("q");
  const scope = [...(account ? [`Account: ${account}`] : []), ...(search ? [`Session search: ${search}`] : [])];
  if (scope.length) {
    const caption = document.createElement("div");
    caption.className = "muted";
    caption.style.fontSize = "12px";
    caption.textContent = scope.join(" · ");
    heading.append(caption);
  }
  element.append(heading);
  for (const source of app.querySelectorAll<HTMLElement>(":scope > .filters, :scope > main, :scope > footer")) {
    const clone = source.cloneNode(true) as HTMLElement;
    clone.querySelectorAll("details").forEach(details => { details.open = true; });
    const originalSvg = source.querySelectorAll<SVGElement>("svg *");
    // html-to-image deep-clones SVG roots without styling their descendants.
    clone.querySelectorAll<SVGElement>("svg *").forEach((node, index) => {
      const style = getComputedStyle(originalSvg[index]);
      for (const property of ["fill", "fill-opacity", "stroke", "stroke-width", "stroke-opacity", "color", "font-family", "font-size", "font-weight", "text-anchor", "dominant-baseline"]) {
        node.style.setProperty(property, style.getPropertyValue(property));
      }
    });
    const originals = source.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input, select, textarea");
    clone.querySelectorAll("input, select, textarea").forEach((control, index) => {
      const original = originals[index];
      const value = document.createElement("span");
      value.className = "dashboard-export-value";
      value.textContent = original instanceof HTMLSelectElement
        ? original.selectedOptions[0]?.textContent ?? "All"
        : original instanceof HTMLInputElement && original.type === "checkbox"
          ? original.checked ? "On" : "Off" : original.value || "All";
      control.replaceWith(value);
    });
    clone.querySelectorAll("button").forEach(button => {
      if (button.closest("th")) {
        const label = document.createElement("span");
        label.textContent = button.textContent;
        button.replaceWith(label);
      } else button.remove();
    });
    clone.querySelectorAll("[data-export-exclude]").forEach(control => control.remove());
    element.append(clone);
  }
  if (!element.querySelector("main") || !element.querySelector("footer")) {
    throw new Error("The dashboard view and provenance footer must be ready before exporting.");
  }
  if (!options.includeProjectNames) {
    const projectChoices = [...app.querySelectorAll<HTMLOptionElement>('select[name="project"] option')]
      .map(option => option.value).filter(Boolean);
    maskProjects(element, [...options.projects!, ...projectChoices]);
  }
  host.append(element);
  document.body.append(host);
  return { element, cleanup: () => host.remove() };
}

export async function exportDashboardPng(app: HTMLElement, options: ExportOptions): Promise<Blob> {
  await document.fonts?.ready;
  const capture = createExportView(app, options);
  try {
    // Widen only when a table or calendar needs it; overflow must never disappear into the PNG edge.
    capture.element.style.width = `${Math.ceil(Math.max(capture.element.scrollWidth, capture.element.getBoundingClientRect().width))}px`;
    const width = Math.ceil(capture.element.scrollWidth || capture.element.getBoundingClientRect().width);
    const height = Math.ceil(Math.max(capture.element.scrollHeight, capture.element.getBoundingClientRect().height));
    if (width > MAX_DIMENSION || height > MAX_DIMENSION || width * height > MAX_PIXELS) {
      throw new Error("This view is too large for a PNG. Narrow the date range or filters and try again.");
    }
    const canvas = await toCanvas(capture.element, {
      width, height, pixelRatio: 1, skipAutoScale: true, skipFonts: true,
      backgroundColor: getComputedStyle(capture.element).backgroundColor,
    });
    const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("The browser could not create a PNG. Narrow the filters and try again.");
    return blob;
  } finally {
    capture.cleanup();
  }
}
