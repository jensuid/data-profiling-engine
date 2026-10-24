import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import DatasetSelection from "./pages/DatasetSelection";
import Placeholder from "./pages/Placeholder";
import RunConfig from "./pages/RunConfig";

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Data Profiling Engine</span>
        <nav className="nav">
          <NavLink to="/" end>Dataset</NavLink>
          <NavLink to="/run">Run</NavLink>
          <NavLink to="/report">Report</NavLink>
        </nav>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<DatasetSelection />} />
          <Route path="/run" element={<RunConfig />} />
          <Route path="/report" element={<Placeholder title="Report (M6-T4/T5/T6)" />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
