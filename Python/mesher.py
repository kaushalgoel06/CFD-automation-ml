"""
mesher.py

Fluent Meshing automation using the Watertight Geometry Workflow API.
Handles clean imports, boundary preservation, and eliminates schema warnings.
Dynamically maps isolated workspace subfolders per geometry configuration.
"""

import time
from pathlib import Path
from ansys.fluent.core import launch_fluent

import config
import mesh_config

class FluentMesher:

    def __init__(self):
        print("=" * 60)
        print("Launching Fresh Fluent Meshing Session...")
        print("=" * 60)

        # Retry with backoff: a fresh launch right after a forceful taskkill
        # cleanup can hit a transient license-seat or socket-release race —
        # Fluent's process starts but the gRPC server it listens on isn't
        # ready yet, producing "Connection refused" / StatusCode.UNAVAILABLE.
        # This is environmental, not a code bug, so retrying with a longer
        # pause (rather than crashing the whole multi-geometry run) is the
        # right response. Only after all attempts fail do we give up and
        # let the caller's normal PIPELINE CRASH handling take over.
        max_retries = getattr(config, "FLUENT_LAUNCH_MAX_RETRIES", 3)
        retry_delay = getattr(config, "FLUENT_LAUNCH_RETRY_DELAY_SECONDS", 90)

        self.session = None
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                self.session = launch_fluent(
                    mode="meshing",
                    precision="double",
                    processor_count=4,
                    ui_mode="no_gui_or_graphics"
                )
                break
            except Exception as e:
                last_error = e
                print(f"⚠️ Fluent launch attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    print(f"   Waiting {retry_delay}s before retrying (license seat or socket "
                          f"may need more time to release after the previous session's cleanup)...")
                    time.sleep(retry_delay)

        if self.session is None:
            raise RuntimeError(
                f"Could not launch a Fluent meshing session after {max_retries} attempts. "
                f"Last error: {last_error}\n"
                f"This is typically a transient license-seat or socket-release issue rather "
                f"than a code bug. If it persists: check Task Manager for any lingering "
                f"fluent.exe / cortex.exe / fl_mpi.exe processes, confirm no other Fluent "
                f"session (GUI or otherwise) is holding the Student license seat, and "
                f"consider raising config.FLUENT_LAUNCH_RETRY_DELAY_SECONDS."
            )

        self.workflow = self.session.workflow
        
        # Track the active geometry subfolder dynamically
        self.current_case_dir = None
        print("Connected to brand new Fluent instance.\n")

    def task(self, name):
        """Returns a non-cached live workflow task object reference."""
        return self.workflow.TaskObject[name]

    def initialize_workflow(self):
        print("Initializing Watertight Geometry Workflow...")
        self.workflow.InitializeWorkflow(WorkflowType=mesh_config.WORKFLOW)

    def import_geometry(self, geometry_path: Path):
        absolute_path = geometry_path.resolve()
        print(f"Importing geometry from absolute path: {absolute_path}")
        
        task = self.task("Import Geometry")
        task.Arguments.set_state({
            "FileName": str(absolute_path),
            "LengthUnit": mesh_config.LENGTH_UNIT,
        })
        task.Execute()

    def add_local_sizing(self):
        if not mesh_config.USE_LOCAL_SIZING:
            return

        print("Applying Local Sizing (Body of Influence)...")
        task = self.task("Add Local Sizing")

        arguments = {
            "AddChild": "yes",
            "BOIExecution": mesh_config.LOCAL_EXECUTION,
            "BOIFaceLabelList": mesh_config.LOCAL_SIZING_LABELS,
            "BOIGrowthRate": mesh_config.LOCAL_GROWTH_RATE,
            "BOISize": mesh_config.LOCAL_SIZE,
            "BOIZoneorLabel": mesh_config.LOCAL_ZONE_TYPE,
        }

        task.Arguments.set_state(arguments)
        task.AddChildAndUpdate(DeferUpdate=False)

    def generate_surface_mesh(self):
        print("Generating Surface Mesh...")
        task = self.task("Generate the Surface Mesh")
        task.Arguments.set_state({
            "CFDSurfaceMeshControls": {
                "MaxSize": mesh_config.SURFACE_MAX_SIZE,
                "MinSize": mesh_config.SURFACE_MIN_SIZE,
                "Curvature": mesh_config.USE_CURVATURE,
                "Proximity": mesh_config.USE_PROXIMITY,
                "CellsPerGap": 2,
                "CurvatureNormalAngle": mesh_config.CURVATURE_NORMAL_ANGLE
            }
        })
        task.Execute()

    def describe_geometry(self):
        print("Describing Multi-Domain Region Geometry...")
        task = self.task("Describe Geometry")
        task.Arguments.set_state({
            "SetupType": "The geometry consists of both fluid and solid regions and/or voids",
            "CappingRequired": "No",
            "WallToInternal": "No",
            "InvokeShareTopology": "Yes" if mesh_config.USE_SHARED_TOPOLOGY else "No",
            "NonConformal": "No",
            "Multizone": "No",
        })
        task.Execute()

    def apply_share_topology(self):
        if not mesh_config.USE_SHARED_TOPOLOGY:
            return

        print("Executing Conformal Share Topology...")
        task = self.task("Apply Share Topology")
        task.Arguments.set_state({
            "InterfaceSelect": mesh_config.SHARE_TOPOLOGY_METHOD
        })
        task.Execute()

    def update_boundaries(self):
        print("Updating Boundaries...")
        self.task("Update Boundaries").Execute()

    def create_regions(self):
        print("Analyzing and Separating Cell Zones...")
        self.task("Create Regions").Execute()

    def update_regions(self):
        print("Updating Regions...")
        self.task("Update Regions").Execute()

    def add_boundary_layers(self):
        if not mesh_config.USE_BOUNDARY_LAYERS:
            return

        print("Configuring Boundary Layer Inflation Prisms...")
        task = self.task("Add Boundary Layers")
        task.Arguments.set_state({
            "OffsetMethodType": mesh_config.BOUNDARY_LAYER_OFFSET,
            "NumberOfLayers": mesh_config.BOUNDARY_LAYER_COUNT
        })
        task.AddChildAndUpdate(DeferUpdate=False)

    def generate_volume_mesh(self):
        print("Generating Volume Mesh...")
        task = self.task("Generate the Volume Mesh")
        task.Arguments.set_state({
            "VolumeFill": mesh_config.VOLUME_FILL,
            "AvoidPrismCellSqueeze": mesh_config.AVOID_PRISM_SQUEEZE,
            "IncludeSolidRegions": "yes"
        })
        task.Execute()

    def save_mesh(self, geometry_path: Path, suffix: str = ""):
        if not (hasattr(mesh_config, "SAVE_MESH") and mesh_config.SAVE_MESH):
            return

        name_modifier = f"_{suffix}" if suffix else ""
        mesh_name = geometry_path.stem + name_modifier + ".msh.h5"
        
        # Target the geometry subfolder instead of the general mesh folder
        mesh_path = self.current_case_dir / mesh_name

        print(f"Saving compiled binary mesh -> {mesh_path.name} into target workspace subfolder")
        self.session.tui.file.write_mesh(f'"{mesh_path}"')

    def switch_to_solver(self):
        print("\nHanding off controls: Switching from Meshing to Solver Mode...\n")
        solver = self.session.switch_to_solver()
        print("Solver environment fully online.\n")
        return solver

    def mesh_geometry(self, geometry_path: Path, suffix: str = ""):
        print()
        print("=" * 70)
        print(f"Starting Workflow Execution Sequence: {geometry_path.name}")
        print("=" * 70)

        # 1. Create the dedicated subfolder under the main execution results path
        geo_folder_name = f"cfd.{geometry_path.stem}"
        self.current_case_dir = config.RESULT_FOLDER / geo_folder_name
        self.current_case_dir.mkdir(parents=True, exist_ok=True)
        print(f"📂 Created runtime isolation workspace: {self.current_case_dir}")

        # 2. Execute standard Watertight geometry pipeline
        self.initialize_workflow()
        self.import_geometry(geometry_path)
        self.add_local_sizing()
        self.generate_surface_mesh()
        self.describe_geometry()
        self.apply_share_topology()
        self.update_boundaries()
        self.create_regions()
        self.update_regions()
        self.add_boundary_layers()
        self.generate_volume_mesh()
        
        # 3. Save mesh directly into the new isolated subfolder
        self.save_mesh(geometry_path, suffix=suffix)

        return self.switch_to_solver()