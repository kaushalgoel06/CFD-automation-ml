import json
from pathlib import Path

# ==============================================================================
# BASE DIRECTORIES & PATHS
# ==============================================================================
# Core project directory containing workflow data and reports
MAIN_DIRECTORY = Path(r"C:\Users\Kaushal Goel\Documents\CFD-automation-ml\colloq v4")

# Status and tracking file location for the ML pipeline
STATUS_FILE = Path(r"C:\Users\Kaushal Goel\Documents\CFD-automation-ml\ML\status.json")


# ==============================================================================
# SUBDIRECTORIES
# ==============================================================================
# Directory containing compiled CSV report files
COMPILED_CSV_DIR = MAIN_DIRECTORY / "Compiled_CSV_Reports"

# Directory for CAD / Geometry files (if applicable)
GEOMETRY_DIR = MAIN_DIRECTORY / "Geometry"


# ==============================================================================
# FILE MATCHING & FILTER CONFIGURATIONS
# ==============================================================================
# Settings for CSV processing
CSV_EXTENSION = ".csv"
CSV_PREFIX = "csv_results_"

# Settings for Geometry processing
GEOMETRY_EXTENSION = ".stl"  # Update extension if using .step, .iges, .sldprt, etc.
GEOMETRY_PREFIX = "case_"


# ==============================================================================
# STATUS FILE HELPER FUNCTIONS
# ==============================================================================
def init_status_file():
    """Ensures the parent folder and status JSON file exist with default structure."""
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STATUS_FILE.exists():
        default_data = {
            "completed_geometries": [],
            "completed_csvs": [],
            "pipeline_status": "initialized"
        }
        with open(STATUS_FILE, "w") as f:
            json.dump(default_data, f, indent=4)