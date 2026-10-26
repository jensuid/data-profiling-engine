/**
 * M7-T5c — bridge readiness in the packaged shell.
 *
 * pywebview injects ``window.pywebview`` AFTER the host page has loaded and only
 * then dispatches ``pywebviewready`` (``webview/js/finish.js``). The packaged
 * bundle mounts React faster than that injection lands, so a one-shot
 * :func:`bridgeAvailable` snapshot latched the "Desktop bridge not detected"
 * banner on forever in the ``.app`` — every read command stayed disabled even
 * though the bridge arrived milliseconds later. Dev mode hid the race because
 * the Vite module graph loads slower than the injection.
 *
 * This hook owns the readiness signal for the UI: it seeds from the current
 * state (the injection may already have happened before first render), then
 * listens for ``pywebviewready`` with a bounded poll as a fallback, so the
 * banner clears itself instead of requiring a second navigation.
 */
import { useEffect, useState } from "react";
import { bridgeAvailable } from "./api";

/** ``pywebviewready`` is host-dispatched and not part of lib.dom's map. */
declare global {
  interface WindowEventMap {
    pywebviewready: Event;
  }
}

/** Cheap re-check cadence; the injection normally lands well inside one tick. */
const POLL_INTERVAL_MS = 100;
/** Give up polling after this long — outside the shell the bridge never comes. */
const POLL_TIMEOUT_MS = 10_000;

/**
 * ``true`` once ``window.pywebview.api.bridge_send`` is callable. Starts
 * ``false`` in a plain browser and stays that way (dev fallback keeps working).
 */
export function useBridgeReady(): boolean {
  const [ready, setReady] = useState<boolean>(() => bridgeAvailable());

  useEffect(() => {
    // Already injected before the first render — nothing to wait for.
    if (bridgeAvailable()) {
      setReady(true);
      return;
    }

    const check = () => {
      if (!bridgeAvailable()) return;
      setReady(true);
      window.removeEventListener("pywebviewready", check);
    };

    window.addEventListener("pywebviewready", check);
    const started = Date.now();
    const timer = window.setInterval(() => {
      check();
      if (bridgeAvailable() || Date.now() - started > POLL_TIMEOUT_MS) {
        window.clearInterval(timer);
      }
    }, POLL_INTERVAL_MS);

    return () => {
      window.removeEventListener("pywebviewready", check);
      window.clearInterval(timer);
    };
  }, []);

  return ready;
}
