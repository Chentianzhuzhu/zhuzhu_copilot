import bpy
import math
import os

# Clear all objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

# Create floor
bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0.5, -0.01))
floor = bpy.context.active_object
floor.name = 'Floor'

# Create cat body parts
# Body
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0.8, 0.5))
body = bpy.context.active_object
body.name = 'Body'

# Head
bpy.ops.mesh.primitive_cube_add(size=0.7, location=(0, 0.8, 1.3))
head = bpy.context.active_object
head.name = 'Head'

# Ears (cones)
bpy.ops.mesh.primitive_cone_add(vertices=3, radius1=0.1, radius2=0, depth=0.2, location=(-0.25, 0.8, 1.55))
ear_l = bpy.context.active_object
ear_l.name = 'Ear_Left'

bpy.ops.mesh.primitive_cone_add(vertices=3, radius1=0.1, radius2=0, depth=0.2, location=(0.25, 0.8, 1.55))
ear_r = bpy.context.active_object
ear_r.name = 'Ear_Right'

# Tail
bpy.ops.mesh.primitive_cube_add(size=0.15, location=(0, -0.5, 0.8))
tail = bpy.context.active_object
tail.name = 'Tail'
tail.scale = (1, 1, 1.5)
tail.rotation_euler = (0, 0, 0.52)

# Legs
positions = [(-0.3, 0.6), (0.3, 0.6), (-0.35, 0.1), (0.35, 0.1)]
for i, (x, y) in enumerate(positions):
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(x, y, 0.2))
    leg = bpy.context.active_object
    leg.name = f'Leg_{i}'
    leg.scale = (0.25, 0.25, 0.6)

# Paws
bpy.ops.mesh.primitive_cube_add(size=0.12, location=(-0.2, 0.35, 0.45))
paw_l = bpy.context.active_object
paw_l.name = 'Paw_Left'
paw_l.scale = (0.15, 0.12, 0.12)

bpy.ops.mesh.primitive_cube_add(size=0.12, location=(0.2, 0.35, 0.45))
paw_r = bpy.context.active_object
paw_r.name = 'Paw_Right'
paw_r.scale = (0.15, 0.12, 0.12)

# Eyes
bpy.ops.mesh.primitive_cube_add(size=0.08, location=(-0.15, 0.8, 1.35))
eye_l = bpy.context.active_object
eye_l.name = 'Eye_Left'
eye_l.scale = (0.08, 0.08, 0.08)

bpy.ops.mesh.primitive_cube_add(size=0.08, location=(0.15, 0.8, 1.35))
eye_r = bpy.context.active_object
eye_r.name = 'Eye_Right'
eye_r.scale = (0.08, 0.08, 0.08)

# Nose
bpy.ops.mesh.primitive_cube_add(size=0.05, location=(0, 0.8, 1.25))
nose = bpy.context.active_object
nose.name = 'Nose'
nose.scale = (0.05, 0.05, 0.05)

# Laptop
bpy.ops.mesh.primitive_cube_add(size=1.2, location=(0, -0.3, 0.05))
laptop_base = bpy.context.active_object
laptop_base.name = 'Laptop_Base'
laptop_base.scale = (1.5, 1.0, 0.05)

bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, -0.8, 0.5))
laptop_screen = bpy.context.active_object
laptop_screen.name = 'Laptop_Screen'
laptop_screen.scale = (1.4, 0.05, 0.9)

# Keyboard
bpy.ops.mesh.primitive_cube_add(size=0.8, location=(0, -0.2, 0.08))
keyboard = bpy.context.active_object
keyboard.name = 'Keyboard'
keyboard.scale = (0.8, 0.5, 0.02)

# ============ MATERIALS (simple approach) ============
def add_material(obj, r, g, b, metallic=0.0, roughness=0.5):
    mat = bpy.data.materials.new(name=obj.name + '_mat')
    mat.use_nodes = True
    # Find the Principled BSDF node by iterating
    for node in mat.node_tree.nodes:
        if 'BSDF' in node.type:
            node.inputs['Base Color'].default_value = (r, g, b, 1)
            node.inputs['Metallic'].default_value = metallic
            node.inputs['Roughness'].default_value = roughness
            break
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat

# Apply materials
add_material(floor, 0.91, 0.91, 0.91, 0, 0.8)
add_material(body, 0.96, 0.96, 0.86, 0, 0.7)
add_material(head, 0.83, 0.64, 0.45, 0, 0.6)
add_material(ear_l, 0.83, 0.64, 0.45, 0, 0.6)
add_material(ear_r, 0.83, 0.64, 0.45, 0, 0.6)
add_material(tail, 0.96, 0.96, 0.86, 0, 0.7)
add_material(paw_l, 0.96, 0.96, 0.86, 0, 0.7)
add_material(paw_r, 0.96, 0.96, 0.86, 0, 0.7)
add_material(laptop_base, 0.75, 0.75, 0.75, 0.8, 0.3)
add_material(laptop_screen, 0.75, 0.75, 0.75, 0.8, 0.3)
add_material(keyboard, 0.2, 0.2, 0.2, 0.8, 0.3)
add_material(eye_l, 0.1, 0.1, 0.1, 0, 0.2)
add_material(eye_r, 0.1, 0.1, 0.1, 0, 0.2)
add_material(nose, 1.0, 0.6, 0.6, 0, 0.5)

