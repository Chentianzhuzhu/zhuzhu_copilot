import bpy
import math
import os

print("Starting scene setup...")

# Clear all objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
print("Cleared all objects")

# Create floor
bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0.5, -0.01))
floor = bpy.context.active_object
floor.name = 'Floor'
print("Created floor")

# Create cat body parts using simpler approach
objects_created = []

# Body
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0.8, 0.5))
body = bpy.context.active_object
body.name = 'Body'
objects_created.append(body)

# Head
bpy.ops.mesh.primitive_cube_add(size=0.7, location=(0, 0.8, 1.3))
head = bpy.context.active_object
head.name = 'Head'
objects_created.append(head)

# Ears
bpy.ops.mesh.primitive_cone_add(vertices=3, radius1=0.1, radius2=0, depth=0.2, location=(-0.25, 0.8, 1.55))
ear_l = bpy.context.active_object
ear_l.name = 'Ear_Left'
objects_created.append(ear_l)

bpy.ops.mesh.primitive_cone_add(vertices=3, radius1=0.1, radius2=0, depth=0.2, location=(0.25, 0.8, 1.55))
ear_r = bpy.context.active_object
ear_r.name = 'Ear_Right'
objects_created.append(ear_r)

# Tail
bpy.ops.mesh.primitive_cube_add(size=0.15, location=(0, -0.5, 0.8))
tail = bpy.context.active_object
tail.name = 'Tail'
tail.scale = (1, 1, 1.5)
tail.rotation_euler = (0, 0, 0.52)
objects_created.append(tail)

# Legs
for i, (x, y) in enumerate([(-0.3, 0.6), (0.3, 0.6), (-0.35, 0.1), (0.35, 0.1)]):
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(x, y, 0.2))
    leg = bpy.context.active_object
    leg.name = f'Leg_{i}'
    leg.scale = (0.25, 0.25, 0.6)
    objects_created.append(leg)

# Paws
bpy.ops.mesh.primitive_cube_add(size=0.12, location=(-0.2, 0.35, 0.45))
paw_l = bpy.context.active_object
paw_l.name = 'Paw_Left'
paw_l.scale = (0.15, 0.12, 0.12)
objects_created.append(paw_l)

bpy.ops.mesh.primitive_cube_add(size=0.12, location=(0.2, 0.35, 0.45))
paw_r = bpy.context.active_object
paw_r.name = 'Paw_Right'
paw_r.scale = (0.15, 0.12, 0.12)
objects_created.append(paw_r)

# Eyes
bpy.ops.mesh.primitive_cube_add(size=0.08, location=(-0.15, 0.8, 1.35))
eye_l = bpy.context.active_object
eye_l.name = 'Eye_Left'
eye_l.scale = (0.08, 0.08, 0.08)
objects_created.append(eye_l)

bpy.ops.mesh.primitive_cube_add(size=0.08, location=(0.15, 0.8, 1.35))
eye_r = bpy.context.active_object
eye_r.name = 'Eye_Right'
eye_r.scale = (0.08, 0.08, 0.08)
objects_created.append(eye_r)

# Nose
bpy.ops.mesh.primitive_cube_add(size=0.05, location=(0, 0.8, 1.25))
nose = bpy.context.active_object
nose.name = 'Nose'
nose.scale = (0.05, 0.05, 0.05)
objects_created.append(nose)

# Laptop
bpy.ops.mesh.primitive_cube_add(size=1.2, location=(0, -0.3, 0.05))
laptop_base = bpy.context.active_object
laptop_base.name = 'Laptop_Base'
laptop_base.scale = (1.5, 1.0, 0.05)
objects_created.append(laptop_base)

bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, -0.8, 0.5))
laptop_screen = bpy.context.active_object
laptop_screen.name = 'Laptop_Screen'
laptop_screen.scale = (1.4, 0.05, 0.9)
objects_created.append(laptop_screen)

bpy.ops.mesh.primitive_cube_add(size=0.8, location=(0, -0.2, 0.08))
keyboard = bpy.context.active_object
keyboard.name = 'Keyboard'
keyboard.scale = (0.8, 0.5, 0.02)
objects_created.append(keyboard)

print(f"Created {len(objects_created)} objects")

# ============ MATERIALS ============
print("Creating materials...")

def add_simple_material(obj, color_tuple):
    """Add a simple material to an object"""
    mat = bpy.data.materials.new(name=obj.name + '_mat')
    mat.diffuse_color = color_tuple
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat

