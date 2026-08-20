/**
 * Per-post hook-image regenerations on the Social Posts page.
 *
 * A retry runs on the worker for about a minute, so the page has to hold
 * in-flight state for an arbitrary number of posts at once, poll each one, and
 * say what happened when it lands. That is a lot of `Set`/`Record` bookkeeping
 * for a page that also owns compose, approve and reject, so it lives here.
 *
 * The server is the source of truth for "is this one still running", not this
 * hook: `retry-image/status` reads the post's own row (`'composing'` while
 * claimed), so a reload, a second tab, or a run started by someone else all
 * resolve correctly. `adopt` is how the page hands those back in after a list
 * refetch.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getErrorMessage, isHttpStatus } from "../api/client";
import {
  fetchRetryImageStatus,
  retrySocialPostImage,
} from "../api/socialPosts";

const POLL_MS = 5000;

export interface ImageRetries {
  isRetrying: (id: string) => boolean;
  noteFor: (id: string) => string | null;
  /** Cache-buster for the image, bumped when a regeneration lands. */
  versionFor: (id: string) => number | undefined;
  start: (id: string, referenceCategory: string) => Promise<void>;
  /** Resume tracking runs already in flight server-side (reload, other tab). */
  adopt: (ids: string[]) => void;
}

export function useImageRetries(onSettled: () => void): ImageRetries {
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [versions, setVersions] = useState<Record<string, number>>({});
  const timers = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());
  // Kept in a ref so `poll` never closes over a stale callback identity.
  const settled = useRef(onSettled);
  settled.current = onSettled;

  const stop = useCallback((id: string): void => {
    const timer = timers.current.get(id);
    if (timer) clearInterval(timer);
    timers.current.delete(id);
    setRunning((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  // Clear every interval on unmount.
  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach((timer) => clearInterval(timer));
      map.clear();
    };
  }, []);

  const poll = useCallback(
    (id: string): void => {
      if (timers.current.has(id)) return;
      setRunning((prev) => new Set(prev).add(id));
      timers.current.set(
        id,
        setInterval(() => {
          void fetchRetryImageStatus(id).then((s) => {
            if (s.running) return;
            stop(id);
            // `source` is the outcome that matters: the run either left a
            // generated image on the post or it did not. `ok` only reports
            // this brand's last run, which may have been another post's.
            const won = s.source === "gemini";
            if (won) setVersions((prev) => ({ ...prev, [id]: Date.now() }));
            setNotes((prev) => ({
              ...prev,
              [id]: won
                ? "New image generated."
                : `Could not regenerate — the post is unchanged. ${s.detail ?? ""}`.trim(),
            }));
            settled.current();
          });
        }, POLL_MS),
      );
    },
    [stop],
  );

  const start = useCallback(
    async (id: string, referenceCategory: string): Promise<void> => {
      setNotes((prev) => ({ ...prev, [id]: "" }));
      try {
        await retrySocialPostImage(id, referenceCategory);
        poll(id);
      } catch (err) {
        if (isHttpStatus(err, 409)) {
          poll(id); // already running — track it rather than complaining
          return;
        }
        setNotes((prev) => ({
          ...prev,
          [id]: getErrorMessage(err, "Could not start the retry."),
        }));
      }
    },
    [poll],
  );

  const adopt = useCallback(
    (ids: string[]): void => {
      ids.forEach((id) => poll(id));
    },
    [poll],
  );

  return {
    isRetrying: (id) => running.has(id),
    noteFor: (id) => notes[id] || null,
    versionFor: (id) => versions[id],
    start,
    adopt,
  };
}
