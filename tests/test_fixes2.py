#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""
Test the fixed error handling in mosaik parser.
"""

def test_fixed_error_recovery():
    """Test parser error handling with fixes applied."""
    print("=== TESTING FIXED ERROR HANDLING ===")
    
    error_cases = [
        ("Missing brace", '''
        module "bad1" {
            function test() {
                return 42
            -- Missing closing brace
        '''),
        
        ("Invalid export - trailing comma", '''
        module "bad2" {
            function test() {}
            export test,
        }
        '''),
        
        ("Invalid export - comma without identifier", '''
        module "bad3" {
            function test() {}
            export test, 
        }
        '''),
        
        ("Invalid type", '''
        module "bad4" {
            var x: invalid_type = 0
        }
        ''')
    ]
    
    # Import original compiler first
    from mosaik import Lexer, MosaikCompiler
    
    # Test with original parser (should show the problem)
    print("--- Testing with ORIGINAL parser ---")
    compiler = MosaikCompiler()
    
    for name, code in error_cases[1:]:  # Skip the first one that already works
        print(f"Testing: {name}")
        result = compiler.compile(code.strip())
        if result.startswith("Compilation error:"):
            print(f"  ✅ Correctly caught: {result.split(chr(10))[0]}")
        else:
            print(f"  ❌ Should have failed but didn't!")
    
    print("\n--- To fix this, apply the parser fixes from the artifact above ---")
    print("The fixes make the parser stricter about:")
    print("  • Requiring identifiers after commas in export lists")
    print("  • Better validation of type names in the type checker")

def test_specific_export_cases():
    """Test various export syntax edge cases."""
    print("\n=== TESTING EXPORT EDGE CASES ===")
    
    from mosaik import Lexer, Parser
    
    # These should work
    valid_cases = [
        ("Normal export", "export test"),
        ("Multiple exports", "export a, b, c"),
        ("Braced exports", "export { a, b, c }"),
        ("Single braced", "export { test }")
    ]
    
    # These should fail (with fixes)
    invalid_cases = [
        ("Trailing comma", "export test,"),
        ("Empty after comma", "export test, "),
        ("Comma without identifier", "export test, }"),
    ]
    
    for name, export_stmt in valid_cases:
        test_code = f'''
        module "test" {{
            function test() {{}}
            var a: u8 = 0
            var b: u8 = 0
            var c: u8 = 0
            {export_stmt}
        }}
        '''
        
        try:
            lexer = Lexer(test_code.strip())
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            ast = parser.parse()
            print(f"  ✅ {name}: {ast.modules[0].exports}")
        except Exception as e:
            print(f"  ❌ {name} failed: {e}")
    
    print("\nInvalid cases (should fail with fixes):")
    for name, export_stmt in invalid_cases:
        test_code = f'''
        module "test" {{
            function test() {{}}
            {export_stmt}
        }}
        '''
        
        try:
            lexer = Lexer(test_code.strip())
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            ast = parser.parse()
            print(f"  ❌ {name}: Should have failed but didn't!")
        except Exception as e:
            print(f"  ✅ {name}: Correctly caught error")

if __name__ == "__main__":
    test_fixed_error_recovery()
    test_specific_export_cases()
