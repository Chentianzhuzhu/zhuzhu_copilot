"""
Selection operators based on Blender API
"""
import bpy


class ADVANCED_OT_select_all(bpy.types.Operator):
    """Select all vertices/edges/faces"""
    bl_idname = "advanced.select_all"
    bl_label = "Select All"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(
        items=[
            ('SELECT', 'Select', 'Select all'),
            ('DESELECT', 'Deselect', 'Deselect all'),
            ('INVERT', 'Invert', 'Invert selection'),
        ],
        description="Selection action"
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        
        if self.action == 'SELECT':
            bpy.ops.mesh.select_all(action='SELECT')
        elif self.action == 'DESELECT':
            bpy.ops.mesh.select_all(action='DESELECT')
        elif self.action == 'INVERT':
            bpy.ops.mesh.select_all(action='INVERT')
        
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}


class ADVANCED_OT_select_by_type(bpy.types.Operator):
    """Select by vertex/edge/face type"""
    bl_idname = "advanced.select_by_type"
    bl_label = "Select by Type"
    bl_options = {'REGISTER', 'UNDO'}

    select_type: bpy.props.EnumProperty(
        items=[
            ('VERT', 'Vertex', 'Select vertices'),
            ('EDGE', 'Edge', 'Select edges'),
            ('FACE', 'Face', 'Select faces'),
        ],
        description="Selection type"
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type=self.select_type.lower())
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}


class ADVANCED_OT_select_by_material(bpy.types.Operator):
    """Select by material"""
    bl_idname = "advanced.select_by_material"
    bl_label = "Select by Material"
    bl_options = {'REGISTER', 'UNDO'}

    material_index: bpy.props.IntProperty(
        name="Material Index",
        description="Index of material to select",
        default=0,
        min=0
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        if self.material_index >= len(obj.data.materials):
            self.report({'ERROR'}, f"Material index {self.material_index} out of range")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        
        # Deselect all first
        bpy.ops.mesh.select_all(action='DESELECT')
        
        # Select faces with this material
        bpy.ops.object.material_slot_select()
        
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}


class ADVANCED_OT_select_random(bpy.types.Operator):
    """Select random vertices/edges/faces"""
    bl_idname = "advanced.select_random"
    bl_label = "Select Random"
    bl_options = {'REGISTER', 'UNDO'}

    percentage: bpy.props.FloatProperty(
        name="Percentage",
        description="Percentage of elements to select",
        default=10.0,
        min=0.0,
        max=100.0
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        
        bpy.ops.mesh.select_all(action='DESELECT')
        bpy.ops.mesh.select_random(percentage=self.percentage / 100.0)
        
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}


class ADVANCED_OT_select_linked(bpy.types.Operator):
    """Select linked geometry"""
    bl_idname = "advanced.select_linked"
    bl_label = "Select Linked"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_linked()
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}
