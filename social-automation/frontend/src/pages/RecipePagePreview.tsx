import { recipePageUrl } from "../api/recipes";

/**
 * Full-screen modal that previews the rendered recipe page in an <iframe>.
 *
 * The iframe loads the API's `/recipes/{id}/page` endpoint, which builds the
 * `.dff-recipe` HTML from DB fields + image artifacts — the same markup the
 * publisher emits — so the post body can be eyeballed before going live.
 */
export function PagePreviewModal({
  recipeId,
  recipeName,
  onClose,
}: {
  recipeId: string;
  recipeName: string;
  onClose: () => void;
}) {
  const url = recipePageUrl(recipeId);
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/50" aria-hidden />
      <div
        className="relative z-[61] m-4 flex h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2">
          <h3 className="flex items-center gap-2 font-semibold text-slate-800">
            <span>🔍</span> Page preview — {recipeName}
          </h3>
          <div className="flex items-center gap-3">
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-cyan-700 hover:underline"
            >
              open in tab ↗
            </a>
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-slate-600"
            >
              ✕
            </button>
          </div>
        </div>
        <iframe
          title="recipe page preview"
          src={url}
          className="w-full flex-1 bg-slate-100"
        />
      </div>
    </div>
  );
}
