import { useLiveRefresh } from "./lib/liveRefresh";
import { lazy, Suspense, useEffect } from "react";
import { Route, Routes, useLocation, type Location } from "react-router";
import { FilterBar } from "./components/FilterBar";
import { Footer } from "./components/Footer";
import { Header } from "./components/Header";
import { Placeholder } from "./pages/Placeholder";
import { Phase6Panels } from "./components/Phase6Panels";
import { preloadable } from "./lib/preloadable";

const Overview = preloadable(() => import("./pages/Overview").then(m => m.Overview));
const Sessions = preloadable(() => import("./pages/Sessions").then(m => m.Sessions));
const SessionModal = lazy(() => import("./pages/Sessions").then(m => ({ default: m.SessionModal })));
const Breakdown = preloadable(() => import("./pages/Breakdown").then(m => m.Breakdown));
const Analysis = preloadable(() => import("./pages/Analysis").then(m => m.Analysis));
const FindingModal = lazy(() => import("./pages/Analysis").then(m => ({ default: m.FindingModal })));
const Wrapped = preloadable(() => import("./pages/Wrapped").then(m => m.Wrapped));
const ROUTES = [Overview, Sessions, Breakdown, Analysis, Wrapped];

/** Warm every route chunk once the first view is idle, so later tab switches never show the fallback. */
function usePreloadRoutes() {
  useEffect(() => {
    const warm = () => ROUTES.forEach(route => void route.preload());
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(warm);
      return () => window.cancelIdleCallback(id);
    }
    const id = window.setTimeout(warm, 1000);
    return () => window.clearTimeout(id);
  }, []);
}

export function App() {
  useLiveRefresh();
  usePreloadRoutes();
  const location = useLocation();
  const state = location.state as { backgroundLocation?: Location } | null;
  const modal = location.pathname.startsWith("/sessions/") || location.pathname.startsWith("/analysis/findings/");
  const background = modal ? state?.backgroundLocation : undefined;
  return (
    <div className="app">
      {/* Skips the header and the six filter controls on every page (A-040). */}
      <a className="skip-link" href="#main">Skip to content</a>
      <Header />
      <FilterBar />
      <main className="main" id="main" tabIndex={-1}>
        <Suspense fallback={<p role="status">Loading view…</p>}>
        <Routes location={background ?? location}>
          <Route path="/" element={<Overview />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:id" element={<Sessions />} />
          <Route path="/projects" element={<Breakdown dimension="project" />} />
          <Route path="/models" element={<Breakdown dimension="model" />} />
          <Route path="/analysis" element={<Analysis />} />
          <Route path="/analysis/findings/:id" element={<Analysis />} />
          <Route path="/wrapped" element={<Wrapped />} />
          <Route path="*" element={<Placeholder title="Not found" />} />
        </Routes>
        </Suspense>
        {/* Its own boundary: loading the modal chunk must not unmount the page behind it. */}
        <Suspense fallback={null}>
        <Routes>
          <Route path="/sessions/:id" element={<SessionModal hasBackground={Boolean(background)} />} />
          <Route path="/analysis/findings/:id" element={<FindingModal hasBackground={Boolean(background)} />} />
          <Route path="*" element={null} />
        </Routes>
        </Suspense>
        <Phase6Panels />
      </main>
      <Footer />
    </div>
  );
}
