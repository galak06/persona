---
name: sheet-backup
description: >
  Back up Google Sheet data and local state files to prevent data loss.
  Exports both sheet tabs to JSON, copies all state files, maintains
  90-day retention. Use when: "backup sheet", "save backup", "backup data",
  or run weekly via scheduled task.
---

# Sheet Backup Skill

Backs up Google Sheet data and local state files to prevent accidental data loss.

## Workflow

### Step 1 — Navigate to Google Sheet

Open the Google Sheet via Chrome browser:
- URL: stored in `config.json` → social_sheet_url
- Authenticate if needed via Cowork

### Step 2 — Extract Sheet Tabs

For each tab (posts, publish_posts):

1. Click on tab to activate
2. Select all cells (Ctrl+A or Cmd+A)
3. Extract via JavaScript:

```python
# Run in browser console via mcp__Claude_in_Chrome__javascript_tool
sheet_data = []
rows = document.querySelectorAll('div[role="gridcell"]')
for row in rows:
    cell_text = row.innerText
    sheet_data.append(cell_text)

# Alternative: use Sheets API if available
# or parse visible table structure
```

4. Save as JSON:

```python
import json
from datetime import datetime
from pathlib import Path

backup_dir = Path('backups/sheets')
backup_dir.mkdir(parents=True, exist_ok=True)

timestamp = datetime.utcnow().isoformat().replace(':', '-')
for tab_name, data in tabs.items():
    filename = f"{tab_name}_{timestamp}.json"
    filepath = backup_dir / filename
    
    with open(filepath, 'w') as f:
        json.dump({
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'tab_name': tab_name,
            'data': data
        }, f, indent=2)
    
    print(f"Backed up {tab_name} → {filename}")
```

### Step 3 — Copy State Files

Copy all state files to timestamped backup directory:

```python
import shutil
from pathlib import Path
from datetime import datetime

state_dir = Path('.claude/state')
backup_dir = Path('backups/state')

timestamp = datetime.utcnow().isoformat().replace(':', '-').split('.')[0]
backup_path = backup_dir / timestamp

backup_path.mkdir(parents=True, exist_ok=True)

for state_file in state_dir.glob('*.json'):
    shutil.copy(state_file, backup_path / state_file.name)
    print(f"Copied {state_file.name}")

print(f"State backup complete: {backup_path}")
```

### Step 4 — Prune Old Backups

Remove backups older than 90 days:

```python
from datetime import datetime, timedelta
from pathlib import Path

backup_dir = Path('backups')
cutoff = datetime.utcnow() - timedelta(days=90)

for backup_path in backup_dir.glob('**/*'):
    if backup_path.is_file():
        mtime = datetime.fromtimestamp(backup_path.stat().st_mtime)
        if mtime < cutoff:
            backup_path.unlink()
            print(f"Deleted old backup: {backup_path}")
```

### Step 5 — Trim Completed Ideas from Sheet

After backup is saved, clean up the Google Sheet to keep it lean (~20-30 rows max).

**Rules:**
- `publish` / `approved` / `wp_draft` → KEEP (active pipeline)
- `wp_published` / `social_done` → keep latest 2 per category, DELETE the rest
- `skipped` → DELETE all

**Process:**

1. Read all rows from the "posts" tab (already loaded in Step 2)
2. Group rows by Category + Status
3. Identify rows to delete:

```python
from collections import defaultdict

rows_to_keep = []
rows_to_delete = []

# Active statuses — always keep
active_statuses = {"publish", "approved", "wp_draft"}

# Completed statuses — keep latest 2 per category
completed_statuses = {"wp_published", "social_done"}

# Group completed rows by category
completed_by_category = defaultdict(list)

for row in all_rows:
    status = row.get("Status", "").strip().lower()
    category = row.get("Category", "").strip()

    if status in active_statuses:
        rows_to_keep.append(row)
    elif status in completed_statuses:
        completed_by_category[category].append(row)
    elif status == "skipped":
        rows_to_delete.append(row)
    else:
        rows_to_keep.append(row)  # unknown status — keep to be safe

# Keep latest 2 completed per category, delete the rest
for category, completed_rows in completed_by_category.items():
    # Sort by date/position (most recent first)
    sorted_rows = sorted(completed_rows, key=lambda r: r.get("_row_index", 0), reverse=True)
    rows_to_keep.extend(sorted_rows[:2])
    rows_to_delete.extend(sorted_rows[2:])

print(f"Sheet cleanup: keeping {len(rows_to_keep)}, deleting {len(rows_to_delete)}")
```

4. Delete identified rows from Google Sheet:
   - Navigate to the "posts" tab
   - Select and delete rows from bottom to top (to avoid shifting row indices)
   - For each row to delete: right-click → Delete row

5. Verify final row count matches expected

6. Send Telegram notification:
```
🧹 Sheet cleanup complete
  Kept: {len(rows_to_keep)} rows (active + 2 recent per category)
  Deleted: {len(rows_to_delete)} rows (old completed + skipped)
```

**Safety:** This step only runs AFTER backup is confirmed saved in Step 2-3. If backup failed, skip cleanup entirely.

### Step 6 — Log Backup Completion

```python
import json
from datetime import datetime
from pathlib import Path

log_entry = {
    "timestamp": datetime.utcnow().isoformat() + 'Z',
    "action": "backup_complete",
    "sheets_backed_up": ["posts", "publish_posts"],
    "state_files_backed_up": len(list(Path('.claude/state').glob('*.json'))),
    "backup_location": "backups/",
    "status": "SUCCESS"
}

log_file = Path('logs/backup_log.jsonl')
log_file.parent.mkdir(parents=True, exist_ok=True)

with open(log_file, 'a') as f:
    f.write(json.dumps(log_entry) + '\n')

print("Backup logged successfully")
```

## Error Handling

- Sheet not accessible → log "SHEET_UNAVAILABLE", notify user
- State directory missing → skip state backup, log warning
- Disk space low → warn user before pruning
- Backup creation fails → retry once, then abort with error log

## Activation Trigger

Use this skill when the user requests:
- "backup sheet"
- "save backup"
- "backup data"
- Or via scheduled weekly task
