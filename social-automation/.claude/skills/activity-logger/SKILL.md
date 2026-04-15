---
name: activity-logger
description: >
  Log every social media action to JSONL + update the Excel tracker Post Log sheet.
  Called after each comment, like, or group join by other agents.
  Also generates weekly/monthly summary reports on demand.
  Use when the user says "log activity", "show engagement activity",
  "show last 7 days", "generate activity report", or "show last run status".
---

# Activity Logger — DogFoodAndFun

Persistent log of all social media actions. Source of truth for engagement history.

---

## Log an Action

Called by other agents after each action. Write to both JSONL and Excel.

### Log Entry Schema

```python
import json
from datetime import datetime
from pathlib import Path

def log_action(
    platform: str,         # "facebook" | "instagram"
    action: str,           # "comment" | "like" | "group_visit" | "group_join"
    target_url: str,       # post URL or group URL
    target_name: str,      # group name or hashtag
    content: str = "",     # comment text or empty for likes
    status: str = "success",  # "success" | "failed" | "skipped"
    relevance_score: float = 0.0,
    notes: str = "",
) -> None:
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "platform": platform,
        "action": action,
        "target_url": target_url,
        "target_name": target_name,
        "content": content[:300] if content else "",
        "status": status,
        "relevance_score": relevance_score,
        "notes": notes,
    }

    log_file = Path('../logs/engagement_log.jsonl')
    log_file.parent.mkdir(exist_ok=True)
    with log_file.open('a') as f:
        f.write(json.dumps(entry) + "\n")

    # Also write to audit trail
    audit_file = Path('../logs/audit_trail.json')
    audit = []
    if audit_file.exists():
        with audit_file.open() as f:
            audit = json.load(f)
    audit.append(entry)
    # Keep last 1000 entries
    audit = audit[-1000:]
    with audit_file.open('w') as f:
        json.dump(audit, f, indent=2)
```

### Write to Excel Post Log

```python
from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from datetime import datetime

EXCEL_PATH = '../facebook_groups_tracker.xlsx'
PLATFORM_COLORS = {
    'facebook': 'DBEAFE',  # light blue
    'instagram': 'FCE7F3', # light pink
}

def update_excel_log(entry: dict) -> None:
    try:
        wb = load_workbook(EXCEL_PATH)
        if 'Post Log' not in wb.sheetnames:
            ws = wb.create_sheet('Post Log')
            # Add headers
            headers = ['Date', 'Platform', 'Action', 'Group/Hashtag', 
                      'Content Preview', 'Status', 'Score', 'URL', 'Notes']
            for i, h in enumerate(headers, 1):
                ws.cell(1, i, h)
        else:
            ws = wb['Post Log']

        row = ws.max_row + 1
        ws.cell(row, 1, entry['date'])
        ws.cell(row, 2, entry['platform'].title())
        ws.cell(row, 3, entry['action'])
        ws.cell(row, 4, entry['target_name'])
        ws.cell(row, 5, entry['content'][:100] if entry['content'] else '')
        ws.cell(row, 6, entry['status'].title())
        ws.cell(row, 7, entry['relevance_score'])
        ws.cell(row, 8, entry['target_url'])
        ws.cell(row, 9, entry['notes'])

        # Color by platform
        color = PLATFORM_COLORS.get(entry['platform'], 'FFFFFF')
        fill = PatternFill('solid', fgColor=color)
        for col in range(1, 10):
            ws.cell(row, col).fill = fill

        wb.save(EXCEL_PATH)
    except Exception as e:
        # Log to errors.log but don't crash — JSONL is source of truth
        with open('../logs/errors.log', 'a') as f:
            f.write(f"{datetime.utcnow().isoformat()} EXCEL_WRITE_ERROR: {e}\n")
```

---

## Log an Error

```python
def log_error(
    agent: str,
    error_type: str,
    message: str,
    context: str = "",
) -> None:
    from pathlib import Path
    from datetime import datetime
    
    error_file = Path('../logs/errors.log')
    error_file.parent.mkdir(exist_ok=True)
    with error_file.open('a') as f:
        f.write(
            f"{datetime.utcnow().isoformat()} [{agent}] {error_type}: {message}"
            + (f" | context: {context}" if context else "")
            + "\n"
        )
```

---

## Generate Activity Report

When user asks for "last 7 days" or "activity report":

```python
from datetime import date, timedelta
import json
from pathlib import Path

def get_activity_report(days: int = 7) -> str:
    log_file = Path('../logs/engagement_log.jsonl')
    if not log_file.exists():
        return "No activity logged yet."

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    entries = []
    with log_file.open() as f:
        for line in f:
            entry = json.loads(line)
            if entry['date'] >= cutoff:
                entries.append(entry)

    if not entries:
        return f"No activity in the last {days} days."

    # Aggregate
    by_platform = {}
    by_action = {}
    by_group = {}
    for e in entries:
        by_platform[e['platform']] = by_platform.get(e['platform'], 0) + 1
        by_action[e['action']] = by_action.get(e['action'], 0) + 1
        by_group[e['target_name']] = by_group.get(e['target_name'], 0) + 1

    report = [f"=== Activity Report — Last {days} Days ==="]
    report.append(f"Total actions: {len(entries)}")
    report.append("\nBy platform:")
    for k, v in sorted(by_platform.items(), key=lambda x: -x[1]):
        report.append(f"  {k}: {v}")
    report.append("\nBy action:")
    for k, v in sorted(by_action.items(), key=lambda x: -x[1]):
        report.append(f"  {k}: {v}")
    report.append("\nTop groups/hashtags:")
    for k, v in sorted(by_group.items(), key=lambda x: -x[1])[:10]:
        report.append(f"  {k}: {v}")

    return "\n".join(report)
```

---

## Show Last Run Status

```python
def show_last_run_status() -> str:
    import json
    from pathlib import Path
    
    last_run_file = Path('../.claude/state/last_run.json')
    if not last_run_file.exists():
        return "No runs recorded yet."
    
    with last_run_file.open() as f:
        last_run = json.load(f)
    
    lines = ["=== Last Run Status ==="]
    for agent, info in last_run.items():
        lines.append(f"\n{agent}:")
        for k, v in info.items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)
```

---

## Key Rules

- JSONL log is the source of truth — Excel write failures are non-fatal
- Always log failed actions too (status="failed") — helps debug issues
- Never truncate the JSONL log — it's append-only
- Excel Post Log is for human review — JSONL is for programmatic queries