# Set all legs to use cat body material
for leg in bpy.context.scene.objects:
    if leg.name.startswith('Leg_'):
        add_material(leg, 0.96, 0.96, 0.86, 0, 0.7)

# ============ BONES ============
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.object.select_all(action='DESELECT')
bpy.ops.object.add(type='ARMATURE', location=(0, 0.8, 0.5))
arm = bpy.context.active_object
arm.name = 'CatArmature'
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='EDIT')

bones_data = [
    ('Root', (0, 0, 0), (0, 0, 0.3)),
    ('Spine', (0, 0, 0.3), (0, 0, 0.5)),
    ('Neck', (0, 0, 0.8), (0, 0, 0.3)),
    ('Head', (0, 0, 1.1), (0, 0, 0.3)),
    ('L_Ear', (-0.2, 0, 1.4), (0, 0, 0.2)),
    ('R_Ear', (0.2, 0, 1.4), (0, 0, 0.2)),
    ('Tail_Base', (0, -0.3, 0.6), (0, 0, 0.3)),
    ('Tail_Tip', (0, -0.3, 0.9), (0, 0, 0.2)),
    ('Paw_L', (-0.25, 0.35, 0.45), (0, 0, 0.1)),
    ('Paw_R', (0.25, 0.35, 0.45), (0, 0, 0.1)),
]

for bone_name, head_pos, tail_pos in bones_data:
    bone = arm.data.edit_bones.new(bone_name)
    bone.head = head_pos
    bone.tail = tail_pos

bone_map = {b.name: b for b in arm.data.edit_bones}
bone_map['Head'].parent = bone_map['Neck']
bone_map['Neck'].parent = bone_map['Spine']
bone_map['L_Ear'].parent = bone_map['Head']
bone_map['R_Ear'].parent = bone_map['Head']
bone_map['Tail_Base'].parent = bone_map['Spine']
bone_map['Tail_Tip'].parent = bone_map['Tail_Base']
bone_map['Paw_L'].parent = bone_map['Spine']
bone_map['Paw_R'].parent = bone_map['Spine']

bpy.ops.object.mode_set(mode='OBJECT')

# Add armature modifier to cat objects
cat_objs = ['Body', 'Head', 'Ear_Left', 'Ear_Right', 'Tail', 'Paw_Left', 'Paw_Right']
for obj_name in cat_objs:
    if obj_name in bpy.data.objects:
        obj = bpy.data.objects[obj_name]
        mod = obj.modifiers.new(name='Armature', type='ARMATURE')
        mod.object = arm

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

# Typing animation
for frame in range(1, 61):
    if frame % 4 == 0:
        set_bone_location('Paw_L', -0.25, 0.35, 0.45 + 0.08, frame)
    else:
        set_bone_location('Paw_L', -0.25, 0.35, 0.45, frame)
    
    if frame % 4 == 2:
        set_bone_location('Paw_R', 0.25, 0.35, 0.45 + 0.08, frame)
    else:
        set_bone_location('Paw_R', 0.25, 0.35, 0.45, frame)

# Head following mouse
for frame in range(1, 61):
    x = 0.5 * math.sin(frame * 0.1)
    y = 0.5 * math.cos(frame * 0.15)
    
    follow_x = x * 0.3
    follow_y = y * 0.3
    rot_x = follow_y * 0.5
    rot_y = follow_x * 0.8
    
    set_bone_rotation('Head', rot_x, rot_y, 0, frame)
    set_bone_rotation('Spine', 0, follow_x * 0.3, 0, frame)

# Tail wagging
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

# ============ LIGHTING ============
bpy.ops.object.select_all(action='DESELECT')
bpy.ops.object.light_add(type='AREA', location=(3, -2, 4))
light = bpy.context.active_object
light.name = 'KeyLight'
light.data.energy = 500
light.data.size = 3

bpy.ops.object.light_add(type='AREA', location=(-3, -2, 3))
fill = bpy.context.active_object
fill.name = 'FillLight'
fill.data.energy = 200
fill.data.size = 2

bpy.ops.object.light_add(type='SUN', location=(0, 5, 5))
sun = bpy.context.active_object
sun.name = 'SunLight'
sun.data.energy = 1.5

# ============ CAMERA ============
bpy.ops.object.camera_add(location=(2.5, -2, 2))
cam = bpy.context.active_object
cam.name = 'Camera'
cam.rotation_euler = (1.1, 0, 0.785)

# Scene settings
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.render.file_format = 'PNG'
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.cycles.samples = 128
scene.cycles.use_denoising = True

# World background
world = bpy.data.worlds['World']
world.node_tree.nodes['Background'].inputs['Color'].default_value = (0.1, 0.1, 0.15, 1)
world.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.5

# Save
save_path = os.path.join(os.path.dirname(__file__), 'kitty_working.blend')
bpy.ops.wm.save_as_mainfile(filepath=save_path)
print('Scene setup complete!')
print(f'Saved to: {save_path}')
