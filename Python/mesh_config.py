from pathlib import Path

# ==========================================
# 1. FILE PATHS & DIRECTORIES
# ==========================================
GEOMETRY_FOLDER = Path(r"C:\Users\Kaushal Goel\Documents\Colloq\CAD")
GEOMETRY_EXTENSION = ".scdocx"
GEOMETRY_PREFIX = "colloq"

# Main workspace directory where isolated per-geometry subfolders are created
MESH_FOLDER = Path(r"C:\Users\Kaushal Goel\Documents\Colloq\Mesh")


# ==========================================
# 2. WORKFLOW & GENERAL SETUP
# ==========================================
# Watertight Geometry Workflow type (Standard for multi-region fluid/solid setups)
WORKFLOW = "Watertight Geometry"
LENGTH_UNIT = "m"  # Options: "m", "mm", "in", etc.
SAVE_MESH = True   # Automatically export compiled .msh.h5 mesh file


# ==========================================
# 3. SURFACE MESHING CONTROLS (~600k cells target)
# ==========================================
SURFACE_MAX_SIZE = 0.0035          
SURFACE_MIN_SIZE = 0.0001          
USE_CURVATURE = "yes"              # Options: "yes", "no"
USE_PROXIMITY = "yes"              # Options: "yes", "no"          
CURVATURE_NORMAL_ANGLE = 10        # Degrees


# ==========================================
# 4. LOCAL SIZING / BODY OF INFLUENCE (BOI)
# ==========================================
USE_LOCAL_SIZING = False           # Toggle True/False to enable local BOI sizing
LOCAL_EXECUTION = "Body of Influence"
LOCAL_SIZING_LABELS = []           # List of face/zone labels for local sizing
LOCAL_GROWTH_RATE = 1.2
LOCAL_SIZE = 0.0001                # Local element size constraint
LOCAL_ZONE_TYPE = "face"           # Options: "face", "body"


# ==========================================
# 5. GEOMETRY DESCRIPTION & SHARE TOPOLOGY
# ==========================================
# Enables conformal contact sharing across multiple bodies/parts
USE_SHARED_TOPOLOGY = True         
SHARE_TOPOLOGY_METHOD = "Global"   # Options: "Global", "Per Face", etc.


# ==========================================
# 6. BOUNDARY LAYER (INFLATION) CONTROLS
# ==========================================
USE_BOUNDARY_LAYERS = True
BOUNDARY_LAYER_COUNT = 10          
BOUNDARY_LAYER_OFFSET = "smooth-transition"  # Options: "smooth-transition", "first-height", etc.
BOUNDARY_LAYER_GROWTH = 1.15       


# ==========================================
# 7. VOLUME MESHING CONTROLS
# ==========================================
# Volume element fill method: "poly" (polyhedral), "tet" (tetrahedral), "hexcore"
VOLUME_FILL = "poly"               
AVOID_PRISM_SQUEEZE = "yes"        # Options: "yes", "no"


# ==========================================
# 8. SOLVER-SPECIFIC PARAMETERS (POST-HANDOFF)
# ==========================================
# Use these parameters after handoff to configure your solver physics/operations
SOLVER_SETTINGS = {
    "solver_type": "Pressure-Based", # or "Density-Based"
    "time_mode": "Steady",                # Options: "Steady", "Transient"
    "turb_model": "k-omega",              # Options: "k-epsilon", "k-omega", "sst", "laminar"
    "turb_model_opt": "sst",              # Sub-option (e.g., "Standard", "SST")
    "gravity_enabled": True,
    "gravity_vector": [0.0, -9.81, 0.0],  # m/s^2
    "operating_pressure": 101325.0,       # Pascals
    
    # Numerical Schemes
    "pressure_velocity_coupling": "Coupled", # Options: "Coupled", "Simple", "Simplec", "Piso"
    "gradient_scheme": "Least Squares Cell Based",
    
    # Convergence criteria (Residuals)
    "residual_continuity": 1e-4,
    "residual_velocity": 1e-4,
    "residual_turbulence": 1e-3,
    
    # Execution setup
    "number_of_iterations": 500,
    "save_per_iterations": 100
}