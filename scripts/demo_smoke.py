from __future__ import annotations

import sys
from pathlib import Path

# Keep this script runnable exactly as documented: `python scripts/demo_smoke.py`.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.runtime import AgentHarness


def main() -> None:
    prompts = [
        '最近搜索“露营灯”不准，帮我优化但先不要上线',
        '看看用户 u-lin 的推荐首屏，给我一个可验证的改进方案',
        '做一次全局体检',
    ]
    for prompt in prompts:
        result = AgentHarness(build_sample_catalog()).run(prompt)
        print("\n>", prompt)
        print(result["answer"])


if __name__ == "__main__":
    main()
