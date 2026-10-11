import re
import glob
from pathlib import Path
import pandas as pd

# Import your custom manager and config architecture if available
try:
    from csv_manager import CSVManager  
except ImportError:
    CSVManager = None

try:
    import config
except ImportError:
    config = None

def parse_filename(filename):
    """
    Parses geometry parameters from the filename structure:
    v5_{angle_with_ground}_{height}_{angle_with_straight_line}.csv
    Example: csv_results_cfd_colloq_baseline_v5_60_2_10.csv
    """
    match = re.search(r"v(\d+)_(\d+)_(\d+)_(\d+)", filename)
    if match:
        version, angle_ground, height, angle_line = match.groups()
        return {
            "version": int(version),
            "angle_ground": float(angle_ground),     
            "height": float(height),                 
            "angle_line": float(angle_line)          
        }
    return None

def analyze_maximum_cooling(output_dir="analysis_outputs"):
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # 1. Resolve and Load Baseline Data
    if config and hasattr(config, "COMPILED_CSV_DIR"):
        baseline_path = config.COMPILED_CSV_DIR / "csv_results_Results_version_4_baseline.csv"
    else:
        baseline_path = Path("csv_results_Results_version_4_baseline.csv")
        if not baseline_path.exists():
            baseline_path = Path(r"C:\Users\Kaushal Goel\Documents\CFD-automation-ml\colloq v4\Compiled_CSV_Reports\csv_results_Results_version_4_baseline.csv")

    if not baseline_path.exists():
        print(f"Error: Baseline file not found at '{baseline_path}'.")
        return None
    
    baseline_df = pd.read_csv(baseline_path)
    baseline_df.columns = [c.strip() for c in baseline_df.columns]
    
    base_dp = baseline_df["PressureDrop (Pa)"].mean()
    base_tout = baseline_df["Tout (K)"].mean()

    print(f"--- Baseline Averages (v4) ---")
    print(f"Mean Pressure Drop : {base_dp:.2f} Pa")
    print(f"Mean Tout          : {base_tout:.2f} K\n")

    # 2. Fetch Variant Reports
    variant_files = []
    if CSVManager:
        try:
            manager = CSVManager()
            variant_files = manager.get_remaining_csvs()
        except Exception:
            pass

    if not variant_files:
        search_dir = baseline_path.parent if baseline_path.exists() else Path(".")
        variant_files = list(search_dir.glob("csv_results_*.csv"))
        if not variant_files:
            variant_files = [Path(f) for f in glob.glob("csv_results_*.csv")]

    all_data = []
    for file_path in variant_files:
        file_path = Path(file_path)
        
        # FIX: Only skip the exact v4 baseline file, allowing variants that contain 'baseline' in their name
        if "results_version_4_baseline" in file_path.name.lower():
            continue
            
        params = parse_filename(file_path.name)
        if not params:
            continue
        
        try:
            df = pd.read_csv(file_path)
            df.columns = [c.strip() for c in df.columns]
            
            for key, val in params.items():
                df[key] = val
            df["filename"] = file_path.name
            all_data.append(df)
            
            if CSVManager and 'manager' in locals():
                try:
                    manager.mark_csv_complete(file_path.name)
                except Exception:
                    pass
        except Exception as e:
            print(f"Error reading {file_path.name}: {e}")

    if not all_data:
        print("No valid variant data found to analyze.")
        return None

    master_df = pd.concat(all_data, ignore_index=True)

    # 3. Find Configurations that Give Maximum Cooling at Each Velocity
    print("\n========================================================")
    print("      MAXIMUM COOLING CONFIGURATIONS PER VELOCITY       ")
    print("========================================================")
    
    optimal_configs = []
    for vel, group in master_df.groupby("Velocity (m/s)"):
        best_row = group.loc[group["Tmax (K)"].idxmin()]
        optimal_configs.append(best_row)

    optimal_df = pd.DataFrame(optimal_configs)
    
    summary_columns = [
        "Velocity (m/s)", "angle_ground", "height", "angle_line", 
        "Tmax (K)", "Tout (K)", "PressureDrop (Pa)", "filename"
    ]
    optimal_summary = optimal_df[[c for c in summary_columns if c in optimal_df.columns]]
    print(optimal_summary.to_string(index=False))
    
    optimal_csv_path = out_path / "optimal_cooling_configurations.csv"
    optimal_summary.to_csv(optimal_csv_path, index=False)
    print(f"\n[Saved] Optimal configurations saved to: {optimal_csv_path.resolve()}")

    # 4. Calculate Overall Average Percentage Decreases Compared to Baseline
    print("\n========================================================")
    print("      OVERALL AVERAGE PERCENTAGE IMPROVEMENTS           ")
    print("========================================================")
    
    mean_variant_dp = master_df["PressureDrop (Pa)"].mean()
    mean_variant_tout = master_df["Tout (K)"].mean()
    mean_variant_tmax = master_df["Tmax (K)"].mean()

    dp_decrease_pct = ((base_dp - mean_variant_dp) / base_dp) * 100
    tout_decrease_pct = ((base_tout - mean_variant_tout) / base_tout) * 100

    print(f"Average Pressure Drop Decrease : {dp_decrease_pct:.2f}% (Variant: {mean_variant_dp:.2f} Pa vs Baseline: {base_dp:.2f} Pa)")
    print(f"Average Tout Decrease          : {tout_decrease_pct:.2f}% (Variant: {mean_variant_tout:.2f} K vs Baseline: {base_tout:.2f} K)")
    print(f"Average Peak Temp (Tmax)       : {mean_variant_tmax:.2f} K")
    print("========================================================")

    return optimal_df

if __name__ == "__main__":
    analyze_maximum_cooling()