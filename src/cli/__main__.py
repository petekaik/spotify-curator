"""Entry point for the `spotify-curator` console command.

Allows the package to be installed and run as:
    pip install -e .
    spotify-curator auth

Or directly:
    python -m src.cli.main
"""
from src.cli.main import main

if __name__ == "__main__":
    main()
