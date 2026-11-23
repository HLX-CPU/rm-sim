# -*- coding: utf-8 -*-
"""统一主入口：``python main.py`` 启动 GUI；``python main.py --selfcheck`` 无头自检。

实现位于 app/rm_sim/main.py；本文件只做路径引导，
保证「python main.py」「python -m app」两种启动方式行为一致。
"""
from __future__ import annotations

import os
import sys

_APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from rm_sim.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
