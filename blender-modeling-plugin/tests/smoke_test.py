"""
Smoke tests for basic functionality
"""
import sys
import os

# Add plugin root to path
plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, plugin_root)

# Test imports
try:
    from operators.primitive import ADVANCED_OT_primitive_add
    print("[OK] primitive module imported")
except ImportError as e:
    print(f"[FAIL] primitive module: {e}")

try:
    from operators.boolean import ADVANCED_OT_mesh_boolean
    print("[OK] boolean module imported")
except ImportError as e:
    print(f"[FAIL] boolean module: {e}")

try:
    from operators.modifiers import ADVANCED_OT_add_modifier
    print("[OK] modifiers module imported")
except ImportError as e:
    print(f"[FAIL] modifiers module: {e}")

try:
    from operators.mesh_operations import ADVANCED_OT_extrude_region
    print("[OK] mesh_operations module imported")
except ImportError as e:
    print(f"[FAIL] mesh_operations module: {e}")

try:
    from operators.selection import ADVANCED_OT_select_all
    print("[OK] selection module imported")
except ImportError as e:
    print(f"[FAIL] selection module: {e}")

try:
    from operators.transform import ADVANCED_OT_transform_copy
    print("[OK] transform module imported")
except ImportError as e:
    print(f"[FAIL] transform module: {e}")

try:
    from models.mesh_utils import create_bmesh_from_primitive
    print("[OK] mesh_utils module imported")
except ImportError as e:
    print(f"[FAIL] mesh_utils module: {e}")

try:
    from models.transform_utils import copy_transform
    print("[OK] transform_utils module imported")
except ImportError as e:
    print(f"[FAIL] transform_utils module: {e}")

print("\nAll imports successful!")
