import { lazy, Suspense } from "react";
import { Route, Routes, useLocation, type Location } from "react-router-dom";
import { FilterBar } from "./components/FilterBar";
import { Footer } from "./components/Footer";
import { Header } from "./components/Header";
import { Placeholder } from "./pages/Placeholder";
import { Phase6Panels } from "./components/Phase6Panels";

const Overview = lazy(() => import("./pages/Overview").then(m => ({ default: m.Overview })));
const Sessions = lazy(() => import("./pages/Sessions").then(m => ({ default: m.Sessions })));
const SessionModal = lazy(() => import("./pages/Sessions").then(m => ({ default: m.SessionModal })));
const Breakdown = lazy(() => import("./pages/Breakdown").then(m => ({ default: m.Breakdown })));
const Analysis = lazy(() => import("./pages/Analysis").then(m => ({ default: m.Analysis })));
const FindingModal = lazy(() => import("./pages/Analysis").then(m => ({ default: m.FindingModal })));
const Wrapped = lazy(() => import("./pages/Wrapped").then(m => ({ default: m.Wrapped })));

export function App() {
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
