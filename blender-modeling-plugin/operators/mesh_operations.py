"""
Mesh operations based on Blender API and BMesh
"""
import bpy
import bmesh
from mathutils import Vector


class ADVANCED_OT_extrude_region(bpy.types.Operator):
    """Extrude selected faces"""
    bl_idname = "advanced.extrude_region"
    bl_label = "Extrude Region"
    bl_options = {'REGISTER', 'UNDO'}

    offset: bpy.props.FloatProperty(
        name="Offset",
        description="Extrusion offset",
        default=0.1,
        step=0.01
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        
        # Create BMesh from mesh
        bm = bmesh.from_edit_mesh(obj.data)
        
        # Get selected faces
        selected_faces = [f for f in bm.faces if f.select]
        
        if not selected_faces:
            self.report({'ERROR'}, "No faces selected")
            bpy.ops.object.mode_set(mode='OBJECT')
            return {'CANCELLED'}

        # Extrude faces
        bmesh.ops.extrude_face_region(bm, faces=selected_faces)
        
        # Move extruded faces by offset
        new_faces = [f for f in bm.faces if f.select]
        if new_faces:
            for face in new_faces:
                for v in face.verts:
                    v.co += Vector((0, 0, self.offset))
        
        bmesh.update_edit_mesh(obj.data)
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}


class ADVANCED_OT_extrude_edges(bpy.types.Operator):
    """Extrude selected edges"""
    bl_idname = "advanced.extrude_edges"
    bl_label = "Extrude Edges"
    bl_options = {'REGISTER', 'UNDO'}

    offset: bpy.props.FloatProperty(
        name="Offset",
        description="Extrusion offset",
        default=0.1,
        step=0.01
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.extrude_region_move(
            TRANSFORM_OT_translate={
                "value": (0, 0, self.offset)
            }
        )
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}


class ADVANCED_OT_merge(bpy.types.Operator):
    """Merge vertices"""
    bl_idname = "advanced.merge"
    bl_label = "Merge"
    bl_options = {'REGISTER', 'UNDO'}

    merge_type: bpy.props.EnumProperty(
        items=[
            ('CENTER', 'At Center', 'Merge to center'),
            ('LAST', 'At Last', 'Merge to last selected'),
            ('CURSOR', 'At Cursor', 'Merge to 3D cursor'),
            ('BOUNDARY', 'At Boundary', 'Merge boundary vertices'),
        ],
        description="Merge type"
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        
        if self.merge_type == 'CENTER':
            bpy.ops.mesh.merge(type='CENTER')
        elif self.merge_type == 'LAST':
            bpy.ops.mesh.merge(type='LAST')
        elif self.merge_type == 'CURSOR':
            bpy.ops.mesh.merge(type='CURSOR')
        elif self.merge_type == 'BOUNDARY':
            bpy.ops.mesh.merge(type='BOUNDARY')
        
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}


class ADVANCED_OT_split(bpy.types.Operator):
    """Split vertices/faces"""
    bl_idname = "advanced.split"
    bl_label = "Split"
    bl_options = {'REGISTER', 'UNDO'}

    split_type: bpy.props.EnumProperty(
        items=[
            ('VERT', 'Vertex', 'Split at vertex'),
            ('EDGE', 'Edge', 'Split at edge'),
        ],
        description="Split type"
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        
        if self.split_type == 'VERT':
            bpy.ops.mesh.split(type='VERT')
        else:
            bpy.ops.mesh.split()
        
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}


class ADVANCED_OT_face_fill(bpy.types.Operator):
    """Fill selected edges with face"""
    bl_idname = "advanced.face_fill"
    bl_label = "Fill Face"
    bl_options = {'REGISTER', 'UNDO'}

    fill_type: bpy.props.EnumProperty(
        items=[
            ('FACE', 'Face', 'Simple fill'),
            ('NGON', 'Ngon', 'N-gon fill'),
            ('TRIANGLES', 'Triangles', 'Triangle fan'),
            ('QUADS', 'Quads', 'Quad fill'),
        ],
        description="Fill type"
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        
        if self.fill_type == 'FACE':
            bpy.ops.mesh.face_fill()
        elif self.fill_type == 'NGON':
            bpy.ops.mesh.fill(shape_type='NGON')
        elif self.fill_type == 'TRIANGLES':
            bpy.ops.mesh.fill(shape_type='TRIFAN')
        elif self.fill_type == 'QUADS':
            bpy.ops.mesh.fill(shape_type='VERTS')
        
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}


class ADVANCED_OT_Knife(bpy.types.Operator):
    """Cut geometry with knife tool"""
    bl_idname = "advanced.knife"
    bl_label = "Knife Cut"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.knife_tool()
        
        return {'FINISHED'}


class ADVANCED_OT_shrink_fatten(bpy.types.Operator):
    """Shrink or fatten selected faces"""
    bl_idname = "advanced.shrink_fatten"
    bl_label = "Shrink/Fatten"
    bl_options = {'REGISTER', 'UNDO'}

    amount: bpy.props.FloatProperty(
        name="Amount",
        description="Shrink/fatten amount",
        default=0.1,
        step=0.01
    )

    def execute(self context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.shrink_fatten(value=self.amount)
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return {'FINISHED'}
