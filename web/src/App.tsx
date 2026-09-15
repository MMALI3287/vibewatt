import { Route, Routes } from "react-router-dom";
import { FilterBar } from "./components/FilterBar";
import { Footer } from "./components/Footer";
import { Header } from "./components/Header";
import { Overview } from "./pages/Overview";
import { Placeholder } from "./pages/Placeholder";

export function App() {
  return (
    <div className="app">
      <Header />
      <FilterBar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/sessions" element={<Placeholder title="Sessions" />} />
          <Route path="/projects" element={<Placeholder title="Projects" />} />
          <Route path="/analysis" element={<Placeholder title="Analysis" />} />
          <Route path="/wrapped" element={<Placeholder title="Wrapped" />} />
          <Route path="*" element={<Placeholder title="Not found" />} />
        </Routes>
      </main>
      <Footer />
    </div>
  );
}
