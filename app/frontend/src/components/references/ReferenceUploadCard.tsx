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

type RowStatus = "pending" | "uploading" | "done" | "failed";

interface QueuedFile {
  key: string;
  file: File;
  previewUrl: string;
  status: RowStatus;
  message: string;
  /** The filed entry, once the server answers — what the model decided. */
  result?: LibraryImage;
}

interface ReferenceUploadCardProps {
  disabled?: boolean;
  /** Refetch the library after anything lands. */
  onUploaded: () => void;
}

/** Per-status tone, plus fixed text for the states that carry no message. */
const STATUS_ROW: Record<RowStatus, { cls: string; text: string }> = {
  pending: { cls: "text-slate-500", text: "Ready to upload" },
  uploading: { cls: "text-amber-700", text: "Analyzing and filing…" },
  done: { cls: "text-emerald-700", text: "" },
  failed: { cls: "text-rose-700", text: "" },
};

/** The tagging result, spelled out — this is the operator's proof it ran. */
function AnalysisSummary({ image }: { image: LibraryImage }): React.JSX.Element {
  return (
    <>
      <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs">
        <span className="rounded bg-stone-100 px-1.5 py-0.5 font-semibold text-slate-700">
          {image.category}
        </span>
        <span
          className={
            image.shows_mascot
              ? "rounded bg-emerald-100 px-1.5 py-0.5 font-semibold text-emerald-800"
              : "rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-500"
          }
        >
          {image.shows_mascot ? "shows the mascot" : "no mascot"}
        </span>
      </p>
      {image.description && (
        <p className="mt-0.5 text-xs text-slate-500">{image.description}</p>
      )}
    </>
  );
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
    let landed = 0;
    for (const row of pending) {
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

  const clearFinished = () => {
    // Revoke as we drop them; the unmount cleanup is the backstop, not the
    // only pass, or a long session's previews accumulate for nothing.
    const done = queue.filter((row) => row.status === "done");
    done.forEach((row) => URL.revokeObjectURL(row.previewUrl));
    objectUrls.current = objectUrls.current.filter(
      (url) => !done.some((row) => row.previewUrl === url),
    );
    setQueue((prev) => prev.filter((row) => row.status !== "done"));
  };

  const pendingCount = queue.filter((row) => row.status === "pending").length;

  return (
    <div className="rounded-xl border border-stone-200 bg-white p-5 space-y-4">
      <div>
        <h3 className="font-display text-base font-semibold text-slate-800">
          Add reference photos
        </h3>
        <p className="text-xs text-slate-500 mt-1">
          Anything that grounds a generated image belongs here &mdash; the brand&rsquo;s
          mascot, but equally products, locations, people, settings, style shots.
          Every upload is analyzed and tagged automatically, so you never pick a
          category; if a tag or a mascot flag comes back wrong, correct it in the
          library below. Clear, well-lit shots of one subject work best. PNG, JPEG or
          WEBP, at least 256px per side, up to 12&nbsp;MB.
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
            <li
              key={row.key}
              className="flex items-start gap-3 rounded-lg border border-stone-200 p-2"
            >
              <img
                src={row.previewUrl}
                alt={row.file.name}
                className="h-12 w-12 shrink-0 rounded object-cover border border-stone-200"
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-slate-700">{row.file.name}</p>
                <p className={`text-xs ${STATUS_ROW[row.status].cls}`}>
                  {STATUS_ROW[row.status].text || row.message}
                </p>
                {row.result && <AnalysisSummary image={row.result} />}
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={disabled || busy || pendingCount === 0}
          onClick={() => void handleUpload()}
          className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
        >
          {busy ? "Uploading…" : `Upload ${pendingCount || ""}`.trim()}
        </button>
        {queue.some((row) => row.status === "done") && (
          <button
            type="button"
            onClick={clearFinished}
            className="text-xs text-slate-500 hover:text-slate-700"
          >
            Clear finished
          </button>
        )}
      </div>
    </div>
  );
}
