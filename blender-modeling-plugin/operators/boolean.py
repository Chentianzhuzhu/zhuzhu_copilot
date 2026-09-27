"""
Boolean operation operators based on Blender API
"""
import bpy
import bmesh


class ADVANCED_OT_mesh_boolean(bpy.types.Operator):
    """Apply boolean operation"""
    bl_idname = "advanced.mesh_boolean"
    bl_label = "Boolean Operation"
    bl_options = {'REGISTER', 'UNDO'}

    operation: bpy.props.EnumProperty(
        items=[
            ('DIFFERENCE', 'Difference', 'Subtract union from target'),
            ('UNION', 'Union', 'Combine all meshes'),
            ('INTERSECT', 'Intersect', 'Keep only overlapping areas'),
        ],
        description="Boolean operation type"
    )

    target: bpy.props.StringProperty(
        name="Target",
        description="Target object for boolean operation",
        default=""
    )

    modifier_name: bpy.props.StringProperty(
        name="Modifier Name",
        description="Name of the boolean modifier",
        default="Boolean"
    )

    def execute(self, context):
        if not context.active_object:
            self.report({'ERROR'}, "No active object selected")
            return {'CANCELLED'}

        obj = context.active_object
        modifier = obj.modifiers.new(name=self.modifier_name, type='BOOLEAN')
        
        if self.target:
            modifier.object = bpy.data.objects.get(self.target)
            if not modifier.object:
                self.report({'ERROR'}, f"Target object '{self.target}' not found")
                return {'CANCELLED'}
        else:
            # Use selection
            selected = [o for o in context.selected_objects if o != obj and o.type == 'MESH']
            if selected:
                modifier.object = selected[0]
            else:
                self.report({'ERROR'}, "No target object selected")
                return {'CANCELLED'}

        modifier.operation = self.operation.lower()
        
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "operation")
        self.layout.prop(self, "target")
        self.layout.prop(self, "modifier_name")


class ADVANCED_OT_mesh_boolean_cut(ADVANCED_OT_mesh_boolean):
    """Cut with boolean difference"""
    bl_idname = "advanced.mesh_boolean_cut"
    bl_label = "Boolean Cut"

    def invoke(self, context, event):
        self.operation = 'DIFFERENCE'
        return super().invoke(context, event)


class ADVANCED_OT_mesh_boolean_union(ADVANCED_OT_mesh_boolean):
    """Union boolean operation"""
    bl_idname = "advanced.mesh_boolean_union"
    bl_label = "Boolean Union"

    def invoke(self, context, event):
        self.operation = 'UNION'
        return super().invoke(context, event)


class ADVANCED_OT_mesh_boolean_intersection(ADVANCED_OT_mesh_boolean):
    """Intersect boolean operation"""
    bl_idname = "advanced.mesh_boolean_intersection"
    bl_label = "Boolean Intersect"

    def invoke(self, context, event):
        self.operation = 'INTERSECT'
        return super().invoke(context, event)
