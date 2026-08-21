// Add photos to the reference library: drag-and-drop or a file picker, one
// status row per file, uploaded sequentially so a mid-batch failure leaves
// the successful ones filed rather than rolling the lot back.
//
// There is no category picker here on purpose. The server runs a vision pass
// over every upload and tags it itself, so the operator's job is "drop the
// photos in", and each finished row reports what the model decided — its
// tag, whether it saw the mascot, and its one-line description. Correcting a
// wrong call is an edit in the grid below, not a decision made up front.

import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";

import { getErrorMessage } from "../../api/client";
import {
  ACCEPT_ATTR,
  preflightRejection,
  uploadReferenceImage,
} from "../../api/referenceImages";
import type { LibraryImage } from "../../api/referenceImages";
import Alert from "../ui/Alert";
import UploadQueueRow from "./UploadQueueRow";
import type { QueuedFile, RowStatus } from "./UploadQueueRow";

interface ReferenceUploadCardProps {
  disabled?: boolean;
  /** Refetch the library after anything lands. */
  onUploaded: () => void;
}

export default function ReferenceUploadCard({
  disabled = false,
  onUploaded,
}: ReferenceUploadCardProps): React.JSX.Element {
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [notice, setNotice] = useState("");
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  // Every object URL we mint, so unmount can revoke all of them. Keying the
  // revoke off `queue` instead would tear down URLs still on screen: each
  // per-file status update replaces the array and would fire the cleanup.
  const objectUrls = useRef<string[]>([]);
  // Rows dropped since the current batch started, so the upload loop's snapshot
  // can skip anything the operator has since taken out of the list.
  const removedKeys = useRef<Set<string>>(new Set());

  useEffect(() => () => objectUrls.current.forEach((u) => URL.revokeObjectURL(u)), []);

  const addFiles = useCallback((files: FileList | null) => {
    if (!files || files.length === 0) return;
    setNotice("");
    const rows: QueuedFile[] = Array.from(files).map((file, i) => {
      const previewUrl = URL.createObjectURL(file);
      objectUrls.current.push(previewUrl);
      const rejection = preflightRejection(file);
      return {
        key: `${Date.now()}-${i}-${file.name}`,
        file,
        previewUrl,
        status: rejection ? "failed" : "pending",
        message: rejection ?? "",
      };
    });
    setQueue((prev) => [...prev, ...rows]);
  }, []);

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (!disabled) addFiles(e.dataTransfer.files);
  };

  const markRow = (
    key: string,
    status: RowStatus,
    message: string,
    result?: LibraryImage,
  ) => {
    setQueue((prev) =>
      prev.map((row) => (row.key === key ? { ...row, status, message, result } : row)),
    );
  };

  const handleUpload = async () => {
    const pending = queue.filter((row) => row.status === "pending");
    if (pending.length === 0 || disabled) return;
    setBusy(true);
    setNotice("");
    removedKeys.current.clear();
    let landed = 0;
    for (const row of pending) {
      // `pending` is a snapshot, so a row the operator X-ed out after the batch
      // started is still in it. Skip it, or the file lands on the server while
      // its row is gone from the list — the same lie the X is disabled to avoid
      // on the row already in flight.
      if (removedKeys.current.has(row.key)) continue;
      markRow(row.key, "uploading", "");
      try {
        const entry = await uploadReferenceImage(row.file);
        markRow(row.key, "done", "Filed and tagged.", entry);
        landed += 1;
      } catch (err) {
        markRow(row.key, "failed", getErrorMessage(err, "Upload failed."));
      }
    }
    setBusy(false);
    if (landed > 0) onUploaded();
  };

  // The one way a row ever leaves the queue — the X, "Clear finished" and
  // "Clear all" are all just different predicates over this. Revoke as we drop
  // them; the unmount cleanup is the backstop, not the only pass, or a long
  // session's previews accumulate for nothing.
  //
  // An uploading row is never dropped, whatever the predicate says: taking it
  // out would not cancel the request in flight, so the file would still land
  // while the list pretended otherwise.
  const dropRows = (shouldDrop: (row: QueuedFile) => boolean) => {
    const dropped = queue.filter(
      (row) => row.status !== "uploading" && shouldDrop(row),
    );
    if (dropped.length === 0) return;
    dropped.forEach((row) => {
      URL.revokeObjectURL(row.previewUrl);
      removedKeys.current.add(row.key);
    });
    objectUrls.current = objectUrls.current.filter(
      (url) => !dropped.some((row) => row.previewUrl === url),
    );
    const gone = new Set(dropped.map((row) => row.key));
    setQueue((prev) => prev.filter((row) => !gone.has(row.key)));
  };

  const pendingCount = queue.filter((row) => row.status === "pending").length;
  const doneCount = queue.filter((row) => row.status === "done").length;
  // "Clear finished" only earns a slot while it would do something "Clear all"
  // wouldn't: keep the failures on screen for inspection, drop the successes.
  // Once every row is done the two are the same action, so only show one.
  const showClearFinished = doneCount > 0 && doneCount < queue.length;

  return (
    <div className="rounded-xl border border-stone-200 bg-white p-5 space-y-4">
      <div>
        <h3 className="font-display text-base font-semibold text-slate-800">
          Add reference photos
        </h3>
        <p className="text-xs text-slate-500 mt-1">
          Anything that grounds a generated image belongs here &mdash; the brand&rsquo;s
          mascot and the person behind it, but equally products, locations, settings,
          style shots. Every upload is analyzed and given a specific tag automatically,
          so you never pick a category; if a tag or a subject flag comes back wrong,
          correct it in the library below. Clear, well-lit shots of one subject work
          best. PNG, JPEG or WEBP, at least 256px per side, up to 12&nbsp;MB.
        </p>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors ${
          dragging ? "border-amber-400 bg-amber-50/60" : "border-stone-300 bg-stone-50/50"
        } ${disabled ? "opacity-50" : ""}`}
      >
        <p className="text-sm text-slate-600">Drag photos here</p>
        <p className="text-xs text-slate-400 mt-1 mb-3">or</p>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTR}
          multiple
          className="hidden"
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
          className="rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm font-semibold text-slate-700 hover:bg-stone-50 disabled:opacity-50"
        >
          Choose files
        </button>
      </div>

      {notice && <Alert status="error">{notice}</Alert>}

      {queue.length > 0 && (
        <ul className="space-y-2">
          {queue.map((row) => (
            <UploadQueueRow
              key={row.key}
              row={row}
              onRemove={(key) => dropRows((r) => r.key === key)}
            />
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={disabled || busy || pendingCount === 0}
          onClick={() => void handleUpload()}
          className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
        >
          {busy ? "Uploading…" : `Upload ${pendingCount || ""}`.trim()}
        </button>
        {showClearFinished && (
          <button
            type="button"
            onClick={() => dropRows((row) => row.status === "done")}
            title="Take the filed ones out of this list and leave the rest"
            className="text-xs text-slate-500 hover:text-slate-700"
          >
            Clear finished
          </button>
        )}
        {queue.length > 0 && (
          <button
            type="button"
            // Disabled mid-batch so the label stays honest: an uploading row
            // can't be dropped, and "Clear all" must not leave rows behind.
            disabled={busy}
            onClick={() => dropRows(() => true)}
            aria-label="Clear all photos from this upload list"
            title="Empty this upload list — photos already filed are not touched"
            className="text-xs text-slate-500 hover:text-slate-700 disabled:opacity-40 disabled:hover:text-slate-500"
          >
            Clear all
          </button>
        )}
      </div>

      {queue.length > 0 && (
        <p className="text-[11px] text-slate-400">
          Removing a photo here, or clearing the list, only empties this staging
          list &mdash; nothing that has already been filed leaves the library below.
        </p>
      )}
    </div>
  );
}
