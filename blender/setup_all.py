import bpy
import math
import os

# Create armature for the cat
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

bpy.ops.object.mode_set(mode='OBJECT')

# Add armature modifier to cat parts
cat_objects = ['Body_Group', 'Head', 'Ear_Left', 'Ear_Right', 'Tail']
for obj_name in cat_objects:
    if obj_name in bpy.data.objects:
        obj = bpy.data.objects[obj_name]
        mod = obj.modifiers.new(name='Armature', type='ARMATURE')
        mod.object = arm
        
        if obj_name == 'Body_Group':
            vg = obj.vertex_groups.new(name='Spine')
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

# Add armature modifier to paws
for paw_name in ['Paw_Left', 'Paw_Right']:
    if paw_name in bpy.data.objects:
        obj = bpy.data.objects[paw_name]
        mod = obj.modifiers.new(name='Armature', type='ARMATURE')
        mod.object = arm
        vg = obj.vertex_groups.new(name='Tail_Base')
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
        obj.vertex_groups.active = vg
        bpy.ops.paint.weight_from_layout(mode='AFFECT_GEOMETRY')
        bpy.ops.object.mode_set(mode='OBJECT')

print('Skeleton setup complete!')

# ============ ANIMATION ============
scene = bpy.context.scene
scene.frame_start = 1
scene.frame_end = 60
scene.render.fps = 24

def set_bone_rotation(bone_name, rot_x, rot_y, rot_z, frame):
    if bone_name in arm.pose.bones:
        bone = arm.pose.bones[bone_name]
        bone.rotation_euler = (rot_x, rot_y, rot_z)
        bone.keyframe_insert(data_path="rotation_euler", frame=frame)

def set_bone_location(bone_name, loc_x, loc_y, loc_z, frame):
    if bone_name in arm.pose.bones:
        bone = arm.pose.bones[bone_name]
        bone.location = (loc_x, loc_y, loc_z)
        bone.keyframe_insert(data_path="location", frame=frame)

# Keyboard typing animation
for frame in range(1, 61):
    if frame % 4 == 0:
        set_bone_location('Tail_Base', -0.25, 0.35, 0.45 + 0.08, frame)
    else:
        set_bone_location('Tail_Base', -0.25, 0.35, 0.45, frame)
    
    if frame % 4 == 2:
        set_bone_location('Tail_Tip', 0.25, 0.35, 0.45 + 0.08, frame)
    else:
        set_bone_location('Tail_Tip', 0.25, 0.35, 0.45, frame)

# Head following mouse animation
mouse_empty = None
for frame in range(1, 61):
    x = 0.5 * math.sin(frame * 0.1)
    y = 0.5 * math.cos(frame * 0.15)
    z = 1.3
    
    if frame == 1:
        bpy.ops.object.add(type='EMPTY', location=(x, y, z))
        mouse_empty = bpy.context.active_object
        mouse_empty.name = 'MouseCursor'
    
    mouse_empty.location = (x, y, z)
    
    follow_x = x * 0.3
    follow_y = y * 0.3
    rot_x = follow_y * 0.5
    rot_y = follow_x * 0.8
    set_bone_rotation('Head', rot_x, rot_y, 0, frame)
    set_bone_rotation('Spine', 0, follow_x * 0.3, 0, frame)

# Tail wagging animation
for frame in range(1, 61):
    tail_rot_z = 0.2 * math.sin(frame * 0.3)
    tail_rot_x = 0.1 * math.sin(frame * 0.2)
    set_bone_rotation('Tail_Base', tail_rot_x, 0, tail_rot_z, frame)
    set_bone_rotation('Tail_Tip', tail_rot_x * 1.5, 0, tail_rot_z * 1.5, frame)

# Ear twitching
for frame in range(1, 61):
    if frame % 10 == 0:
        set_bone_rotation('L_Ear', 0.1, 0, 0.1, frame)
    else:
        set_bone_rotation('L_Ear', 0, 0, 0, frame)
    
    if frame % 12 == 0:
        set_bone_rotation('R_Ear', 0.1, 0, -0.1, frame)
    else:
        set_bone_rotation('R_Ear', 0, 0, 0, frame)

print('Animation created successfully!')
print('Timeline: 1-60 frames at 24 FPS')

# Save blend file
save_path = os.path.join(os.path.dirname(__file__), 'kitty_working.blend')
bpy.ops.wm.save_as_mainfile(filepath=save_path, check_existing=True)
print(f'Saved to: {save_path}')
