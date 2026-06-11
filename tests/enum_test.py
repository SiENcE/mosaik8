#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""
Test specifically for enum constant resolution - Fixed Version.
"""

def test_enum_constants():
    """Test that enum constants are properly registered and accessible."""
    
    print("Testing Enum Constants Resolution - Fixed")
    print("=" * 50)
    
    try:
        from mosaik import MosaikCompiler
        print("✅ Successfully imported fixed compiler")
    except ImportError as e:
        print(f"❌ Failed to import compiler: {e}")
        return False
    
    compiler = MosaikCompiler()
    
    # Simple enum test
    simple_enum_code = '''
    module "test_enum" {
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
    
    print("\n--- Testing Simple Enum ---")
    result = compiler.compile(simple_enum_code.strip())
    
    if result.startswith("Compilation error:"):
        print(f"❌ FAILED: {result.split(chr(10))[0]}")
        
        # Let's add some debugging
        print("\n--- Debug: Let's see what's in the symbol table ---")
        debug_code = '''
        module "debug" {
            enum Color {
                RED = 1,
                GREEN = 2,
                BLUE = 3
            }
        }
        '''
        
        # Try to compile just the enum to see if it gets registered
        try:
            from mosaik import Lexer, Parser, TypeChecker
            lexer = Lexer(debug_code.strip())
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            ast = parser.parse()
            
            type_checker = TypeChecker()
            type_checker.check_program(ast)
            
            print(f"Symbol table: {type_checker.symbol_table}")
            print(f"Current scope: {type_checker.current_scope}")
            print(f"Type table: {list(type_checker.type_table.keys())}")
            
        except Exception as e:
            print(f"Debug compilation failed: {e}")
        
        return False
    else:
        print("✅ PASSED: Simple enum works!")
        print("Generated assembly (first 10 lines):")
        lines = result.split('\n')
        for line in lines[:10]:
            print(f"  {line}")
        return True

def test_enum_in_expressions():
    """Test enum constants in various expressions."""
    print("\n--- Testing Enum in Expressions ---")
    
    expr_code = '''
    module "enum_expressions" {
        enum Status {
            INACTIVE = 0,
            ACTIVE = 1,
            PENDING = 2
        }
        
        var current_status: Status = ACTIVE
        var is_active: bool = current_status == ACTIVE
        
        function check_status() -> bool {
            if current_status == PENDING {
                return false
            }
            return true
        }
        
        export Status, check_status
    }
    '''
    
    from mosaik import MosaikCompiler
    compiler = MosaikCompiler()
    result = compiler.compile(expr_code.strip())
    
    if result.startswith("Compilation error:"):
        print(f"❌ FAILED: {result.split(chr(10))[0]}")
        return False
    else:
        print("✅ PASSED: Enum expressions work!")
        return True

def test_multiple_enums():
    """Test multiple enums in the same module."""
    print("\n--- Testing Multiple Enums ---")
    
    multi_enum_code = '''
    module "multiple_enums" {
        enum Direction {
            NORTH = 0,
            SOUTH = 1,
            EAST = 2,
            WEST = 3
        }
        
        enum Color {
            RED = 0,
            GREEN = 1,
            BLUE = 2
        }
        
        var player_dir: Direction = NORTH
        var player_color: Color = RED
        
        function move_player() {
            if player_dir == SOUTH {
                player_dir = NORTH
            }
        }
        
        export Direction, Color, move_player
    }
    '''
    
    from mosaik import MosaikCompiler
    compiler = MosaikCompiler()
    result = compiler.compile(multi_enum_code.strip())
    
    if result.startswith("Compilation error:"):
        print(f"❌ FAILED: {result.split(chr(10))[0]}")
        return False
    else:
        print("✅ PASSED: Multiple enums work!")
        return True

if __name__ == "__main__":
    print("mosaik Enum Constants Test - Fixed Version")
    print("=" * 60)
    
    success_count = 0
    total_tests = 3
    
    # Run all tests
    if test_enum_constants():
        success_count += 1
    
    if test_enum_in_expressions():
        success_count += 1
        
    if test_multiple_enums():
        success_count += 1
    
    print(f"\n🎯 Test Results: {success_count}/{total_tests} tests passed")
    
    if success_count == total_tests:
        print("🎉 All enum tests are working correctly!")
    else:
        print("💥 Some enum tests still have issues.")
        print("\nNext steps:")
        print("  1. Check that enum constants are registered in symbol table")
        print("  2. Verify type checking works for enum assignments")  
        print("  3. Test enum constants in comparisons and expressions")
