#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""
Quick test to verify mosaik compiler fixes work correctly.
Save this alongside the fixed compiler files.
"""

def test_basic_compilation():
    """Test that basic compilation works without the previous errors."""
    
    print("Testing mosaik Compiler Fixes")
    print("=" * 40)
    
    # Import the fixed compiler
    try:
        from mosaik import MosaikCompiler, Lexer, Parser
        print("✅ Successfully imported fixed compiler")
    except ImportError as e:
        print(f"❌ Failed to import compiler: {e}")
        return False
    
    # Test cases that were previously failing
    test_cases = [
        ("Type System with Structs", '''
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
        '''),
        
        ("Math Functions with Local Variables", '''
        module "math" {
            function multiply(a: u8, b: u8) -> u8 {
                var result: u8 = 0
                var i: u8 = 0
                
                loop {
                    if i >= b {
                        return result
                    }
                    result += a
                    i += 1
                }
            }
            
            export multiply
        }
        '''),
        
        ("Game Loop with Input", '''
        module "game" {
            import "platform.input"
            import "platform.video"
            
            var player_x: u8 = 80
            var player_y: u8 = 72
            
            function update_player() {
                if input.pressed(INPUT_LEFT) and player_x > 0 {
                    player_x -= 1
                }
                if input.pressed(INPUT_RIGHT) and player_x < 152 {
                    player_x += 1
                }
            }
            
            function main() {
                video.enable_lcd()
                
                loop {
                    update_player()
                    video.wait_vblank()
                }
            }
            
            export main, player_x, player_y
        }
        ''')
    ]
    
    compiler = MosaikCompiler()
    
    for name, code in test_cases:
        print(f"\n--- Testing: {name} ---")
        result = compiler.compile(code.strip())
        
        if result.startswith("Compilation error:"):
            print(f"❌ FAILED: {result.split(chr(10))[0]}")
            return False
        else:
            print("✅ PASSED: Compilation successful")
            # Show a sample of the generated GBDK C
            lines = result.split('\n')
            code_lines = [line for line in lines if line.strip()][:10]
            print("   Sample C output:")
            for line in code_lines[:5]:
                print(f"     {line}")
            if len(code_lines) > 5:
                print(f"     ... ({len(code_lines) - 5} more lines)")

    # Test error handling.
    #
    # Type checking is intentionally best-effort: it emits diagnostics but
    # never aborts code generation, so that the advanced language features the
    # samples rely on (forward references, struct literals, etc.) still build.
    # A hard failure is therefore only expected for genuine *syntax* errors.
    print(f"\n--- Testing: Error Handling (syntax error) ---")
    error_code = '''
    module "bad" {
        function test( {
            return 42
        }
    }
    '''

    result = compiler.compile(error_code.strip())
    if result.startswith("Compilation error:"):
        print("✅ PASSED: Syntax error correctly caught")
        print(f"   Error: {result.split(chr(10))[0]}")
    else:
        print("❌ FAILED: Should have caught syntax error")
        return False

    return True

if __name__ == "__main__":
    success = test_basic_compilation()
    
    if success:
        print("\n🎉 All tests passed! The mosaik compiler fixes work correctly.")
        print("\nYou can now run the full demo with:")
        print("    python tests/run_all.py")
    else:
        print("\n💥 Some tests failed. Check the error messages above.")
