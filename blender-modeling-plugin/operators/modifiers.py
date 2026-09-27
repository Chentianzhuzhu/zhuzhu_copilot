"""
Modifier operators based on Blender API
"""
import bpy


class ADVANCED_OT_add_modifier(bpy.types.Operator):
    """Add a modifier to the active object"""
    bl_idname = "advanced.add_modifier"
    bl_label = "Add Modifier"
    bl_options = {'REGISTER', 'UNDO'}

    modifier_type: bpy.props.EnumProperty(
        items=[
            ('SUBDIVISION', 'Subdivision Surface', 'Smooth subdivision'),
            ('BEVEL', 'Bevel', 'Bevel edges'),
            ('SIMPLIFY', 'Simplify', 'Reduce complexity'),
            ('SMOOTH', 'Smooth', 'Smooth deformation'),
            ('CAST', 'Cast', 'Cast deformation'),
            ('CURVE', 'Curve', 'Curve deformation'),
            ('LATTICE', 'Lattice', 'Lattice deformation'),
            ('MIRROR', 'Mirror', 'Symmetry mirror'),
            ('ARRAY', 'Array', 'Duplicate array'),
            ('BOOLEAN', 'Boolean', 'Boolean operation'),
            ('SHRINKWRAP', 'Shrinkwrap', 'Project to surface'),
            ('FLUID', 'Fluid', 'Fluid simulation'),
        ],
        description="Modifier type to add"
    )

    modifier_name: bpy.props.StringProperty(
        name="Name",
        description="Name of the modifier",
        default=""
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        name = self.modifier_name or self.modifier_type
        modifier = obj.modifiers.new(name=name, type=self.modifier_type)
        
        # Set default values based on type
        if self.modifier_type == 'SUBDIVISION':
            modifier.levels = 2
            modifier.render_levels = 3
            modifier.subdivision_type = 'CATMULL_CLARK'
        elif self.modifier_type == 'BEVEL':
            modifier.width = 0.01
            modifier.segments = 5
        elif self.modifier_type == 'MIRROR':
            modifier.use_axis[0] = True
            modifier.use_axis[1] = True
            modifier.use_axis[2] = True
        
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "modifier_type")
        self.layout.prop(self, "modifier_name")


class ADVANCED_OT_remove_modifier(bpy.types.Operator):
    """Remove a modifier"""
    bl_idname = "advanced.remove_modifier"
    bl_label = "Remove Modifier"
    bl_options = {'REGISTER', 'UNDO'}

    modifier_index: bpy.props.IntProperty(
        name="Index",
        description="Index of modifier to remove",
        default=0
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        if 0 <= self.modifier_index < len(obj.modifiers):
            obj.modifiers.remove(obj.modifiers[self.modifier_index])
            return {'FINISHED'}
        
        self.report({'ERROR'}, "Invalid modifier index")
        return {'CANCELLED'}


class ADVANCED_OT_apply_modifier(bpy.types.Operator):
    """Apply a modifier"""
    bl_idname = "advanced.apply_modifier"
    bl_label = "Apply Modifier"
    bl_options = {'REGISTER', 'UNDO'}

    modifier_index: bpy.props.IntProperty(
        name="Index",
        description="Index of modifier to apply",
        default=0
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        if 0 <= self.modifier_index < len(obj.modifiers):
            modifier = obj.modifiers[self.modifier_index]
            bpy.ops.object.modifier_apply(modifier=modifier.name)
            return {'FINISHED'}
        
        self.report({'ERROR'}, "Invalid modifier index")
        return {'CANCELLED'}


class ADVANCED_OT_subdivision_surface(bpy.types.Operator):
    """Add subdivision surface modifier"""
    bl_idname = "advanced.subdivision_surface"
    bl_label = "Subdivision Surface"
    bl_options = {'REGISTER', 'UNDO'}

    levels: bpy.props.IntProperty(name="Viewport Levels", default=2, min=0, max=10)
    render_levels: bpy.props.IntProperty(name="Render Levels", default=3, min=0, max=10)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        modifier = obj.modifiers.new(name="Subdivision", type='SUBSURF')
        modifier.levels = self.levels
        modifier.render_levels = self.render_levels
        modifier.subdivision_type = 'CATMULL_CLARK'
        
        return {'FINISHED'}


class ADVANCED_OT_mirror(bpy.types.Operator):
    """Add mirror modifier"""
    bl_idname = "advanced.mirror"
    bl_label = "Mirror"
    bl_options = {'REGISTER', 'UNDO'}

    use_x: bpy.props.BoolProperty(name="X Axis", default=True)
    use_y: bpy.props.BoolProperty(name="Y Axis", default=False)
    use_z: bpy.props.BoolProperty(name="Z Axis", default=False)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        modifier = obj.modifiers.new(name="Mirror", type='MIRROR')
        modifier.use_axis[0] = self.use_x
        modifier.use_axis[1] = self.use_y
        modifier.use_axis[2] = self.use_z
        
        return {'FINISHED'}
