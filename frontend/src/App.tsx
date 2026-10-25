import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import DatasetSelection from "./pages/DatasetSelection";
import Report from "./pages/Report";
import RunConfig from "./pages/RunConfig";
import RunProgress from "./pages/RunProgress";

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Data Profiling Engine</span>
        <nav className="nav">
          <NavLink to="/" end>Dataset</NavLink>
          <NavLink to="/run">Run</NavLink>
          <NavLink to="/progress">Progress</NavLink>
          <NavLink to="/report">Report</NavLink>
        </nav>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<DatasetSelection />} />
          <Route path="/run" element={<RunConfig />} />
          <Route path="/progress" element={<RunProgress />} />
          <Route path="/report" element={<Report />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
