export interface SessionStatus {
  platform: string;
  exists: boolean;
  last_saved: string | null;
  login_command: string;
}

export interface SessionStatusResponse {
  sessions: SessionStatus[];
}
