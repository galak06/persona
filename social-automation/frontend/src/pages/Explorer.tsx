import { useMemo, useState } from "react";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type { components, ScheduleEntry } from "../types/openapi";
import LoadingState from "../components/ui/LoadingState";
import Alert from "../components/ui/Alert";

type FlowsStateResponse = components["schemas"]["FlowsStateResponse"];

interface ArtifactViewerProps {
  label: string;
}

function ArtifactViewer({ label }: ArtifactViewerProps): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const { data, loading, error, refetch } = useApiQuery<{ data: unknown }>(
    endpoints.scheduleArtifact(label),
    { enabled: open }
  );

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="text-xs px-2 py-1 rounded border border-cyan-300 text-cyan-800 hover:bg-cyan-100"
      >
        {open ? "Hide Content" : "View Content"}
      </button>

      {open && (
        <div className="relative">
          <button
            onClick={() => void refetch()}
            className="absolute top-2 right-2 text-[10px] bg-white/80 px-1.5 py-0.5 rounded border border-slate-200 hover:bg-white"
          >
            Refresh
          </button>
          {loading ? (
            <div className="p-4 text-center text-xs text-slate-400">Loading artifact...</div>
          ) : error ? (
            <div className="p-4 text-xs text-rose-600 font-mono bg-rose-50 rounded border border-rose-100">
              {error}
            </div>
          ) : (
            <pre className="text-[10px] bg-slate-900 text-cyan-400 p-3 rounded overflow-auto max-h-[400px] font-mono leading-relaxed shadow-inner">
              {JSON.stringify(data?.data, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function Explorer(): React.JSX.Element {
  const { data, loading, error } = useApiQuery<FlowsStateResponse>(
    endpoints.flowsState,
    { refetchInterval: 30000 }
  );

  const [selectedFlowId, setSelectedFlowId] = useState<string>("");
  const [selectedStepLabel, setSelectedStepLabel] = useState<string>("");

  const flows = data?.flows ?? [];
  const schedule = (data?.schedule ?? []) as ScheduleEntry[];

  const flowOptions = useMemo(() => {
    return flows.map((f) => ({ id: f.id, name: f.name }));
  }, [flows]);

  const stepsInSelectedFlow = useMemo(() => {
    if (!selectedFlowId) return [];
    return schedule.filter((s) => s.flow_id === selectedFlowId);
  }, [selectedFlowId, schedule]);

  const selectedStep = useMemo(() => {
    return stepsInSelectedFlow.find((s) => s.label === selectedStepLabel);
  }, [selectedStepLabel, stepsInSelectedFlow]);

  if (loading && !data) return <LoadingState message="Loading pipeline data..." />;
  if (error) return <Alert status="error">{error}</Alert>;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold text-slate-900">Flow Explorer</h1>
        <p className="text-sm text-slate-500">Inspect inputs and outputs for every step in your automation.</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-1">
          <label className="block text-sm font-medium text-slate-700">Select Flow</label>
          <select
            className="w-full rounded-md border-slate-300 shadow-sm focus:border-cyan-500 focus:ring-cyan-500 sm:text-sm"
            value={selectedFlowId}
            onChange={(e) => {
              setSelectedFlowId(e.target.value);
              setSelectedStepLabel("");
            }}
          >
            <option value="">-- Choose a flow --</option>
            {flowOptions.map((f) => (
              <option key={f.id} value={f.id}>{f.name}</option>
            ))}
          </select>
        </div>

        <div className="space-y-1">
          <label className="block text-sm font-medium text-slate-700">Select Step</label>
          <select
            className="w-full rounded-md border-slate-300 shadow-sm focus:border-cyan-500 focus:ring-cyan-500 sm:text-sm disabled:bg-slate-50 disabled:text-slate-400"
            value={selectedStepLabel}
            onChange={(e) => setSelectedStepLabel(e.target.value)}
            disabled={!selectedFlowId}
          >
            <option value="">-- Choose a step --</option>
            {stepsInSelectedFlow.map((s) => (
              <option key={s.label} value={s.label}>
                {s.label.replace("com.dogfoodandfun.", "")}
              </option>
            ))}
          </select>
        </div>
      </div>

      {selectedStep ? (
        <div className="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden">
          <div className="bg-slate-50 px-4 py-3 border-b border-slate-200">
            <h3 className="text-sm font-bold text-slate-900 font-mono">{selectedStep.label}</h3>
          </div>
          <div className="p-4 space-y-6">
            <section className="space-y-3">
              <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Inputs</h4>
              {selectedStep.input_status && selectedStep.input_status.length > 0 ? (
                <div className="space-y-2">
                  {selectedStep.input_status.map((input) => (
                    <div key={input.path} className="flex items-center justify-between p-2 rounded border border-slate-100 bg-slate-50">
                      <div className="min-w-0">
                        <p className="text-sm font-mono text-slate-800 truncate">{input.path}</p>
                        <p className="text-xs text-slate-500">
                          {input.exists ? `Count: ${input.count} · Age: ${input.age_hours?.toFixed(1)}h` : "Missing"}
                        </p>
                      </div>
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${input.ok ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"}`}>
                        {input.ok ? "OK" : "Blocked"}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-slate-400 italic">No inputs defined for this step.</p>
              )}
            </section>

            <section className="space-y-3">
              <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Output</h4>
              {selectedStep.output_file ? (
                <div className="space-y-3">
                  <div className="p-2 rounded border border-cyan-100 bg-cyan-50 flex items-center gap-3">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-mono text-cyan-900 truncate">{selectedStep.output_file}</p>
                      <p className="text-xs text-cyan-600">Primary artifact produced by this step.</p>
                    </div>
                    <span className="px-2 py-0.5 rounded-full bg-cyan-200 text-cyan-800 text-[10px] font-bold uppercase">Artifact</span>
                  </div>
                  <ArtifactViewer label={selectedStep.label} />
                </div>
              ) : (
                <p className="text-sm text-slate-400 italic">No output file defined for this step.</p>
              )}
            </section>

            {selectedStep.script_path && (
              <section className="pt-4 border-t border-slate-100">
                <p className="text-xs text-slate-400">
                  <span className="font-semibold">Script:</span> <span className="font-mono">{selectedStep.script_path}</span>
                </p>
              </section>
            )}
          </div>
        </div>
      ) : selectedFlowId ? (
        <div className="text-center py-12 bg-slate-50 rounded-lg border-2 border-dashed border-slate-200">
          <p className="text-slate-500">Select a step above to see its data flow.</p>
        </div>
      ) : null}
    </div>
  );
}
