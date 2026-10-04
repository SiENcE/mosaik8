"""`python -m mosaik_vm ...` entry point (was `python mosaik_vm.py ...`)."""
from .cli import main

raise SystemExit(main())
