"""Support python -m orders_pipeline as well as the installed console command."""

from orders_pipeline.cli import main

raise SystemExit(main())
