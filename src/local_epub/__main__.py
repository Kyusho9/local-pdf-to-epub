import sys
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "--worker" in sys.argv:
        sys.argv.remove("--worker")
    if len(sys.argv) > 1 and sys.argv[1] == "gui-test":
        from .gui_acceptance import main
        sys.exit(main(sys.argv[2:]))
    if len(sys.argv) == 1 or sys.argv[1] == "gui":
        from .gui import main
    else:
        from .cli import main
    sys.exit(main())
