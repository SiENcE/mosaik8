#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""
Quick verification test for the specific failing cases.
Run this to check if the main issues are fixed.
"""

def test_specific_failures():
    """Test the exact cases that were failing before."""
    
    print("Testing Specific mosaik Failures")
    print("=" * 40)
    
    try:
        from mosaik import MosaikCompiler
        print("✅ Successfully imported fixed compiler")
    except ImportError as e:
        print(f"❌ Failed to import compiler: {e}")
        return False
    
    compiler = MosaikCompiler()
    
    # Test 1: Struct with newlines (was failing)
    print("\n--- Test 1: Struct with Newlines ---")
    struct_code = '''
    module "test_structs" {
        type Point = struct {
            x: u8,
            y: u8
        }
        
        var origin: Point
        
        export Point, origin
    }
    '''
    
    result = compiler.compile(struct_code.strip())
    if result.startswith("Compilation error:"):
        print(f"❌ FAILED: {result.split(chr(10))[0]}")
        return False
    else:
        print("✅ PASSED: Struct with newlines works")
    
    # Test 2: Enum declaration (was failing)
    print("\n--- Test 2: Standalone Enum ---")
    enum_code = '''
    module "types_demo" {
        enum Direction {
            UP = 0,
            DOWN = 1,
            LEFT = 2,
            RIGHT = 3
        }
        
        var dir: Direction = UP
        
        export Direction, dir
    }
    '''
    
    result = compiler.compile(enum_code.strip())
    if result.startswith("Compilation error:"):
        print(f"❌ FAILED: {result.split(chr(10))[0]}")
        return False
    else:
        print("✅ PASSED: Standalone enum works")
        print("   Generated assembly preview:")
        lines = result.split('\n')[:8]
        for line in lines:
            if line.strip():
                print(f"     {line}")
    
    # Test 3: Complex struct (was failing)
    print("\n--- Test 3: Complex Struct Type ---")
    complex_code = '''
    module "advanced" {
        type Sprite = struct {
            x: u8,
            y: u8,
            tile_id: u8,
            flags: u8
        }
        
        var player: Sprite
        
        export Sprite, player
    }
    '''
    
    result = compiler.compile(complex_code.strip())
    if result.startswith("Compilation error:"):
        print(f"❌ FAILED: {result.split(chr(10))[0]}")
        return False
    else:
        print("✅ PASSED: Complex struct works")
    
    # Test 4: Combined types (the original failing case)
    print("\n--- Test 4: Combined Types (Original Failure) ---")
    combined_code = '''
    module "types_demo" {
        type Position = struct {
            x: u8,
            y: u8
        }
        
        enum Direction {
            UP = 0,
            DOWN = 1,
            LEFT = 2,
            RIGHT = 3
        }
        
        var pos: Position
        var dir: Direction = UP
        
        function move_position(delta_x: i8, delta_y: i8) {
            pos.x += delta_x
            pos.y += delta_y
        }
        
        export Position, Direction, move_position
    }
    '''
    
    result = compiler.compile(combined_code.strip())
    if result.startswith("Compilation error:"):
        print(f"❌ FAILED: {result.split(chr(10))[0]}")
        return False
    else:
        print("✅ PASSED: Combined types work")
        
    print("\n🎉 All specific failures are now fixed!")
    return True

if __name__ == "__main__":
    success = test_specific_failures()
    
    if success:
        print("\n✨ The compiler fixes are working correctly!")
        print("You can now run the full demo without errors.")
    else:
        print("\n💥 Some issues remain. Check the error messages above.")