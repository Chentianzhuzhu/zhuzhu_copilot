import bpy
import math

# Create animation for the cat
scene = bpy.context.scene

# Set timeline range
scene.frame_start = 1
scene.frame_end = 60
scene.render.fps = 24

# Get armature and bones
armature = bpy.data.objects.get('CatArmature')
if not armature:
    print('Armature not found!')
    exit()

# Helper function to set bone rotation
def set_bone_rotation(bone_name, rot_x, rot_y, rot_z, frame):
    if bone_name in armature.pose.bones:
        bone = armature.pose.bones[bone_name]
        bone.rotation_euler = (rot_x, rot_y, rot_z)
        bone.keyframe_insert(data_path="rotation_euler", frame=frame)

# Helper function to set bone location
def set_bone_location(bone_name, loc_x, loc_y, loc_z, frame):
    if bone_name in armature.pose.bones:
        bone = armature.pose.bones[bone_name]
        bone.location = (loc_x, loc_y, loc_z)
        bone.keyframe_insert(data_path="location", frame=frame)

# ============ KEYBOARD TYPING ANIMATION ============
for frame in range(1, 61):
    # Left paw - rhythmic tapping on keyboard
    if frame % 4 == 0:
        set_bone_location('Paw_Left', -0.25, 0.35, 0.45 + 0.08, frame)
    else:
        set_bone_location('Paw_Left', -0.25, 0.35, 0.45, frame)
    
    # Right paw - rhythmic tapping on keyboard (offset timing)
    if frame % 4 == 2:
        set_bone_location('Paw_Right', 0.25, 0.35, 0.45 + 0.08, frame)
    else:
        set_bone_location('Paw_Right', 0.25, 0.35, 0.45, frame)

# ============ HEAD FOLLOWING MOUSE ANIMATION ============
# Create a "mouse cursor" that moves around
mouse_empty = None
for frame in range(1, 61):
    # Mouse moves in a path
    x = 0.5 * math.sin(frame * 0.1)
    y = 0.5 * math.cos(frame * 0.15)
    z = 1.3
    
    # Create mouse cursor empty at first frame
    if frame == 1:
        bpy.ops.object.add(type='EMPTY', location=(x, y, z))
        mouse_empty = bpy.context.active_object
        mouse_empty.name = 'MouseCursor'
    
    # Update mouse position
    mouse_empty.location = (x, y, z)
    
    # Head follows mouse with smooth delay
    follow_x = x * 0.3
    follow_y = y * 0.3
    follow_z = z * 0.1
    
    # Head rotation follows mouse
    rot_x = follow_y * 0.5
    rot_y = follow_x * 0.8
    set_bone_rotation('Head', rot_x, rot_y, 0, frame)
    
    # Body slightly follows too
    body_rot_y = follow_x * 0.3
    set_bone_rotation('Spine', 0, body_rot_y, 0, frame)

# ============ TAIL WAGGING ANIMATION ============
for frame in range(1, 61):
    # Tail sways gently
    tail_rot_z = 0.2 * math.sin(frame * 0.3)
    tail_rot_x = 0.1 * math.sin(frame * 0.2)
    set_bone_rotation('Tail_Base', tail_rot_x, 0, tail_rot_z, frame)
    set_bone_rotation('Tail_Tip', tail_rot_x * 1.5, 0, tail_rot_z * 1.5, frame)

# ============ EARS TWITCH ============
for frame in range(1, 61):
    # Left ear twitches occasionally
    if frame % 10 == 0:
        set_bone_rotation('L_Ear', 0.1, 0, 0.1, frame)
    else:
        set_bone_rotation('L_Ear', 0, 0, 0, frame)
    
    # Right ear twitches occasionally  
    if frame % 12 == 0:
        set_bone_rotation('R_Ear', 0.1, 0, -0.1, frame)
    else:
        set_bone_rotation('R_Ear', 0, 0, 0, frame)

print('Animation created successfully!')
print('Timeline: 1-60 frames at 24 FPS')
print('Animations:')
print('- Keyboard typing (left/right paws)')
print('- Head follows mouse cursor')
print('- Tail wagging')
print('- Ear twitching')
