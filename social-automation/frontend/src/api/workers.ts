import apiClient from "./client";

export interface WorkerStatus {
  label: string;
  title: string;
  description: string;
  status: "never" | "running" | "success" | "error";
  last_run: string | null;
  message: string | null;
  is_instance?: boolean;
  re_run_guard?: number; // 0 = no daily limit; 1 (default) = block after first daily success
}

export async function fetchWorkers(): Promise<WorkerStatus[]> {
  const { data } = await apiClient.get<WorkerStatus[]>("/workers");
  return data;
}

export async function fetchWorkerStatus(label: string): Promise<WorkerStatus> {
  const { data } = await apiClient.get<WorkerStatus>(
    `/workers/${encodeURIComponent(label)}/status`
  );
  return data;
}

interface TriggerOptions {
  count?: number;
  force?: boolean;
  recipeIds?: string[];
  headless?: boolean;
}

export async function triggerWorker(label: string, options: TriggerOptions = {}): Promise<unknown> {
  const { count = 1, force = false, recipeIds, headless } = options;
  const { data } = await apiClient.post(
    `/workers/${encodeURIComponent(label)}/trigger`,
    {
      count,
      force,
      ...(recipeIds && recipeIds.length > 0 ? { recipe_ids: recipeIds } : {}),
      ...(headless ? { headless: true } : {}),
    }
  );
  return data;
}

export async function fetchWorkerLog(
  label: string,
  lines = 200
): Promise<{ log: string }> {
  const { data } = await apiClient.get(
    `/workers/${encodeURIComponent(label)}/log?lines=${lines}`
  );
  return data;
}

export async function fetchWorkerArtifact(label: string): Promise<unknown> {
  const { data } = await apiClient.get(
    `/workers/${encodeURIComponent(label)}/artifact`
  );
  return data;
}
