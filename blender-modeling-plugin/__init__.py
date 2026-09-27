# Blender Modeling Plugin
# Based on Blender Python API

bl_info = {
    "name": "Advanced Modeling Tools",
    "author": "zhuzhu Copilot",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Modeling",
    "description": "Professional modeling tools based on Blender API",
    "category": "Modeling",
    "support": "COMMUNITY",
}

import bpy
import bmesh
import math
from mathutils import Vector, Matrix
from .operators.primitive import *
from .operators.boolean import *
from .operators.modifiers import *
from .models.mesh_utils import *
from .models.transform_utils import *

classes = (
    ADVANCED_MT_primitive_add_menu,
    ADVANCED_OT_box,
    ADVANCED_OT_cylinder,
    ADVANCED_OT_sphere,
    ADVANCED_OT_cone,
    ADVANCED_OT_circle,
    ADVANCED_OT_uv_sphere,
    ADVANCED_OT_ico_sphere,
    ADVANCED_OT_mesh_boolean_cut,
    ADVANCED_OT_mesh_boolean_union,
    ADVANCED_OT_mesh_boolean_intersection,
    ADVANCED_OT_add_modifier,
    ADVANCED_OT_remove_modifier,
    ADVANCED_OT_apply_modifier,
    ADVANCED_OT_subdivision_surface,
    ADVANCED_OT_smoke_simple,
    ADVANCED_OT_mirror,
    ADVANCED_OT_solidify,
    ADVANCED_OT_bevel,
    ADVANCED_OT_spin,
    ADVANCED_OT_array,
    ADVANCED_OT_screw,
    ADVANCED_OT_extrude_region,
    ADVANCED_OT_extrude_edges,
    ADVANCED_OT_merge,
    ADVANCED_OT_split,
    ADVANCED_OT_delete_vertices,
    ADVANCED_OT_delete_edges,
    ADVANCED_OT_delete_faces,
    ADVANCED_OT_edge_merge,
    ADVANCED_OT_face_fill,
    ADVANCED_OT_edge_fill,
    ADVANCED_OTKnife,
    ADVANCED_OT_shrink_fatten,
    ADVANCED_OT_edge_slide,
    ADVANCED_OT_select_random,
    ADVANCED_OT_select_linked,
    ADVANCED_OT_select_all,
    ADVANCED_OT_select_by_type,
    ADVANCED_OT_select_by_material,
    ADVANCED_OT_transform_copy,
    ADVANCED_OT_transform_paste,
    ADVANCED_OT_transform_apply,
    ADVANCED_OT_transform_reset,
    ADVANCED_OT_align_view,
    ADVANCED_OT_snap_selection,
    ADVANCED_OT_snap_cursor,
    ADVANCED_OT_mirror_axis,
    ADVANCED_OT_origin_set,
    ADVANCED_OT_center_set,
    ADVANCED_OT_material_new,
    ADVANCED_OT_material_assign,
    ADVANCED_OT_material_remove,
    ADVANCED_OT_material_copy,
    ADVANCED_PT_modeling_tools,
    ADVANCED_PT_geometric_tools,
    ADVANCED_PT_selection_tools,
    ADVANCED_PT_transform_tools,
)

def menu_func_add(self, context):
    self.layout.menu("ADVANCED_MT_primitive_add_menu")

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_mesh_add.append(menu_func_add)

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_func_add)

if __name__ == "__main__":
    register()
