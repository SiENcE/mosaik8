#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""
mosaik Parser Test Demo
Tests the fixed parser with various export formats and language constructs.
"""

# You'll need to copy the Lexer class from the original file
# This demo assumes the fixed Parser class is available

def test_export_formats():
    """Test different export statement formats."""
    print("=== TESTING EXPORT FORMATS ===")
    
    test_cases = [
        ("Single Export", '''
        module "test1" {
            function hello() {}
            export hello
        }
        '''),
        
        ("Comma-separated Exports", '''
        module "test2" {
            function add() {}
            var result: u8 = 0
            export add, result
        }
        '''),
        
        ("Braced Exports", '''
        module "test3" {
            function multiply() {}
            function divide() {}
            var counter: u8
            export { multiply, divide, counter }
        }
        '''),
        
        ("Mixed Declaration Types", '''
        module "test4" {
            type Point = struct { x: u8, y: u8 }
            var origin: Point
            function distance() -> u8 {}
            const MAX_POINTS: u8 = 100
            export Point, origin, distance, MAX_POINTS
        }
        ''')
    ]
    
    # Import the classes (assuming they're available)
    from mosaik import Lexer, Parser
    
    for name, code in test_cases:
        print(f"--- {name} ---")
        try:
            lexer = Lexer(code.strip())
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            ast = parser.parse()
            
            module = ast.modules[0]
            print(f"✅ Module: {module.name}")
            print(f"   Declarations: {len(module.declarations)}")
            print(f"   Exports: {module.exports}")
            
        except Exception as e:
            print(f"❌ Error: {e}")
        print()

def test_complex_language_features():
    """Test complex language constructs with fixed parameter names."""
    print("=== TESTING COMPLEX FEATURES (FIXED) ===")
    
    complex_code = '''
    module "game_engine" {
        import "platform.video"
        import "platform.input"
        
        -- Type definitions
        type Vector2 = struct {
            x: i8,
            y: i8
        }
        
        type Entity = struct {
            position: Vector2,
            velocity: Vector2,
            sprite_id: u8,
            active: bool
        }
        
        enum EntityType {
            PLAYER = 0,
            ENEMY = 1,
            POWERUP = 2
        }
        
        -- Global state
        var entities: array[Entity, 32]
        var entity_count: u8 = 0
        const MAX_VELOCITY: u8 = 4
        
        -- Helper functions (fixed parameter name)
        function create_entity(x: i8, y: i8, entity_type: EntityType) -> u8 {
            if entity_count >= 32 {
                return 255  -- Error: no space
            }
            
            var index: u8 = entity_count
            entities[index].position.x = x
            entities[index].position.y = y
            entities[index].velocity.x = 0
            entities[index].velocity.y = 0
            entities[index].active = true
            
            entity_count += 1
            return index
        }
        
        function update_entity(index: u8) {
            if not entities[index].active {
                return
            }
            
            -- Update position
            entities[index].position.x += entities[index].velocity.x
            entities[index].position.y += entities[index].velocity.y
            
            -- Boundary checking
            if entities[index].position.x < 0 or entities[index].position.x > 160 {
                entities[index].velocity.x = -entities[index].velocity.x
            }
            if entities[index].position.y < 0 or entities[index].position.y > 144 {
                entities[index].velocity.y = -entities[index].velocity.y
            }
        }
        
        function update_all_entities() {
            var i: u8 = 0
            loop {
                if i >= entity_count {
                    return
                }
                update_entity(i)
                i += 1
            }
        }
        
        local function internal_cleanup() {
            -- Private function for cleanup
            entity_count = 0
        }
        
        function main_game_loop() {
            video.enable_lcd()
            
            -- Create player entity
            var player_id: u8 = create_entity(80, 72, PLAYER)
            
            loop {
                -- Handle input
                if input.pressed(INPUT_LEFT) {
                    entities[player_id].velocity.x = -1
                }
                if input.pressed(INPUT_RIGHT) {
                    entities[player_id].velocity.x = 1
                }
                if input.pressed(INPUT_UP) {
                    entities[player_id].velocity.y = -1
                }
                if input.pressed(INPUT_DOWN) {
                    entities[player_id].velocity.y = 1
                }
                
                -- Update game state
                update_all_entities()
                
                -- Wait for next frame
                video.wait_vblank()
            }
        }
        
        -- Export public interface
        export Vector2, Entity, EntityType, create_entity, update_entity, 
               update_all_entities, main_game_loop, MAX_VELOCITY
    }
    '''
    
    from mosaik import Lexer, Parser
    
    try:
        lexer = Lexer(complex_code.strip())
        tokens = lexer.tokenize()
        
        print(f"Lexer: Generated {len(tokens)} tokens")
        
        parser = Parser(tokens)
        ast = parser.parse()
        
        module = ast.modules[0]
        print(f"✅ Parser: Successfully parsed complex module")
        print(f"   Module name: {module.name}")
        print(f"   Imports: {[imp.module_name for imp in module.imports]}")
        print(f"   Declarations: {len(module.declarations)}")
        print(f"   Export count: {len(module.exports)}")
        
        # Analyze declaration types
        decl_types = {}
        for decl in module.declarations:
            decl_type = type(decl).__name__
            decl_types[decl_type] = decl_types.get(decl_type, 0) + 1
        
        print(f"   Declaration breakdown: {decl_types}")
        
    except Exception as e:
        print(f"❌ Error in complex parsing: {e}")
        import traceback
        traceback.print_exc()

def test_error_recovery():
    """Test parser error handling and recovery."""
    print("\n=== TESTING ERROR HANDLING ===")
    
    error_cases = [
        ("Missing brace", '''
        module "bad1" {
            function test() {
                return 42
            -- Missing closing brace
        '''),
        
        ("Invalid export", '''
        module "bad2" {
            function test() {}
            export test,  -- Trailing comma
        }
        '''),
        
        ("Invalid type", '''
        module "bad3" {
            var x: invalid_type = 0
        }
        ''')
    ]
    
    from mosaik import Lexer, Parser
    
    for name, code in error_cases:
        print(f"--- {name} ---")
        try:
            lexer = Lexer(code.strip())
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            ast = parser.parse()
            print(f"❌ Should have failed but didn't!")
        except Exception as e:
            print(f"✅ Correctly caught error: {e}")

def demonstrate_ast_structure():
    """Show the AST structure for a simple program."""
    print("\n=== AST STRUCTURE DEMO ===")
    
    simple_code = '''
    module "demo" {
        type Point = struct { x: u8, y: u8 }
        var origin: Point
        
        function distance(p1: Point, p2: Point) -> u8 {
            var dx: u8 = p1.x - p2.x
            var dy: u8 = p1.y - p2.y
            return dx + dy  -- Manhattan distance
        }
        
        export Point, distance
    }
    '''
    
    from mosaik import Lexer, Parser
    
    try:
        lexer = Lexer(simple_code.strip())
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        ast = parser.parse()
        
        print("✅ AST Structure:")
        print(f"Program")
        for i, module in enumerate(ast.modules):
            print(f"  └─ Module[{i}]: '{module.name}'")
            print(f"     ├─ Imports: {len(module.imports)}")
            print(f"     ├─ Declarations: {len(module.declarations)}")
            for j, decl in enumerate(module.declarations):
                decl_type = type(decl).__name__
                if hasattr(decl, 'name'):
                    print(f"     │  ├─ {decl_type}: {decl.name}")
                else:
                    print(f"     │  ├─ {decl_type}")
            print(f"     └─ Exports: {module.exports}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("mosaik Parser Test Demo")
    print("=" * 50)
    
    # Test export formats (the main fix)
    test_export_formats()
    
    # Test complex language features
    test_complex_language_features()
    
    # Test error handling
    test_error_recovery()
    
    # Show AST structure
    demonstrate_ast_structure()
    
    print("\n✅ Parser testing completed!")
    print("\nKey fixes implemented:")
    print("  • Export statements now support comma-separated lists")
    print("  • Better error messages with line numbers")
    print("  • Improved newline handling in struct definitions")
    print("  • More robust token matching and advancement")
