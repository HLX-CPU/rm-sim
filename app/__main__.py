# -*- coding: utf-8 -*-
"""支持 ``python -m app`` 启动（与根目录 main.py 等效的统一入口）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 使 rm_sim 可导入

from rm_sim.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
