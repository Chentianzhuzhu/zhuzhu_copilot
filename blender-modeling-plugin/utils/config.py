"""
Plugin configuration
"""
import bpy

# Plugin info
PLUGIN_NAME = "Advanced Modeling Tools"
PLUGIN_VERSION = (1, 0, 0)
PLUGIN_AUTHOR = "zhuzhu Copilot"

# Default settings
DEFAULT_SETTINGS = {
    "primitive_size": 2.0,
    "bevel_width": 0.01,
    "bevel_segments": 5,
    "subdivision_levels": 2,
}

# Panel categories
CATEGORIES = ["Modeling"]

# Operator categories
OPERATOR_CATEGORIES = {
    "advanced.primitive_add": "Add",
    "advanced.mesh_boolean": "Mesh",
    "advanced.add_modifier": "Modify",
}
