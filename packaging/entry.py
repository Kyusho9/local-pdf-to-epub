"""PyInstaller entry for both GUI and console workers."""
import runpy
import sys
import multiprocessing

multiprocessing.freeze_support()

if "-CLI" in sys.executable and "--worker" not in sys.argv:
    sys.argv.insert(1, "--worker")

runpy.run_module("local_epub", run_name="__main__")
