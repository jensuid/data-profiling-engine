import { useLocation } from "react-router-dom";

export default function Home() {
  const location = useLocation();
  const { hash } = window.location;
  return (
    <section>
      <h1>M0-T1 desktop spike</h1>
      <p>
        If you can read this, <code>pywebview</code> loaded the React build from
        local files over <code>file://</code> — no HTTP/TCP server involved
        (ADR-005), and SPA routing works via <code>HashRouter</code> (ADR-013).
      </p>
      <ul>
        <li>
          Current location: <code>{location.pathname}</code>
        </li>
        <li>
          Raw hash: <code>{hash || "#/"}</code>
        </li>
      </ul>
      <p>Next spike steps: M0-T2 bridge echo · M0-T3 worker job.</p>
    </section>
  );
}
