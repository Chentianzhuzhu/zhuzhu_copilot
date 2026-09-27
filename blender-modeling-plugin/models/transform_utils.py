"""
Transform utility functions
"""
import bpy
from mathutils import Vector, Matrix, Euler


def copy_transform(source, target):
    """Copy transform from source to target object"""
    if source is None or target is None:
        return False
    
    target.location = source.location.copy()
    target.rotation_euler = source.rotation_euler.copy()
    target.scale = source.scale.copy()
    
    return True


def apply_transform(obj, matrix=None):
    """Apply transform matrix to object"""
    if obj is None:
        return False
    
    if matrix is not None:
        obj.matrix_world = matrix @ obj.matrix_world
    
    obj.data.transform(matrix or Matrix())
    return True


def reset_transform(obj):
    """Reset object transform to defaults"""
    if obj is None:
        return False
    
    obj.location = Vector((0.0, 0.0, 0.0))
    obj.rotation_euler = Euler((0.0, 0.0, 0.0))
    obj.scale = Vector((1.0, 1.0, 1.0))
    return True


def align_to_rotation(obj, rotation):
    """Align object rotation to given euler angles"""
    if obj is None:
        return False
    
    obj.rotation_euler = rotation
    return True


def align_to_location(obj, location):
    """Align object location to given vector"""
    if obj is None:
        return False
    
    obj.location = location
    return True


def align_to_scale(obj, scale):
    """Align object scale to given vector"""
    if obj is None:
        return False
    
    obj.scale = scale
    return True


def get_world_matrix(obj):
    """Get world transformation matrix"""
    if obj is None:
        return Matrix.Identity(4)
    return obj.matrix_world.copy()


def set_world_matrix(obj, matrix):
    """Set world transformation matrix"""
    if obj is None:
        return False
    obj.matrix_world = matrix
    return True
