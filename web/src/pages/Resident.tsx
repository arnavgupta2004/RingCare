import { useEffect, useRef, useState } from "react";
import { api, formatTime, isEstimate, newestFirst, openPackage, useDoorState } from "../api";

/**
 * Resident view: large text, high contrast, screen-reader first.
 * - New door updates are announced once through a polite live region (not on every poll).
 * - The "I picked up the package" button only appears when a package is waiting.
 */
export default function Resident() {
  const { state, error, refresh } = useDoorState();
  const [announcement, setAnnouncement] = useState("");
  const [busy, setBusy] = useState(false);
  const lastSeenId = useRef<string | null>(null);
  const pkgHeading = useRef<HTMLHeadingElement>(null);

  const tz = state?.clock.tz ?? "UTC";
  const updates = newestFirst((state?.notifications ?? []).filter((n) => n.audience === "resident"));
  const waiting = openPackage(state?.packages ?? []);
  // While a package is waiting, show the picture of that package; otherwise the newest picture.
  const withPictures = newestFirst((state?.events ?? []).filter((e) => e.snapshot_url));
  const latest = (waiting && withPictures.find((e) => e.event_id === waiting.arrived_event_id)) || withPictures[0];

  useEffect(() => {
    document.title = "Your front door — DoorSight";
  }, []);

  // Announce a new update once; skip the first load so the page doesn't read out history.
  useEffect(() => {
    const newest = updates[0];
    if (!newest) return;
    if (lastSeenId.current !== null && lastSeenId.current !== newest.id) {
      setAnnouncement(`New update: ${newest.text}`);
    }
    lastSeenId.current = newest.id;
  }, [updates]);

  async function pickedUp() {
    if (!waiting) return;
    setBusy(true);
    try {
      await api.pickedUp(waiting.id);
      setAnnouncement("Thank you. The package is marked as picked up.");
      await refresh();
      // The button disappears; move focus to the package section so keyboard users aren't lost.
      pkgHeading.current?.focus();
    } catch {
      setAnnouncement("Sorry, that did not work. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="resident">
      <header className="r-header">
        <h1>Your front door</h1>
        {state && (
          <p className="r-clock">
            It is now <time dateTime={state.clock.sim_now}>{formatTime(state.clock.sim_now, tz)}</time>
          </p>
        )}
      </header>

      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>

      <main>
        {error && (
          <p className="r-error" role="alert">
            Cannot reach DoorSight right now. Trying again.
          </p>
        )}

        <section aria-labelledby="pkg-heading" className="r-card">
          <h2 id="pkg-heading" ref={pkgHeading} tabIndex={-1}>Package</h2>
          {waiting ? (
            <>
              <p className="r-big">
                A package is waiting at your door. It arrived at{" "}
                <time dateTime={waiting.arrived_sim_ts}>{formatTime(waiting.arrived_sim_ts, tz)}</time>.
              </p>
              <button type="button" className="r-button" onClick={pickedUp} disabled={busy}>
                {busy ? "Saving…" : "I picked up the package"}
              </button>
            </>
          ) : (
            <p className="r-big">No package is waiting at your door.</p>
          )}
        </section>

        <section aria-labelledby="pic-heading" className="r-card">
          <h2 id="pic-heading">{waiting && latest?.event_id === waiting.arrived_event_id ? "Your package" : "Latest picture"}</h2>
          {latest ? (
            <figure className="r-figure">
              <img
                src={latest.snapshot_url!}
                alt={latest.accessible_description ?? "Picture from your front door camera."}
              />
              <figcaption>
                Taken at <time dateTime={latest.sim_ts}>{formatTime(latest.sim_ts, tz)}</time>.{" "}
                {latest.accessible_description}
                {latest.description_source === "stub" && <span className="r-estimate"> (automatic estimate)</span>}
              </figcaption>
            </figure>
          ) : (
            <p className="r-big">No pictures yet.</p>
          )}
        </section>

        <section aria-labelledby="updates-heading" className="r-card">
          <h2 id="updates-heading">Door updates</h2>
          {updates.length ? (
            <ol className="r-updates">
              {updates.map((n) => (
                <li key={n.id}>
                  <time dateTime={n.sim_ts}>{formatTime(n.sim_ts, tz)}</time>
                  <p>
                    {n.text}
                    {isEstimate(n) && <span className="r-estimate"> (automatic estimate)</span>}
                  </p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="r-big">Nothing new at your door.</p>
          )}
        </section>
      </main>
    </div>
  );
}
