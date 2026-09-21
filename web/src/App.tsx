import { Route, Routes, useLocation, type Location } from "react-router-dom";
import { FilterBar } from "./components/FilterBar";
import { Footer } from "./components/Footer";
import { Header } from "./components/Header";
import { Overview } from "./pages/Overview";
import { Placeholder } from "./pages/Placeholder";
import { Sessions, SessionModal } from "./pages/Sessions";
import { Breakdown } from "./pages/Breakdown";
import { Analysis } from "./pages/Analysis";
import { Wrapped } from "./pages/Wrapped";
import { Phase6Panels } from "./components/Phase6Panels";

export function App() {
  const location = useLocation();
  const state = location.state as { backgroundLocation?: Location } | null;
  const background = location.pathname.startsWith("/sessions/") ? state?.backgroundLocation : undefined;
  return (
    <div className="app">
      <Header />
      <FilterBar />
      <main className="main">
        <Routes location={background ?? location}>
          <Route path="/" element={<Overview />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:id" element={<Sessions />} />
          <Route path="/projects" element={<Breakdown dimension="project" />} />
          <Route path="/models" element={<Breakdown dimension="model" />} />
          <Route path="/analysis" element={<Analysis />} />
          <Route path="/wrapped" element={<Wrapped />} />
          <Route path="*" element={<Placeholder title="Not found" />} />
        </Routes>
        <Routes>
          <Route path="/sessions/:id" element={<SessionModal hasBackground={Boolean(background)} />} />
          <Route path="*" element={null} />
        </Routes>
        <Phase6Panels />
      </main>
      <Footer />
    </div>
  );
}
