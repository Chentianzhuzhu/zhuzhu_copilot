import bpy
import os

# Clear existing armatures
if 'CatArmature' in bpy.data.objects:
    bpy.data.objects.remove(bpy.data.objects['CatArmature'])

# Create new armature
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.object.select_all(action='DESELECT')
bpy.ops.object.add(type='ARMATURE', location=(0, 0.8, 0.5))
arm = bpy.context.active_object
arm.name = 'CatArmature'
bpy.context.view_layer.objects.active = arm

# Enter edit mode to create bones
bpy.ops.object.mode_set(mode='EDIT')

# Define bone structure
bones = [
    ('Root', (0, 0, 0), (0, 0, 0.3)),
    ('Spine', (0, 0, 0.3), (0, 0, 0.5)),
    ('Neck', (0, 0, 0.8), (0, 0, 0.3)),
    ('Head', (0, 0, 1.1), (0, 0, 0.3)),
    ('L_Ear', (-0.2, 0, 1.4), (0, 0, 0.2)),
    ('R_Ear', (0.2, 0, 1.4), (0, 0, 0.2)),
    ('Tail_Base', (0, -0.3, 0.6), (0, 0, 0.3)),
    ('Tail_Tip', (0, -0.3, 0.9), (0, 0, 0.2)),
]

for bone_name, head, tail in bones:
    bone = arm.data.edit_bones.new(bone_name)
    bone.head = head
    bone.tail = tail

# Set bone hierarchy
bone_map = {b.name: b for b in arm.data.edit_bones}
bone_map['Head'].parent = bone_map['Neck']
bone_map['Neck'].parent = bone_map['Spine']
bone_map['L_Ear'].parent = bone_map['Head']
bone_map['R_Ear'].parent = bone_map['Head']
bone_map['Tail_Base'].parent = bone_map['Spine']
bone_map['Tail_Tip'].parent = bone_map['Tail_Base']

# Exit edit mode
bpy.ops.object.mode_set(mode='OBJECT')

# Parent body parts to bones using vertex groups
cat_objects = ['Body_Group', 'Head', 'Ear_Left', 'Ear_Right', 'Tail']
for obj_name in cat_objects:
    if obj_name in bpy.data.objects:
        obj = bpy.data.objects[obj_name]
        # Add armature modifier
        mod = obj.modifiers.new(name='Armature', type='ARMATURE')
        mod.object = arm
        
        # Create vertex groups and assign
        if obj_name == 'Body_Group':
            vg = obj.vertex_groups.new(name='Spine')
            # Select all vertices and assign to Spine bone
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
            obj.vertex_groups.active = vg
            bpy.ops.paint.weight_from_layout(mode='AFFECT_GEOMETRY')
            bpy.ops.object.mode_set(mode='OBJECT')
        elif obj_name == 'Head':
            vg = obj.vertex_groups.new(name='Head')
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
            obj.vertex_groups.active = vg
            bpy.ops.paint.weight_from_layout(mode='AFFECT_GEOMETRY')
            bpy.ops.object.mode_set(mode='OBJECT')
        elif obj_name in ['Ear_Left', 'Ear_Right']:
            vg = obj.vertex_groups.new(name='L_Ear' if obj_name == 'Ear_Left' else 'R_Ear')
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
            obj.vertex_groups.active = vg
            bpy.ops.paint.weight_from_layout(mode='AFFECT_GEOMETRY')
            bpy.ops.object.mode_set(mode='OBJECT')
        elif obj_name == 'Tail':
            vg = obj.vertex_groups.new(name='Tail_Base')
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
            obj.vertex_groups.active = vg
            bpy.ops.paint.weight_from_layout(mode='AFFECT_GEOMETRY')
            bpy.ops.object.mode_set(mode='OBJECT')

print('Skeleton setup complete!')
print('Bones:', [b.name for b in arm.data.bones])
