#!/usr/bin/env python3
from multiprocessing import freeze_support

if __name__ == "__main__":
    freeze_support()
    from sceneaccess.gui import main
    main()