# Cat materials - cream/beige colors
add_simple_material(floor, (0.91, 0.91, 0.91, 1.0))  # Light gray floor
add_simple_material(body, (0.96, 0.96, 0.86, 1.0))   # Cream body
add_simple_material(head, (0.83, 0.64, 0.45, 1.0))   # Brown head
add_simple_material(ear_l, (0.83, 0.64, 0.45, 1.0))  # Brown ear
add_simple_material(ear_r, (0.83, 0.64, 0.45, 1.0))  # Brown ear
add_simple_material(tail, (0.96, 0.96, 0.86, 1.0))   # Cream tail
add_simple_material(paw_l, (0.96, 0.96, 0.86, 1.0))  # Cream paw
add_simple_material(paw_r, (0.96, 0.96, 0.86, 1.0))  # Cream paw
add_simple_material(laptop_base, (0.75, 0.75, 0.75, 1.0))  # Silver laptop
add_simple_material(laptop_screen, (0.75, 0.75, 0.75, 1.0))  # Silver screen
add_simple_material(keyboard, (0.2, 0.2, 0.2, 1.0))  # Black keyboard
add_simple_material(eye_l, (0.1, 0.1, 0.1, 1.0))    # Black eye
add_simple_material(eye_r, (0.1, 0.1, 0.1, 1.0))    # Black eye
add_simple_material(nose, (1.0, 0.6, 0.6, 1.0))     # Pink nose

# Set all legs to use cat body material
for leg in objects_created:
    if leg.name.startswith('Leg_'):
        add_simple_material(leg, (0.96, 0.96, 0.86, 1.0))

print("Materials created")

# ============ BONES ============
print("Creating armature...")
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.object.select_all(action='DESELECT')
bpy.ops.object.add(type='ARMATURE', location=(0, 0.8, 0.5))
arm = bpy.context.active_object
arm.name = 'CatArmature'
bpy.context.view_layer.objects.active = arm

# Edit mode to create bones
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

# Set hierarchy
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
print("Armature created with bones:", [b.name for b in arm.data.bones])

# Add armature modifier to cat objects
print("Adding armature modifiers...")
cat_objs = ['Body', 'Head', 'Ear_Left', 'Ear_Right', 'Tail', 'Paw_Left', 'Paw_Right']
for obj_name in cat_objs:
    if obj_name in bpy.data.objects:
        obj = bpy.data.objects[obj_name]
        mod = obj.modifiers.new(name='Armature', type='ARMATURE')
        mod.object = arm

print("Armature modifiers added")

# ============ ANIMATION ============
print("Creating animation...")
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
print("Setting typing animation...")
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
print("Setting head follow animation...")
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
print("Setting tail animation...")
for frame in range(1, 61):
    tail_rot_z = 0.2 * math.sin(frame * 0.3)
    tail_rot_x = 0.1 * math.sin(frame * 0.2)
    set_bone_rotation('Tail_Base', tail_rot_x, 0, tail_rot_z, frame)
    set_bone_rotation('Tail_Tip', tail_rot_x * 1.5, 0, tail_rot_z * 1.5, frame)

# Ear twitching
print("Setting ear animation...")
for frame in range(1, 61):
    if frame % 10 == 0:
        set_bone_rotation('L_Ear', 0.1, 0, 0.1, frame)
    else:
        set_bone_rotation('L_Ear', 0, 0, 0, frame)
    
    if frame % 12 == 0:
        set_bone_rotation('R_Ear', 0.1, 0, -0.1, frame)
    else:
        set_bone_rotation('R_Ear', 0, 0, 0, frame)

print("Animation created: 60 frames at 24 FPS")

# ============ LIGHTING ============
print("Setting up lighting...")
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

print("Lights created")

# ============ CAMERA ============
print("Setting up camera...")
bpy.ops.object.camera_add(location=(2.5, -2, 2))
cam = bpy.context.active_object
cam.name = 'Camera'
cam.rotation_euler = (1.1, 0, 0.785)
bpy.context.scene.camera = cam
print("Camera positioned")

# ============ RENDER SETTINGS ============
print("Setting render settings...")
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.cycles.samples = 128
scene.cycles.use_denoising = True
scene.render.film_transparent = True

# Set output path
output_dir = os.path.join(os.path.dirname(__file__), 'output')
os.makedirs(output_dir, exist_ok=True)
scene.render.filepath = os.path.join(output_dir, 'kitty_working')

# Set world background
world = bpy.data.worlds['World']
world.node_tree.nodes['Background'].inputs['Color'].default_value = (0.1, 0.1, 0.15, 1)
world.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.5

# Save blend file
blend_path = os.path.join(os.path.dirname(__file__), 'kitty_working.blend')
bpy.ops.wm.save_as_mainfile(filepath=blend_path)
print(f"Saved blend file to: {blend_path}")

# Render final image
print("Rendering final image...")
bpy.ops.render.render(write_still=True)
print(f"Rendered image saved to: {scene.render.filepath}.png")

print("\n=== SCENE SETUP COMPLETE ===")
print(f"Objects: {len(bpy.context.scene.objects)}")
print(f"Bones: {len(arm.data.bones)}")
print(f"Animation: 1-60 frames")
print(f"Render resolution: {scene.render.resolution_x}x{scene.render.resolution_y}")
print(f"Output directory: {output_dir}")
