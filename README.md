# CFD Automation and Machine Learning

A computational engineering project focused on automating CFD workflows and developing machine learning methods for thermal-fluid analysis.

## Objectives

- Automate parametric CFD simulations.
- Generate and analyze simulation data.
- Post-process results using Python.
- Explore machine learning models for predicting thermal-fluid performance.
- Investigate surrogate modeling to reduce the computational cost of repeated simulations.

## Tech Stack

- Python
- ANSYS Fluent and PyFluent
- CADQuery
- NumPy, pandas and Matplotlib
- scikit-learn
- OpenFOAM (planned)

## Repository Structure

- `CAD/`: CAD geometry files.
- `Mesh/`: Meshing-related configuration and project state.
- `Python/`: Automation, geometry generation, meshing and reporting scripts.
- `colloq v4/`: Simulation project files and selected results.

## Project Status

Under development. The current focus is automating simulation setup, parameter sweeps, result extraction and post-processing, followed by machine learning experiments using simulation data.

## Future Work

- Improve automation reliability and error handling.
- Validate simulation results against physical expectations or experimental data.
- Build and evaluate surrogate models.
- Explore design optimization using trained models.

## Reproducibility

Software versions, dependencies, boundary conditions and simulation settings will be documented as the workflow develops.
