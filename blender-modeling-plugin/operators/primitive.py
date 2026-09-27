"""
Basic primitive creation operators based on Blender API
"""
import bpy
import bmesh
from mathutils import Vector
from ..models.mesh_utils import create_bmesh_from_primitive


class ADVANCED_OT_primitive_add(bpy.types.Operator):
    """Add a new primitive mesh object"""
    bl_idname = "advanced.primitive_add"
    bl_label = "Add Primitive"
    bl_options = {'REGISTER', 'UNDO'}

    type: bpy.props.EnumProperty(
        items=[
            ('MONKEY', 'Monkey', 'Add a Suzanne monkey'),
            ('SPHERE', 'Sphere', 'Add a sphere'),
            ('BOX', 'Box', 'Add a cube'),
            ('CYLINDER', 'Cylinder', 'Add a cylinder'),
            ('CONE', 'Cone', 'Add a cone'),
            ('TORUS', 'Torus', 'Add a torus'),
            ('PLANE', 'Plane', 'Add a plane'),
            ('CIRCLE', 'Circle', 'Add a circle'),
        ],
        description='Primitive type to add',
        default='BOX'
    )

    size: bpy.props.FloatProperty(
        name="Size",
        description="Size of the primitive",
        default=2.0,
        min=0.001,
        max=1000.0
    )

    location: bpy.props.FloatVectorProperty(
        name="Location",
        description="Location of the primitive",
        default=(0.0, 0.0, 0.0),
        subtype='TRANSLATION'
    )

    rotation: bpy.props.FloatVectorProperty(
        name="Rotation",
        description="Rotation in radians",
        default=(0.0, 0.0, 0.0),
        subtype='EULER'
    )

    def execute(self, context):
        obj = create_primitive(self.type, self.size, self.location, self.rotation)
        if obj:
            context.collection.objects.link(obj)
            context.view_layer.objects.active = obj
            obj.select_set(True)
            return {'FINISHED'}
        return {'CANCELLED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "type")
        self.layout.prop(self, "size")
        self.layout.prop(self, "location")
        self.layout.prop(self, "rotation")


def create_primitive(primitive_type, size=2.0, location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0)):
    """Create a primitive mesh object"""
    bpy.ops.object.mode_set(mode='OBJECT')
    
    if primitive_type == 'BOX':
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    elif primitive_type == 'SPHERE':
        bpy.ops.mesh.primitive_uv_sphere_add(
            size=size, 
            location=location,
            segments=32,
            ring_count=16
        )
    elif primitive_type == 'CYLINDER':
        bpy.ops.mesh.primitive_cylinder_add(
            radius=size/2,
            depth=size,
            location=location
        )
    elif primitive_type == 'CONE':
        bpy.ops.mesh.primitive_cone_add(
            radius1=size/2,
            depth=size,
            location=location
        )
    elif primitive_type == 'TORUS':
        bpy.ops.mesh.primitive_torus_add(
            location=location,
            major_radius=size*0.6,
            minor_radius=size*0.2
        )
    elif primitive_type == 'PLANE':
        bpy.ops.mesh.primitive_plane_add(
            size=size*2,
            location=location
        )
    elif primitive_type == 'CIRCLE':
        bpy.ops.mesh.primitive_circle_add(
            radius=size/2,
            location=location
        )
    elif primitive_type == 'MONKEY':
        bpy.ops.object.shrinkwrap_add(location=location)
        # Note: Monkey is available via specific Blender version addons
    
    obj = context.active_object
    if obj and rotation != (0.0, 0.0, 0.0):
        obj.rotation_euler = rotation
    
    return obj


class ADVANCED_OT_box(bpy.types.Operator):
    """Add a box"""
    bl_idname = "advanced.box"
    bl_label = "Add Box"
    bl_options = {'REGISTER', 'UNDO'}

    size: bpy.props.FloatProperty(name="Size", default=2.0, min=0.001)
    location: bpy.props.FloatVectorProperty(name="Location", default=(0,0,0), subtype='TRANSLATION')
    rotation: bpy.props.FloatVectorProperty(name="Rotation", default=(0,0,0), subtype='EULER')

    def execute(self, context):
        obj = create_primitive('BOX', self.size, self.location, self.rotation)
        if obj:
            context.collection.objects.link(obj)
            context.view_layer.objects.active = obj
            obj.select_set(True)
            return {'FINISHED'}
        return {'CANCELLED'}


class ADVANCED_OT_cylinder(bpy.types.Operator):
    """Add a cylinder"""
    bl_idname = "advanced.cylinder"
    bl_label = "Add Cylinder"
    bl_options = {'REGISTER', 'UNDO'}

    radius: bpy.props.FloatProperty(name="Radius", default=1.0, min=0.001)
    depth: bpy.props.FloatProperty(name="Depth", default=2.0, min=0.001)
    location: bpy.props.FloatVectorProperty(name="Location", default=(0,0,0), subtype='TRANSLATION')

    def execute(self, context):
        bpy.ops.mesh.primitive_cylinder_add(
            radius=self.radius,
            depth=self.depth,
            location=self.location
        )
        obj = context.active_object
        if obj:
            context.collection.objects.link(obj)
            context.view_layer.objects.active = obj
            obj.select_set(True)
            return {'FINISHED'}
        return {'CANCELLED'}


class ADVANCED_OT_sphere(bpy.types.Operator):
    """Add a sphere"""
    bl_idname = "advanced.sphere"
    bl_label = "Add Sphere"
    bl_options = {'REGISTER', 'UNDO'}

    size: bpy.props.FloatProperty(name="Size", default=1.0, min=0.001)
    segments: bpy.props.IntProperty(name="Segments", default=32, min=3, max=128)
    ring_count: bpy.props.IntProperty(name="Rings", default=16, min=2, max=64)
    location: bpy.props.FloatVectorProperty(name="Location", default=(0,0,0), subtype='TRANSLATION')

    def execute(self, context):
        bpy.ops.mesh.primitive_uv_sphere_add(
            size=self.size,
            location=self.location,
            segments=self.segments,
            ring_count=self.ring_count
        )
        obj = context.active_object
        if obj:
            context.collection.objects.link(obj)
            context.view_layer.objects.active = obj
            obj.select_set(True)
            return {'FINISHED'}
        return {'CANCELLED'}
