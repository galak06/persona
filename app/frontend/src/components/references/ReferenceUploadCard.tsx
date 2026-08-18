// Add photos to the reference library: drag-and-drop or a file picker, one
// status row per file, uploaded sequentially so a mid-batch failure leaves
// the successful ones filed rather than rolling the lot back.

import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";

import { getErrorMessage } from "../../api/client";
import {
  ACCEPT_ATTR,
  createReferenceCategory,
  preflightRejection,
  suggestReferenceCategory,
  uploadReferenceImage,
} from "../../api/referenceImages";
import type { CategorySummary } from "../../api/referenceImages";
import Alert from "../ui/Alert";

type RowStatus = "pending" | "uploading" | "done" | "failed";

interface QueuedFile {
  key: string;
  file: File;
  previewUrl: string;
  status: RowStatus;
  message: string;
}

interface ReferenceUploadCardProps {
  categories: CategorySummary[];
  disabled?: boolean;
  /** Refetch the library after anything lands. */
  onUploaded: () => void;
}

/** Per-status tone, plus fixed text for the states that carry no message. */
const STATUS_ROW: Record<RowStatus, { cls: string; text: string }> = {
  pending: { cls: "text-slate-500", text: "Ready to upload" },
  uploading: { cls: "text-amber-700", text: "Uploading…" },
  done: { cls: "text-emerald-700", text: "" },
  failed: { cls: "text-rose-700", text: "" },
};

export default function ReferenceUploadCard({
  categories,
  disabled = false,
  onUploaded,
}: ReferenceUploadCardProps): React.JSX.Element {
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [category, setCategory] = useState("general");
  const [newCategory, setNewCategory] = useState("");
  const [notice, setNotice] = useState("");
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  // Every object URL we mint, so unmount can revoke all of them. Keying the
  // revoke off `queue` instead would tear down URLs still on screen: each
  // per-file status update replaces the array and would fire the cleanup.
  const objectUrls = useRef<string[]>([]);
  // Set once the operator picks a category by hand — after that, a late
  // suggestion must not yank the selection out from under them.
  const userChoseCategory = useRef(false);

  useEffect(() => () => objectUrls.current.forEach((u) => URL.revokeObjectURL(u)), []);

  const prefillCategory = useCallback(async (file: File) => {
    try {
      const suggested = (await suggestReferenceCategory(file))?.toLowerCase();
      if (!suggested || userChoseCategory.current) return;
      const match = categories.find(
        (c) => c.label.toLowerCase() === suggested || c.slug.toLowerCase() === suggested,
      );
      if (match) setCategory(match.slug);
    } catch {
      // Advisory only — a failed suggestion is a silent no-op, never a
      // reason to hold up an upload the operator already asked for.
    }
  }, [categories]);

  const addFiles = useCallback(
    (files: FileList | null) => {
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
      const first = rows.find((r) => r.status === "pending");
      if (first) void prefillCategory(first.file);
    },
    [prefillCategory],
  );

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (!disabled) addFiles(e.dataTransfer.files);
  };

  const markRow = (key: string, status: RowStatus, message: string) => {
    setQueue((prev) =>
      prev.map((row) => (row.key === key ? { ...row, status, message } : row)),
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
        await uploadReferenceImage(row.file, category);
        markRow(row.key, "done", "Added to the library.");
        landed += 1;
      } catch (err) {
        markRow(row.key, "failed", getErrorMessage(err, "Upload failed."));
      }
    }
    setBusy(false);
    if (landed > 0) onUploaded();
  };

  const handleCreateCategory = async () => {
    const label = newCategory.trim();
    if (!label || disabled) return;
    setNotice("");
    try {
      const created = await createReferenceCategory(label);
      setNewCategory("");
      userChoseCategory.current = true;
      setCategory(created.slug);
      onUploaded();
    } catch (err) {
      setNotice(getErrorMessage(err, "Could not create that category."));
    }
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
  const knownCategory = categories.some((c) => c.slug === category);

  return (
    <div className="rounded-xl border border-stone-200 bg-white p-5 space-y-4">
      <div>
        <h3 className="font-display text-base font-semibold text-slate-800">
          Add reference photos
        </h3>
        <p className="text-xs text-slate-500 mt-1">
          These ground every generated image, so quality here decides quality there: clear,
          well-lit shots of one subject &mdash; the mascot, a product, an ingredient, a place &mdash;
          from varied angles. PNG, JPEG or WEBP, at least 256px per side, up to 12&nbsp;MB.
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

      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="block mb-1 font-medium text-slate-700">Category</span>
          <select
            value={category}
            disabled={disabled}
            onChange={(e) => {
              userChoseCategory.current = true;
              setCategory(e.target.value);
            }}
            className="rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50 disabled:bg-stone-50"
          >
            {!knownCategory && <option value={category}>{category}</option>}
            {categories.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.label} ({c.count})
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="block mb-1 font-medium text-slate-700">New category</span>
          <input
            type="text"
            value={newCategory}
            disabled={disabled}
            placeholder="e.g. close-up"
            onChange={(e) => setNewCategory(e.target.value)}
            className="rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50 disabled:bg-stone-50"
          />
        </label>
        <button
          type="button"
          disabled={disabled || !newCategory.trim()}
          onClick={() => void handleCreateCategory()}
          className="rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-stone-50 disabled:opacity-50"
        >
          Add category
        </button>
      </div>

      {notice && <Alert status="error">{notice}</Alert>}

      {queue.length > 0 && (
        <ul className="space-y-2">
          {queue.map((row) => (
            <li
              key={row.key}
              className="flex items-center gap-3 rounded-lg border border-stone-200 p-2"
            >
              <img
                src={row.previewUrl}
                alt={row.file.name}
                className="h-12 w-12 rounded object-cover border border-stone-200"
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-slate-700">{row.file.name}</p>
                <p className={`text-xs ${STATUS_ROW[row.status].cls}`}>
                  {STATUS_ROW[row.status].text || row.message}
                </p>
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
