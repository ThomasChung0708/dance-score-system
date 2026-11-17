"""Entry point of the packaged score tool (DanceScore.exe).

The exe runs the local web GUI in game folder mode: songs and results are read
straight from the game, so the OCR packages are left out (see BuildExe.cmd).
Data is kept in a "data" folder next to the exe (dance_results/app_paths.py).
"""
from dance_results.gui import main

if __name__ == '__main__':
    raise SystemExit(main())
