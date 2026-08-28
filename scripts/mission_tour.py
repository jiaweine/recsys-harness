from __future__ import annotations

from lingjing_harness.runtime import AgentHarness
from lingjing_harness.sample_data import build_sample_catalog


MISSIONS = (
    (
        "01",
        "Workspace Audit",
        "只读体检：先理解工作区，再决定需要什么证据。",
        "做一次全局体检，只检查，不修改",
    ),
    (
        "02",
        "Search Diagnosis + Candidate Exploration",
        "围绕一个真实 query 诊断、探索候选，但不授予激活权限。",
        "最近搜索“露营灯”的结果不准，帮我优化，但先不要上线",
    ),
    (
        "03",
        "Permissioned Recommendation Evolution",
        "显式授予策略调整权限，展示 trust 与 authority 分层。",
        "推荐体验需要优化，检查用户 u-xu，允许调整策略",
    ),
)


def _rule(char: str = "─", width: int = 76) -> str:
    return char * width


def _completed_tools(result: dict) -> list[str]:
    return [
        str(action.get("tool"))
        for action in result.get("actions", [])
        if action.get("status") == "completed" and action.get("tool")
    ]


def _status(result: dict) -> str:
    verification = result.get("verification", {})
    passed = bool(verification.get("passed"))
    return "VERIFIED" if passed else "EVIDENCE REVIEW"


def _authority(result: dict) -> str:
    plan = result.get("plan", {})
    return "ADAPTATION ALLOWED" if plan.get("allow_adaptation") else "READ ONLY"


def _print_result(number: str, title: str, note: str, prompt: str, result: dict) -> None:
    plan = result.get("plan", {})
    critic = result.get("deliberation", {}).get("critic", {}) or {}
    autonomy = result.get("autonomy", {})
    tools = _completed_tools(result)

    print()
    print(_rule("═"))
    print(f"{number} · {title}")
    print(note)
    print(_rule())
    print(f"MISSION   {prompt}")
    print(f"MODE      {plan.get('mode', '-')}  ·  {_authority(result)}")
    if plan.get("query"):
        print(f"QUERY     {plan['query']}")
    if plan.get("user_id"):
        print(f"USER      {plan['user_id']}")
    print(f"TRACE     {' → '.join(tools) if tools else 'no completed tool'}")
    print(
        "EVIDENCE  "
        f"{len(result.get('evidence', []))} items  ·  "
        f"{autonomy.get('cycles', 0)} cycles  ·  "
        f"critic {float(critic.get('confidence', 0.0) or 0.0):.0%}"
    )
    print(f"STATUS    {_status(result)}")
    print()
    print(result.get("answer", "No answer returned."))


def main() -> None:
    print(_rule("═"))
    print("XUSHU · 3-MINUTE MISSION TOUR")
    print("Audit → Search → Recommendation · evidence first · authority explicit")
    print(_rule("═"))
    print("使用仓库内置示例数据运行三个真实 Agent Harness mission。")
    print("每个 mission 都会经过任务图、工具执行、反思、critic 与 verifier。")

    harness = AgentHarness(build_sample_catalog())
    for number, title, note, prompt in MISSIONS:
        result = harness.run(prompt)
        _print_result(number, title, note, prompt, result)

    print()
    print(_rule("═"))
    print("TOUR COMPLETE")
    print("同一个 Harness 已连续经历只读审计、候选探索和显式授权的策略进化。")
    print("下一步：make run，然后打开 http://127.0.0.1:8765 进入完整工作台。")
    print(_rule("═"))


if __name__ == "__main__":
    main()
