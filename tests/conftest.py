"""测试会话全局默认关闭自动补算，避免索引类测试误拉起真实嵌入子进程。

需要验证自动补算行为的测试，用 mock.patch.dict(os.environ, {"KB_AUTO_EMBED": "1"}) 显式打开。
"""
from __future__ import annotations

import os

os.environ["KB_AUTO_EMBED"] = "0"
