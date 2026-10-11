import re
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# Import your custom manager and config architecture
from csv_manager import CSVManager  # (Assuming your CSVManager class is saved in csv_manager.py)
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
            "angle_ground": float(angle_ground),     # Angle with the ground
            "height": float(height),                 # Height of the delta winglet
            "angle_line": float(angle_line)          # Angle with a straight line
        }
    return None

def analyze_remaining_reports(output_dir="analysis_outputs"):
    """
    Uses CSVManager to fetch unanalyzed reports, processes them, 
    and updates the completion status in status.json.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Initialize your architecture's manager
    manager = CSVManager()
    remaining_csvs = manager.get_remaining_csvs()

    if not remaining_csvs:
        print("No new CSV files to analyze. All files have already been processed according to status.json.")
        return None

    print(f"Found {len(remaining_csvs)} new CSV file(s) to process via CSVManager.")
    all_data = []

    for file_path in remaining_csvs:
        # file_path returned from manager might be a Path object or string
        file_path = Path(file_path)
        params = parse_filename(file_path.name)
        if not params:
            print(f"Skipping {file_path.name}: Filename parameters could not be parsed.")
            continue
        
        try:
            df = pd.read_csv(file_path)
            # Inject parsed geometric parameters into the dataframe
            for key, val in params.items():
                df[key] = val
            df["filename"] = file_path.name
            all_data.append(df)
            
            # Mark complete in your architecture's status tracking
            manager.mark_csv_complete(file_path.name)
            print(f"Processed and marked complete: {file_path.name}")
            
        except Exception as e:
            print(f"Error reading {file_path.name}: {e}")

    if not all_data:
        print("No valid data compiled from the remaining files.")
        return None

    master_df = pd.concat(all_data, ignore_index=True)

    print("\n========================================")
    print("   NEWLY PROCESSED DATASET SUMMARY      ")
    print("========================================")
    summary_cols = ["Velocity (m/s)", "Heat Flux (W/m2)", "Tout (K)", "Tmax (K)", "PressureDrop (Pa)"]
    print(master_df[summary_cols].describe())

    # Generate Visualizations
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')
    
    # 1. Pressure Drop vs Velocity across Configurations
    plt.figure(figsize=(10, 6))
    sns.lineplot(
        data=master_df, 
        x="Velocity (m/s)", 
        y="PressureDrop (Pa)", 
        hue="height", 
        style="angle_ground", 
        marker="o",
        palette="tab10"
    )
    plt.title("Pressure Drop vs. Velocity across Winglet Geometries")
    plt.xlabel("Velocity (m/s)")
    plt.ylabel("Pressure Drop (Pa)")
    plt.legend(title="Height & Angle (Ground)", bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.tight_layout()
    plt.savefig(out_path / "pressure_drop_analysis_new.png", dpi=300)
    plt.close()

    # 2. Thermal Performance: Tout and Tmax vs Heat Flux
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    
    sns.lineplot(
        ax=axes[0], data=master_df, x="Heat Flux (W/m2)", y="Tout (K)", 
        hue="height", style="angle_line", marker="o", palette="viridis"
    )
    axes[0].set_title("Outlet Temperature (Tout) vs Heat Flux")
    axes[0].set_xlabel("Heat Flux (W/m^2)")
    axes[0].set_ylabel("Tout (K)")
    
    sns.lineplot(
        ax=axes[1], data=master_df, x="Heat Flux (W/m2)", y="Tmax (K)", 
        hue="height", style="angle_line", marker="o", palette="plasma"
    )
    axes[1].set_title("Maximum Temperature (Tmax) vs Heat Flux")
    axes[1].set_xlabel("Heat Flux (W/m^2)")
    axes[1].set_ylabel("Tmax (K)")
    
    plt.tight_layout()
    plt.savefig(out_path / "thermal_performance_analysis_new.png", dpi=300)
    plt.close()

    print(f"\n[Success] Analysis plots successfully saved to: {out_path.resolve()}")
    return master_df

if __name__ == "__main__":
    analyze_remaining_reports()