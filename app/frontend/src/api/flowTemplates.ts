/**
 * Flow template catalog: the pre-brand flow definitions (script, cron,
 * approval/browser/notify flags) that `lib/brand_provisioning.py` reads
 * when onboarding a NEW brand. Editing one here only affects brands
 * provisioned after the edit -- an already-provisioned brand's own
 * `schedule_tasks` row is an independent copy (see `api/workers.ts` /
 * the Schedule page for editing THAT).
 */

export interface FlowTemplate {
  id: string;
  platform: string;
  title: string;
  description: string;
  order_num: number;
  script: string | null;
  skill: string | null;
  args: string[];
  depends_on: string[];
  requires_approval: boolean;
  approval_channel: string | null;
  requires_browser: boolean;
  re_run_guard: boolean;
  output_file: string | null;
  schedule: { cron?: string; cadence?: string };
  inputs: unknown[];
  telegram_notify: boolean;
}

export interface FlowTemplateUpdate {
  description?: string;
  script?: string;
  cron?: string;
  requires_approval?: boolean;
  requires_browser?: boolean;
  re_run_guard?: boolean;
  telegram_notify?: boolean;
}
