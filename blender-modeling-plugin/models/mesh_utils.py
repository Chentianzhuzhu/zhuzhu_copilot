"""
Mesh utility functions
"""
import bpy
import bmesh
from mathutils import Vector, Matrix


def create_bmesh_from_primitive(type_name, size=1.0):
    """Create a BMesh from primitive type"""
    bm = bmesh.new()
    
    if type_name == 'BOX':
        bmesh.ops.create_cube(bm, size=size)
    elif type_name == 'SPHERE':
        bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=size/2)
    elif type_name == 'CYLINDER':
        bmesh.ops.create_cylinder(bm, radius=size/2, depth=size, segments=32)
    elif type_name == 'CONE':
        bmesh.ops.create_cone(bm, cap_ends=True, radius1=size/2, radius2=0.0, depth=size, segments=32)
    elif type_name == 'TORUS':
        bmesh.ops.create_torus(bm, major_radius=size*0.6, minor_radius=size*0.2, major_segments=32, minor_segments=16)
    elif type_name == 'PLANE':
        bmesh.ops.create_grid(bm, x_size=size*2, y_size=size*2, size=size)
    elif type_name == 'CIRCLE':
        bmesh.ops.create_circle(bm, cap_ends=True, radius=size/2, segments=32)
    elif type_name == 'MONKEY':
        # Use Blender's built-in monkey mesh
        bpy.ops.mesh.primitive_monkey_add(size=size, location=(0, 0, 0))
        bm = None
    
    return bm


def get_mesh_loose_elements(obj):
    """Get loose geometry (unconnected vertices/edges/faces)"""
    if obj.type != 'MESH':
        return []
    
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.mesh.loose_elements()
    
    mesh = obj.data
    loose_verts = [v for v in mesh.vertices if v.select]
    
    bpy.ops.object.mode_set(mode='OBJECT')
    return loose_verts


def merge_by_distance(obj, threshold=0.0001):
    """Merge vertices by distance"""
    if obj.type != 'MESH':
        return
    
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.merge(type='DISTANCE', threshold=threshold)
    bpy.ops.object.mode_set(mode='OBJECT')


def subdivide(obj, number_of_cuts=1, use_quad_corner_cut=False):
    """Subdivide mesh faces"""
    if obj.type != 'MESH':
        return
    
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.subdivide(number_of_cuts=number_of_cuts, quadcorner=use_quad_corner_cut)
    bpy.ops.object.mode_set(mode='OBJECT')


def add_noise(obj, strength=0.5, seed=0):
    """Add noise to mesh vertices"""
    if obj.type != 'MESH':
        return
    
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.noise(offset_fac=strength, depth_fac=0.0, amplitude=strength, noise=seed)
    bpy.ops.object.mode_set(mode='OBJECT')
