// One file waiting in (or moving through) the upload queue: thumbnail, name,
// what happened to it, and an X to take it back out of the list.
//
// The X is deliberately dead while the row is uploading. Dropping the row
// would not cancel the request already in flight, so the file would still
// land on the server while the UI claimed it was gone — a lie the operator
// only discovers when the library refetches.

import type { LibraryImage } from "../../api/referenceImages";
import { subjectsShown } from "./subjects";

export type RowStatus = "pending" | "uploading" | "done" | "failed";

export interface QueuedFile {
  key: string;
  file: File;
  previewUrl: string;
  status: RowStatus;
  message: string;
  /** The filed entry, once the server answers — what the model decided. */
  result?: LibraryImage;
}

/** Per-status tone, plus fixed text for the states that carry no message. */
const STATUS_ROW: Record<RowStatus, { cls: string; text: string }> = {
  pending: { cls: "text-slate-500", text: "Ready to upload" },
  uploading: { cls: "text-amber-700", text: "Analyzing and filing…" },
  done: { cls: "text-emerald-700", text: "" },
  failed: { cls: "text-rose-700", text: "" },
};

/**
 * The tagging result, spelled out — this is the operator's proof it ran.
 *
 * Both subject flags are reported, not just the mascot: a photo of the person
 * behind the brand is exactly as load-bearing, and "neither" is a real and
 * common answer (a trail, a porch, a shelf of products) that has to read as a
 * decision rather than as a blank.
 */
function AnalysisSummary({ image }: { image: LibraryImage }): React.JSX.Element {
  const anySubject = image.shows_mascot || image.shows_persona;
  return (
    <>
      <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs">
        <span className="rounded bg-stone-100 px-1.5 py-0.5 font-semibold text-slate-700">
          {image.category}
        </span>
        <span
          className={
            anySubject
              ? "rounded bg-emerald-100 px-1.5 py-0.5 font-semibold text-emerald-800"
              : "rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-500"
          }
        >
          {anySubject ? `shows ${subjectsShown(image)}` : "no persona or mascot"}
        </span>
      </p>
      {image.description && (
        <p className="mt-0.5 text-xs text-slate-500">{image.description}</p>
      )}
    </>
  );
}

interface UploadQueueRowProps {
  row: QueuedFile;
  /** Drop this row from the staging list — never touches the filed library. */
  onRemove: (key: string) => void;
}

export default function UploadQueueRow({
  row,
  onRemove,
}: UploadQueueRowProps): React.JSX.Element {
  const uploading = row.status === "uploading";

  return (
    <li className="flex items-start gap-3 rounded-lg border border-stone-200 p-2">
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
      <button
        type="button"
        disabled={uploading}
        onClick={() => onRemove(row.key)}
        aria-label={`Remove ${row.file.name} from the upload list`}
        title={
          uploading
            ? "This one is already uploading — it can't be taken back now."
            : `Remove ${row.file.name} from the upload list`
        }
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded text-base leading-none text-slate-400 hover:bg-stone-100 hover:text-slate-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-500 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-slate-400"
      >
        <span aria-hidden="true">&times;</span>
      </button>
    </li>
  );
}
