from pathlib import Path



# ==========================================================

# 1. MAIN DIRECTORY

# ==========================================================

MAIN_PROJECT_DIR = Path(r"C:\Users\Kaushal Goel\Documents\Colloq\colloq v4")



# The fix for 'module has no attribute RESULT_FOLDER'

RESULT_FOLDER = MAIN_PROJECT_DIR / "Simulation_Data"

RESULT_FOLDER.mkdir(parents=True, exist_ok=True)



# The fix for the CSV loading error

CSV_EXPORT_FOLDER = MAIN_PROJECT_DIR / "Compiled_CSV_Reports"

CSV_EXPORT_FOLDER.mkdir(parents=True, exist_ok=True)

RESULTS_CSV = CSV_EXPORT_FOLDER / "parametric_results.csv"



# ==========================================================

# FLUENT CONNECTION

# ==========================================================

SERVER_INFO = r"C:\Users\Kaushal Goel\Documents\Colloq\colloq v4 with delta generator degree 15\server_info-21948.txt"



# ==========================================================

# FLUENT BOUNDARY NAMES

# ==========================================================

VELOCITY_INLET_NAME = "velocity_inlet"

HEAT_WALL_NAME = "heat-wall"

OUTLET_NAME = "outlet"



# Aliases for compatibility with your runner.py logic

REAL_INLET_NAME = VELOCITY_INLET_NAME

REAL_WALL_NAME = HEAT_WALL_NAME



# ==========================================================

# GEOMETRY DIMENSIONS (For Nusselt & Friction Factor Math)

# ==========================================================

CHANNEL_WIDTH = 0.040   # meters

CHANNEL_HEIGHT = 0.040  # meters

CHANNEL_LENGTH = 0.300  # meters



# ==========================================================

# PARAMETRIC STUDY

# ==========================================================

VELOCITIES = [1.0, 2.0, 3.0, 4.0, 5.0]

HEAT_FLUXES = [6250, 12500, 18750, 25000]



# ==========================================================

# SOLVER SETTINGS

# ==========================================================

ITERATIONS_PER_CASE = 400



# ==========================================================

# FILE NAMING

# ==========================================================

CASE_PREFIX = "HS"

CASE_NAME_FORMAT = "{prefix}_V{velocity:.1f}_Q{heat_flux}"



# ==========================================================

# REPORT SETTINGS & LOGGING

# ==========================================================

EXPORT_CSV = True

PRINT_PROGRESS = True 

