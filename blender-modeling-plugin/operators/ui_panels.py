"""
UI Panels for modeling tools
"""
import bpy
from .operators.primitive import *
from .operators.boolean import *
from .operators.modifiers import *
from .operators.mesh_operations import *
from .operators.selection import *
from .operators.transform import *


class ADVANCED_PT_modeling_tools(bpy.types.Panel):
    """Main modeling tools panel"""
    bl_label = "Advanced Modeling Tools"
    bl_idname = "ADVANCED_PT_modeling_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Modeling"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Section: Primitive Creation
        box = layout.box()
        box.label(text="Primitive Creation", icon='MESH_CUBE')
        col = box.column(align=True)
        col.operator("advanced.box", text="Box", icon='MESH_CUBE')
        col.operator("advanced.cylinder", text="Cylinder", icon='MESH_CYLINDER')
        col.operator("advanced.sphere", text="Sphere", icon='MESH_ICOSPHERE')
        
        # Section: Boolean Operations
        box = layout.box()
        box.label(text="Boolean Operations", icon='MOD_BOOLEAN')
        col = box.column(align=True)
        col.operator("advanced.mesh_boolean_cut", text="Boolean Cut", icon='MOD_BOOLEAN')
        col.operator("advanced.mesh_boolean_union", text="Boolean Union", icon='MOD_BOOLEAN')
        col.operator("advanced.mesh_boolean_intersection", text="Boolean Intersect", icon='MOD_BOOLEAN')
        
        # Section: Mesh Operations
        box = layout.box()
        box.label(text="Mesh Operations", icon='EDITMODE_HLT')
        col = box.column(align=True)
        col.operator("advanced.extrude_region", text="Extrude Region", icon='GRID')
        col.operator("advanced.extrude_edges", text="Extrude Edges", icon='GRID')
        col.operator("advanced.face_fill", text="Fill Face", icon='FACESEL')
        col.operator("advanced.Knife", text="Knife Tool", icon='SCULPTMODE_HLT')


class ADVANCED_PT_geometric_tools(bpy.types.Panel):
    """Geometric manipulation panel"""
    bl_label = "Geometric Tools"
    bl_idname = "ADVANCED_PT_geometric_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Modeling"

    def draw(self, context):
        layout = self.layout
        
        # Section: Modifiers
        box = layout.box()
        box.label(text="Modifiers", icon='MODIFIER_DATA')
        col = box.column(align=True)
        col.operator("advanced.add_modifier", text="Add Modifier", icon='ADD')
        col.operator("advanced.remove_modifier", text="Remove Modifier", icon='REMOVE')
        col.operator("advanced.apply_modifier", text="Apply Modifier", icon='APPLY')
        
        # Subdivision
        box = layout.box()
        box.label(text="Subdivision", icon='MOD_SUBDIVSURF')
        col = box.column(align=True)
        col.operator("advanced.subdivision_surface", text="Add Subdivision", icon='MOD_SUBDIVSURF')
        
        # Mirror
        box = layout.box()
        box.label(text="Mirror", icon='MOD_MIRROR')
        col = box.column(align=True)
        col.operator("advanced.mirror", text="Add Mirror", icon='MOD_MIRROR')


class ADVANCED_PT_selection_tools(bpy.types.Panel):
    """Selection tools panel"""
    bl_label = "Selection Tools"
    bl_idname = "ADVANCED_PT_selection_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Modeling"

    def draw(self, context):
        layout = self.layout
        
        # Selection actions
        box = layout.box()
        box.label(text="Selection", icon='SELECT_MODE')
        col = box.column(align=True)
        col.operator("advanced.select_all", text="Select All", icon='FULLSCREEN')
        col.operator("advanced.select_random", text="Select Random", icon='MAT_FLUID')
        col.operator("advanced.select_linked", text="Select Linked", icon='LINK_BLEND')
        
        # Selection by type
        box = layout.box()
        box.label(text="Select By Type", icon='RESTRICT_SELECT_OFF')
        col = box.column(align=True)
        col.operator("advanced.select_by_type", text="Select Vertices", icon='VERTEXSEL').select_type = 'VERT'
        col.operator("advanced.select_by_type", text="Select Edges", icon='EDGESEL').select_type = 'EDGE'
        col.operator("advanced.select_by_type", text="Select Faces", icon='FACESEL').select_type = 'FACE'
        
        # Merge/Split operations
        box = layout.box()
        box.label(text="Merge & Split", icon='EDITMODE_HLT')
        col = box.column(align=True)
        col.operator("advanced.merge", text="Merge Vertices", icon='MESH_SPHERE')
        col.operator("advanced.split", text="Split Selection", icon='UNLINKED')


class ADVANCED_PT_transform_tools(bpy.types.Panel):
    """Transform tools panel"""
    bl_label = "Transform Tools"
    bl_idname = "ADVANCED_PT_transform_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Modeling"

    def draw(self, context):
        layout = self.layout
        
        # Transform operations
        box = layout.box()
        box.label(text="Transform", icon='TRANSLATE')
        col = box.column(align=True)
        col.operator("advanced.transform_copy", text="Copy Transform", icon='COPYDOWN')
        col.operator("advanced.transform_paste", text="Paste Transform", icon='PASTEDOWN')
        col.operator("advanced.transform_apply", text="Apply Transform", icon='MOD_TRANSFORM')
        col.operator("advanced.transform_reset", text="Reset Transform", icon='UNLINKED')
        
        # Snap operations
        box = layout.box()
        box.label(text="Snap", icon='SNAP_ON')
        col = box.column(align=True)
        col.operator("advanced.snap_selection", text="Snap to Cursor", icon='LIGHT')
        
        # View alignment
        box = layout.box()
        box.label(text="View", icon='VIEW3D')
        col = box.column(align=True)
        col.operator("advanced.align_view", text="Align View to Selection", icon='ALIGN_LEFT')
