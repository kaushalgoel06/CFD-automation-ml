# CFD-Automation-ML

**Automated CFD, parametric design exploration, and machine learning for thermal-fluid engineering**

[![Python](https://img.shields.io/badge/Python-3.14-blue?logo=python)](https://www.python.org/)
[![CFD](https://img.shields.io/badge/Simulation-ANSYS%20Fluent-orange)](https://www.ansys.com/products/fluids/ansys-fluent)
[![Data Analysis](https://img.shields.io/badge/Data%20Analysis-Pandas%20%7C%20NumPy-green)](https://pandas.pydata.org/)
[![Machine Learning](https://img.shields.io/badge/ML-Scikit--learn-yellow)](https://scikit-learn.org/)

## Overview

This project combines CFD automation, parametric geometry exploration, and machine learning into one workflow for thermal-fluid design. Python and PyFluent drive ANSYS Fluent simulations, results are exported to CSV, and pandas-based scripts compare geometric variants against a baseline. The ML component is a foundation for surrogate models that could eventually reduce the number of expensive CFD runs.

**Objectives**

- Run repeated simulations across operating conditions and geometries with minimal manual effort
- Track completed cases, recover from interrupted runs, and detect solver failures
- Extract temperature, pressure-drop, and related metrics in a standard format
- Compare candidate designs against a baseline
- Use simulation data to build predictive models

## Engineering problem

Cooling improvements often come with higher flow resistance, so judging a design on one metric can mislead. The study focuses on a heatsink cooling configuration with delta-wing / vortex-generator features.

| Category | Parameters / outputs |
|---|---|
| Operating conditions | Inlet velocity, wall heat flux |
| Geometry | Version, angle with ground, feature height, angle with straight line |
| Thermal | Maximum, outlet, and average temperature |
| Hydraulic | Pressure drop, mass flow rate |
| Derived | Percentage change vs. baseline |

## Workflow

```text
CAD → Meshing → Fluent / PyFluent sweeps → CSV reports
    → Validation → Baseline comparison → ML models → Design exploration
```

This is the intended end-to-end flow; the degree of integration between stages depends on the current script configuration.

## Repository structure

```text
CFD-automation-ml/
├── CAD/                 # Geometry files
├── Python/              # CFD automation
│   ├── config.py        # Sweeps, boundary names, solver settings
│   ├── geometry_manager.py
│   ├── mesh_config.py
│   ├── mesher.py
│   ├── solver_setup.py
│   ├── reports.py
│   ├── runner.py        # Main driver
│   └── cadquerykgf.py   # CAD generation
├── Mesh/project_state.json
├── colloq v4/Compiled_CSV_Reports/   # CFD result CSVs
├── Data analysis/csv_manager.py
├── ML/
│   ├── analysis.py
│   ├── config.py
│   ├── csv_manager.py
│   ├── linear regression.py
│   └── status.json
├── analysis_outputs/    # Plots and summary CSVs
└── README.md
```

Generated simulation files may exist locally or be excluded from version control.

## How it works

**Parametric execution.** `runner.py` iterates over every inlet velocity and heat-flux combination for each geometry. For example, 5 velocities × 4 heat fluxes gives 20 cases per geometry. It applies and verifies boundary conditions, runs the solver in chunks, flags stalled or diverged solutions, and resumes from existing case files. Completed cases are checked per geometry, so geometries that share operating conditions are not confused.

**Meshing and solver setup.** `mesher.py` and `solver_setup.py` automate Fluent meshing and physics configuration through PyFluent. Boundary names must match the zones in the loaded case; the code handles some naming variations and verifies applied values.

**Result extraction.** `reports.py` pulls temperature, mass-flow, and pressure-drop reports. A run that finishes its iterations but cannot produce its required reports is not treated as successful.

**Data handling.** `ML/analysis.py` reads geometry parameters from filenames, e.g. `csv_results_cfd_colloq_baseline_v5_60_2_10.csv` (version 5, 60° to ground, height 2, 10° to straight line). `CSVManager` records processed files in `status.json` to avoid repeat work. Files that break the naming convention may be skipped.

## Analysis

The analysis compares variants against `csv_results_Results_version_4_baseline.csv`, then selects the lowest maximum-temperature design at each inlet velocity and writes it to `analysis_outputs/optimal_cooling_configurations.csv`.

Percentage reduction versus baseline for a quantity where lower is better:

```
Reduction (%) = (X_baseline − X_variant) / X_baseline × 100
```

Positive means a reduction, negative an increase.

**Limitation:** minimizing maximum temperature alone does not identify the best design, since a lower temperature may cost a much higher pressure drop. A Pareto analysis over temperature and pressure drop is a planned improvement.

## Machine learning

- **Inputs:** inlet velocity, heat flux, geometry version (treated as categorical), feature height, and the two angles
- **Targets:** maximum, outlet, and average temperature; pressure drop
- **Current model:** a linear-regression baseline in `ML/linear regression.py`
- **Evaluation:** MSE, MAE, and R². Because rows from the same geometry are correlated, grouped validation by geometry gives a more realistic test than a random split

Linear regression is an interpretable baseline but will not capture nonlinear flow physics or regime changes. The repository does not yet contain a validated surrogate model or an automated optimization loop.

## Setup

**Requirements:** Windows, a licensed ANSYS Fluent installation, a compatible PyFluent, Python, and enough memory and storage for your mesh sizes.

```powershell
git clone https://github.com/kaushalgoel06/CFD-automation-ml.git
cd CFD-automation-ml
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install numpy pandas matplotlib scikit-learn ansys-fluent-core
```

1. **Configure paths.** `Python/config.py`, `Python/mesh_config.py`, and `ML/config.py` contain machine-specific Windows paths; update them for your setup.
2. **Configure the study.** Set velocities, heat fluxes, boundary names, solver iterations, report definitions, and output directories.
3. **Run the sweep.** `python .\Python\runner.py`. Test a small case first and inspect the logs and exported reports before a large sweep.
4. **Run the analysis.** `python .\ML\analysis.py`

Note that the runner's cleanup logic can terminate running Fluent processes, so avoid other Fluent sessions while it runs.

## Reliability notes

The solver driver guards against stalled iterations, divergence, inactive flow, and unverified boundary-condition writes. These checks improve robustness but do not guarantee physical correctness. Before accepting a result, confirm the correct geometry and boundary conditions were applied, residuals and monitored quantities behaved sensibly, mass and energy balances are reasonable, results are reasonably mesh-independent, and trends are physically plausible. Do not pool data generated with different solver settings into one training set.

## Future development

- Portable, repository-relative paths and a pinned `requirements.txt`
- A unified data schema and automated validation of missing, duplicate, or failed runs
- Pareto-based design selection and richer plots
- Dimensionless thermal-hydraulic metrics
- Cross-validation and nonlinear models (e.g. tree-based)
- Validated surrogate modelling, constrained optimization, and active learning
- Run metadata snapshots (configuration and software versions) for reproducibility

These are planned extensions, not completed features.

## License

No license is specified yet. Add a `LICENSE` file before presenting the repository as open source.

## Author

**Kaushal Goel** · Mechanical Engineering | CFD | Engineering Automation | Machine Learning
GitHub: [@kaushalgoel06](https://github.com/kaushalgoel06)