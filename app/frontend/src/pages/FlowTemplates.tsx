/**
 * Flow Templates page — the pre-brand flow catalog `lib/brand_provisioning.py`
 * reads when onboarding a NEW brand (`GET/PATCH /api/v1/flow-templates`,
 * backed by the `flow_templates` table, seeded from `profiles/*.json` via
 * `scripts/backfill_flow_templates.py`).
 *
 * Editing a row here only changes what a brand provisioned AFTER the edit
 * gets — an already-provisioned brand's own `schedule_tasks` row is an
 * independent copy (see the Schedule page for editing THAT one instead).
 */

import { useState } from "react";

import Alert from "../components/ui/Alert";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import apiClient, { getErrorMessage } from "../api/client";
import { endpoints } from "../api/endpoints";
import type { FlowTemplate, FlowTemplateUpdate } from "../api/flowTemplates";
import { useApiQuery } from "../hooks/useApiQuery";

interface EditDraft {
  description: string;
  script: string;
  cron: string;
  requires_approval: boolean;
  requires_browser: boolean;
  re_run_guard: boolean;
  telegram_notify: boolean;
}

interface EditState {
  id: string;
  draft: EditDraft;
  saving: boolean;
  error: string | null;
}

function draftFrom(template: FlowTemplate): EditDraft {
  return {
    description: template.description,
    script: template.script ?? "",
    cron: template.schedule.cron ?? "",
    requires_approval: template.requires_approval,
    requires_browser: template.requires_browser,
    re_run_guard: template.re_run_guard,
    telegram_notify: template.telegram_notify,
  };
}

function diffDraft(template: FlowTemplate, draft: EditDraft): FlowTemplateUpdate {
  const update: FlowTemplateUpdate = {};
  if (draft.description !== template.description) update.description = draft.description;
  if (draft.script !== (template.script ?? "")) update.script = draft.script;
  if (draft.cron !== (template.schedule.cron ?? "")) update.cron = draft.cron;
  if (draft.requires_approval !== template.requires_approval) {
    update.requires_approval = draft.requires_approval;
  }
  if (draft.requires_browser !== template.requires_browser) {
    update.requires_browser = draft.requires_browser;
  }
  if (draft.re_run_guard !== template.re_run_guard) update.re_run_guard = draft.re_run_guard;
  if (draft.telegram_notify !== template.telegram_notify) {
    update.telegram_notify = draft.telegram_notify;
  }
  return update;
}

interface FlagProps {
  active: boolean;
  label: string;
}

