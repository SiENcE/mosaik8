#!/usr/bin/env python3
"""MosaiK8 Build Tool (CLI entry).

Build targets + cartridge geometry live in mosaik8_targets; the build pipeline
(BuildConfig / MosaikBuilder / toolchain interfaces) in mosaik8_build. Both
`python mosaik8.py build ...` and `import mosaik8` (for PLATFORM_TARGETS + the
builder classes) keep working via the re-exports below."""
import argparse
import sys

from mosaik8_targets import *  # noqa: F401,F403
from mosaik8_build import *    # noqa: F401,F403


def main():
    """Main entry point."""
    # Ensure console output (which uses status emoji) never crashes on
    # consoles with a non-UTF-8 default encoding such as Windows cp1252.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description='MosaiK8 Build Tool')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Build command
    build_parser = subparsers.add_parser('build', help='Build a mosaik file or project')
    build_parser.add_argument('--platform', choices=list(PLATFORM_TARGETS.keys()),
                             help='Target console (overrides the project setting)')
    build_parser.add_argument('--all-platforms', action='store_true',
                             help='Build for all supported consoles (single-file mode only)')
    build_parser.add_argument('--debug', action='store_true',
                             help='Generate debug symbols')
    build_parser.add_argument('--asset', action='append', default=[],
                             metavar='PNG', dest='assets',
                             help='PNG to convert to tile data and link in '
                                  '(repeatable; projects can also list assets '
                                  'in mosaik.toml under [assets] sprites)')
    build_parser.add_argument('target', nargs='?',
                             help='A .mos source file, or a mosaik.toml '
                                  '(or a directory containing one; defaults to '
                                  './mosaik.toml)')

    # Clean command
    subparsers.add_parser('clean', help='Clean build artifacts')

    # Init command
    init_parser = subparsers.add_parser('init', help='Initialize new project')
    init_parser.add_argument('name', nargs='?', help='Project name')

    # Version command
    subparsers.add_parser('version', help='Show version')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Handle version
    if args.command == 'version':
        print("MosaiK8 Build Tool v1.0.0")
        return 0

    # Create builder
    builder = MosaikBuilder()
    if builder.gbdk.gbdk_path:
        version_type = builder.gbdk.version_info.get('type', 'Unknown')
        print(f"Detected: {version_type} at {builder.gbdk.gbdk_path}")
        print("✅ GBDK-2020 features enabled")
    else:
        print("❌ GBDK not found")

    # Execute command
    try:
        if args.command == 'build':
            success = builder.build(args.target, args.platform, args.debug,
                                    args.assets, args.all_platforms)
            return 0 if success else 1

        elif args.command == 'clean':
            success = builder.clean()
            return 0 if success else 1

        elif args.command == 'init':
            success = builder.init_project(args.name)
            return 0 if success else 1

        else:
            parser.print_help()
            return 1

    except KeyboardInterrupt:
        print("\nBuild interrupted.")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
