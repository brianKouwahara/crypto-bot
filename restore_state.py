# restore_state.py
import os, glob, shutil
from config import STATE_FILE

BACKUP_DIR = os.getenv("STATE_BACKUP_DIR", "state_backups")

def restore_last():
    base = os.path.splitext(os.path.basename(STATE_FILE))[0]  # ex: 'state'
    pattern = os.path.join(BACKUP_DIR, f"{base}_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        print("[RESTORE] Aucun backup trouvé.")
        return
    last = files[-1]
    shutil.copy2(last, STATE_FILE)
    print(f"[RESTORE] {last} -> {STATE_FILE}")

if __name__ == "__main__":
    restore_last()
