from __future__ import annotations

import argparse, json
from .sample_data import build_sample_catalog
from .runtime import AgentHarness


def main()->None:
    p=argparse.ArgumentParser(description="Xushu search/recommendation agent harness")
    p.add_argument("prompt",nargs="*",default=["做一次全局体检"])
    args=p.parse_args()
    result=AgentHarness(build_sample_catalog()).run(" ".join(args.prompt))
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
