"""
Unit tests for Blender modeling plugin
"""
import unittest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.mesh_utils import create_bmesh_from_primitive, merge_by_distance, subdivide
from models.transform_utils import copy_transform, apply_transform, reset_transform


class TestPrimitiveCreation(unittest.TestCase):
    """Test primitive creation functions"""
    
    def test_create_bmesh_box(self):
        """Test creating a box mesh"""
        bm = create_bmesh_from_primitive('BOX', size=2.0)
        self.assertIsNotNone(bm)
        self.assertEqual(len(bm.verts), 8)
        self.assertEqual(len(bm.edges), 12)
        self.assertEqual(len(bm.faces), 6)
    
    def test_create_bmesh_sphere(self):
        """Test creating a sphere mesh"""
        bm = create_bmesh_from_primitive('SPHERE', size=1.0)
        self.assertIsNotNone(bm)
        self.assertGreater(len(bm.verts), 0)
        self.assertGreater(len(bm.faces), 0)
    
    def test_create_bmesh_cylinder(self):
        """Test creating a cylinder mesh"""
        bm = create_bmesh_from_primitive('CYLINDER', size=1.0)
        self.assertIsNotNone(bm)
        self.assertGreater(len(bm.verts), 0)


class TestTransformUtils(unittest.TestCase):
    """Test transform utility functions"""
    
    def test_reset_transform(self):
        """Test resetting transform to defaults"""
        # Mock object for testing
        class MockObj:
            location = None
            rotation_euler = None
            scale = None
        
        obj = MockObj()
        result = reset_transform(obj)
        self.assertTrue(result)
        self.assertEqual(obj.location.x, 0.0)
        self.assertEqual(obj.location.y, 0.0)
        self.assertEqual(obj.location.z, 0.0)
        self.assertEqual(obj.scale.x, 1.0)
        self.assertEqual(obj.scale.y, 1.0)
        self.assertEqual(obj.scale.z, 1.0)


class TestMeshOperations(unittest.TestCase):
    """Test mesh operation functions"""
    
    def test_merge_by_distance_none_object(self):
        """Test merging with None object"""
        result = merge_by_distance(None)
        self.assertFalse(result)
    
    def test_subdivide_none_object(self):
        """Test subdividing with None object"""
        result = subdivide(None)
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