function Flag({ active, label }: FlagProps): React.JSX.Element {
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold mr-1 ${
        active ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-400"
      }`}
    >
      {label}
    </span>
  );
}

interface CheckboxFieldProps {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled: boolean;
}

function CheckboxField({ label, checked, onChange, disabled }: CheckboxFieldProps): React.JSX.Element {
  return (
    <label className="flex items-center gap-1.5 text-xs text-slate-700">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}

interface TemplateRowProps {
  template: FlowTemplate;
  edit: EditState | null;
  onStartEdit: (template: FlowTemplate) => void;
  onDraftChange: (draft: EditDraft) => void;
  onSave: (template: FlowTemplate) => void;
  onCancel: () => void;
}

function TemplateRow({
  template,
  edit,
  onStartEdit,
  onDraftChange,
  onSave,
  onCancel,
}: TemplateRowProps): React.JSX.Element {
  const editing = edit !== null;

  return (
    <>
      <tr className="border-b border-slate-100 align-top">
        <td className="px-3 py-2 text-xs text-slate-500">{template.platform}</td>
        <td className="px-3 py-2 text-sm font-mono text-slate-700">{template.id}</td>
        <td className="px-3 py-2 text-xs font-mono text-slate-600">
          {editing ? (
            <input
              type="text"
              value={edit.draft.script}
              onChange={(e) => onDraftChange({ ...edit.draft, script: e.target.value })}
              disabled={edit.saving}
              className="w-40 px-2 py-1 border border-slate-300 rounded font-mono text-xs"
            />
          ) : (
            template.script ?? <span className="text-slate-400">—</span>
          )}
        </td>
        <td className="px-3 py-2 text-xs font-mono text-slate-700">
          {editing ? (
            <input
              type="text"
              value={edit.draft.cron}
              onChange={(e) => onDraftChange({ ...edit.draft, cron: e.target.value })}
              placeholder="e.g. 3 19 * * *"
              disabled={edit.saving}
              className="w-28 px-2 py-1 border border-slate-300 rounded font-mono text-xs"
            />
          ) : (
            template.schedule.cron ?? <span className="text-slate-400">—</span>
          )}
        </td>
        <td className="px-3 py-2">
          {editing ? (
            <div className="flex flex-col gap-1">
              <CheckboxField
                label="Requires approval"
                checked={edit.draft.requires_approval}
                disabled={edit.saving}
                onChange={(v) => onDraftChange({ ...edit.draft, requires_approval: v })}
              />
              <CheckboxField
                label="Requires browser"
                checked={edit.draft.requires_browser}
                disabled={edit.saving}
                onChange={(v) => onDraftChange({ ...edit.draft, requires_browser: v })}
              />
              <CheckboxField
                label="Re-run guard"
                checked={edit.draft.re_run_guard}
                disabled={edit.saving}
                onChange={(v) => onDraftChange({ ...edit.draft, re_run_guard: v })}
              />
              <CheckboxField
                label="Telegram notify"
                checked={edit.draft.telegram_notify}
                disabled={edit.saving}
                onChange={(v) => onDraftChange({ ...edit.draft, telegram_notify: v })}
              />
            </div>
          ) : (
            <div>
              <Flag active={template.requires_approval} label="Approval" />
              <Flag active={template.requires_browser} label="Browser" />
              <Flag active={template.re_run_guard} label="Re-run guard" />
              <Flag active={template.telegram_notify} label="Notify" />
            </div>
          )}
        </td>
        <td className="px-3 py-2 text-sm">
          {editing ? (
            <div className="flex flex-col gap-2">
              <textarea
                value={edit.draft.description}
                onChange={(e) => onDraftChange({ ...edit.draft, description: e.target.value })}
                disabled={edit.saving}
                rows={2}
                className="w-48 px-2 py-1 border border-slate-300 rounded text-xs"
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => onSave(template)}
                  disabled={edit.saving}
                  className="text-xs px-2 py-1 rounded bg-cyan-600 text-white hover:bg-cyan-700 disabled:bg-slate-300"
                >
                  {edit.saving ? "Saving…" : "Save"}
                </button>
                <button
                  type="button"
                  onClick={onCancel}
                  disabled={edit.saving}
                  className="text-xs px-2 py-1 rounded border border-slate-300 text-slate-700 hover:bg-slate-50"
                >
                  Cancel
                </button>
              </div>
              {edit.error && <span className="text-xs text-rose-700">{edit.error}</span>}
            </div>
          ) : (
            <div className="flex items-start justify-between gap-2">
              <span className="text-xs text-slate-600 max-w-xs">{template.description}</span>
              <button
                type="button"
                onClick={() => onStartEdit(template)}
                className="text-xs px-2 py-1 rounded border border-slate-300 text-slate-700 hover:bg-slate-50 whitespace-nowrap"
              >
                Edit
              </button>
            </div>
          )}
        </td>
      </tr>
    </>
  );
}

export default function FlowTemplates(): React.JSX.Element {
  const { data, loading, error, refetch } = useApiQuery<FlowTemplate[]>(endpoints.flowTemplates);
  const [edit, setEdit] = useState<EditState | null>(null);

  const handleStartEdit = (template: FlowTemplate): void => {
    setEdit({ id: template.id, draft: draftFrom(template), saving: false, error: null });
  };

  const handleCancel = (): void => setEdit(null);

  const handleDraftChange = (draft: EditDraft): void => {
    setEdit((prev) => (prev ? { ...prev, draft } : prev));
  };

  const handleSave = async (template: FlowTemplate): Promise<void> => {
    if (!edit) return;
    const update = diffDraft(template, edit.draft);
    setEdit((prev) => (prev ? { ...prev, saving: true, error: null } : prev));
    try {
      await apiClient.patch(endpoints.flowTemplate(template.id), update);
      setEdit(null);
      void refetch();
    } catch (err) {
      setEdit((prev) =>
        prev ? { ...prev, saving: false, error: getErrorMessage(err, "Save failed") } : prev,
      );
    }
  };

  if (loading && !data) {
    return <LoadingState message="Loading flow templates…" />;
  }

  if (error && !data) {
    return (
      <ErrorState
        title="Could not load flow templates"
        message={error}
        onRetry={() => void refetch()}
        retrying={loading}
      />
    );
  }

  const templates = data ?? [];

  return (
    <section className="space-y-6">
      <p className="text-sm text-slate-500">
        The pre-brand flow catalog — what a NEW brand's onboarding provisions
        from. Editing a row here does not touch any already-provisioned
        brand.
      </p>

      <Alert status="warning" className="mb-4">
        Changes here only apply to brands provisioned after the edit. To
        change an already-provisioned brand's schedule, use the Schedule tab
        under Operations instead.
      </Alert>

      {error && data && (
        <Alert status="warning" title="Polling error">
          {error}
        </Alert>
      )}

      {templates.length === 0 ? (
        <p className="text-sm text-slate-500">No flow templates found.</p>
      ) : (
        <div className="bg-white rounded-lg border border-slate-200 shadow-sm overflow-x-auto">
          <table className="w-full text-left">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Platform
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  ID
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Script
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Cron
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Flags
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Description
                </th>
              </tr>
            </thead>
            <tbody>
              {templates.map((template) => (
                <TemplateRow
                  key={template.id}
                  template={template}
                  edit={edit?.id === template.id ? edit : null}
                  onStartEdit={handleStartEdit}
                  onDraftChange={handleDraftChange}
                  onSave={(t) => void handleSave(t)}
                  onCancel={handleCancel}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
