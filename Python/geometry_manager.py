import json
from pathlib import Path
import mesh_config


class GeometryManager:
    def __init__(self):
        self.state_file = mesh_config.MESH_FOLDER / "project_state.json"
        self.completed_geometries = self._load_state()

    def _load_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    return set(json.load(f))
            except Exception:
                return set()
        return set()

    def _save_state(self):
        # Ensure the parent folder (mesh_config.MESH_FOLDER) actually exists
        # before writing. _load_state() already guards against a missing
        # FILE via .exists(), but nothing previously guarded against a
        # missing DIRECTORY — which is exactly what caused:
        #   [Errno 2] No such file or directory: '...\Mesh\project_state.json'
        # right after Case 20 finished, when the first geometry tried to
        # mark itself complete.
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(list(self.completed_geometries), f, indent=4)

    def get_remaining_geometries(self):
        all_geos = list(mesh_config.GEOMETRY_FOLDER.glob(f"*{mesh_config.GEOMETRY_EXTENSION}"))
        valid_geos = [g for g in all_geos if mesh_config.GEOMETRY_PREFIX in g.name.lower()]
        return [g for g in valid_geos if g.name not in self.completed_geometries]

    def mark_geometry_complete(self, geo_name):
        self.completed_geometries.add(geo_name)
        self._save_state()