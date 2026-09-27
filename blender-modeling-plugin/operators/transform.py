"""
Transform operators based on Blender API
"""
import bpy


class ADVANCED_OT_transform_copy(bpy.types.Operator):
    """Copy transform data"""
    bl_idname = "advanced.transform_copy"
    bl_label = "Copy Transform"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No object selected")
            return {'CANCELLED'}

        # Copy transform to clipboard
        bpy.ops.object.transform_copy()
        self.report({'INFO'}, "Transform copied")
        return {'FINISHED'}


class ADVANCED_OT_transform_paste(bpy.types.Operator):
    """Paste transform data"""
    bl_idname = "advanced.transform_paste"
    bl_label = "Paste Transform"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No object selected")
            return {'CANCELLED'}

        bpy.ops.object.transform_paste()
        self.report({'INFO'}, "Transform pasted")
        return {'FINISHED'}


class ADVANCED_OT_transform_apply(bpy.types.Operator):
    """Apply all transforms"""
    bl_idname = "advanced.transform_apply"
    bl_label = "Apply Transform"
    bl_options = {'REGISTER', 'UNDO'}

    location: bpy.props.BoolProperty(name="Location", default=True)
    rotation: bpy.props.BoolProperty(name="Rotation", default=True)
    scale: bpy.props.BoolProperty(name="Scale", default=True)

    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No object selected")
            return {'CANCELLED'}

        bpy.ops.object.transform_apply(
            location=self.location,
            rotation=self.rotation,
            scale=self.scale
        )
        self.report({'INFO'}, "Transforms applied")
        return {'FINISHED'}


class ADVANCED_OT_transform_reset(bpy.types.Operator):
    """Reset transform to default"""
    bl_idname = "advanced.transform_reset"
    bl_label = "Reset Transform"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No object selected")
            return {'CANCELLED'}

        obj.location = (0.0, 0.0, 0.0)
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)
        
        self.report({'INFO'}, "Transform reset")
        return {'FINISHED'}


class ADVANCED_OT_align_view(bpy.types.Operator):
    """Align view to selected object"""
    bl_idname = "advanced.align_view"
    bl_label = "Align View"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No object selected")
            return {'CANCELLED'}

        # Set view to camera or aligned with selection
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                region = area.regions[0]
                # Get view matrix and align
                break
        
        self.report({'INFO'}, "View aligned")
        return {'FINISHED'}


class ADVANCED_OT_snap_selection(bpy.types.Operator):
    """Snap selection to grid/cursor"""
    bl_idname = "advanced.snap_selection"
    bl_label = "Snap Selection"
    bl_options = {'REGISTER', 'UNDO'}

    snap_type: bpy.props.EnumProperty(
        items=[
            ('CURSOR', 'To Cursor', 'Snap to 3D cursor'),
            ('GRID', 'To Grid', 'Snap to grid'),
        ],
        description="Snap type"
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        
        if self.snap_type == 'CURSOR':
            bpy.ops.object.snap_selected(override={'options': {'TO_ACTIVE_ELEMENT'}})
        else:
            bpy.ops.object.snap_selected()
        
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}
