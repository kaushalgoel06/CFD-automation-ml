import json
import config  # Import the complete config file above

class CSVManager:
    def __init__(self):
        # Ensure status file directory exists
        config.init_status_file()
        self.state_file = config.STATUS_FILE
        self.completed_csvs = self._load_state()

    def _load_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    return set(data.get("completed_csvs", []))
            except Exception:
                return set()
        return set()

    def _save_state(self):
        data = {}
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
            except Exception:
                pass

        data["completed_csvs"] = list(self.completed_csvs)

        with open(self.state_file, "w") as f:
            json.dump(data, f, indent=4)

    def get_remaining_csvs(self):
        if not config.COMPILED_CSV_DIR.exists():
            return []
        
        all_csvs = list(config.COMPILED_CSV_DIR.glob(f"*{config.CSV_EXTENSION}"))
        valid_csvs = [c for c in all_csvs if config.CSV_PREFIX.lower() in c.name.lower()]
        return [c for c in valid_csvs if c.name not in self.completed_csvs]

    def mark_csv_complete(self, csv_name):
        self.completed_csvs.add(csv_name)
        self._save_state()