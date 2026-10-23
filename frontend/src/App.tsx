import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import Placeholder from "./pages/Placeholder";

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Data Profiling Engine</span>
        <nav className="nav">
          <NavLink to="/" end>Home</NavLink>
          <NavLink to="/run">Run</NavLink>
          <NavLink to="/report">Report</NavLink>
        </nav>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/run" element={<Placeholder title="Run (M5/M6)" />} />
          <Route path="/report" element={<Placeholder title="Report (M6)" />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
