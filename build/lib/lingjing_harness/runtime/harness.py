from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid
from typing import Any, Callable

from lingjing_harness.domain import Catalog
from .contracts import RunBudget, RunState
from .memory import AgentMemory, catalog_fingerprint
from .policy import OwnedPolicy
from .tools import ToolRegistry
from .verifier import ResultVerifier

EventSink = Callable[[dict[str, Any]], None]
CheckpointSink = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class RunEvent:
    phase: str
    title: str
    detail: str
    progress: int
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "title": self.title,
            "detail": self.detail,
            "progress": self.progress,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class AgentHarness:
    """Autonomous search/recommendation harness with persistent memory and eval-gated self-evolution."""

    def __init__(
        self,
        catalog: Catalog,
        max_tools: int = 14,
        *,
        memory: AgentMemory | None = None,
        budget: RunBudget | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.catalog = catalog
        self.memory = memory or (tools.memory if tools is not None else AgentMemory())
        self.catalog_key = tools.catalog_key if tools is not None else catalog_fingerprint(catalog)
        self.policy = OwnedPolicy()
        self.tools = tools or ToolRegistry(catalog, self.memory)
        self.verifier = ResultVerifier()
        self.budget = budget or RunBudget(max_tools=max_tools)

    def fork(self) -> "AgentHarness":
        """Fork isolated execution state while reusing immutable search/recommend features."""
        return AgentHarness(
            self.catalog,
            memory=self.memory,
            budget=RunBudget(
                max_tools=self.budget.max_tools,
                max_cost=self.budget.max_cost,
                max_seconds=self.budget.max_seconds,
            ),
            tools=self.tools.fork(),
        )

    def replace_catalog(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.catalog_key = catalog_fingerprint(catalog)
        self.tools.replace_catalog(catalog)

    @staticmethod
    def _emit(
        events: list[RunEvent],
        sink: EventSink | None,
        phase: str,
        title: str,
        detail: str,
        progress: int,
        **payload: Any,
    ) -> None:
        event = RunEvent(phase, title, detail, progress, payload)
        events.append(event)
        if sink:
            sink(event.dict())

    def run(
        self,
        text: str,
        *,
        sink: EventSink | None = None,
        checkpoint_sink: CheckpointSink | None = None,
        resume: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        plan = self.policy.plan(text, self.catalog)
        memory_hits = self.memory.recall(self.catalog_key, plan.goal, plan.mode, limit=4)
        if resume:
            run_id, state, events = self._rehydrate(resume, plan)
            self._emit(
                events,
                sink,
                "resume",
                "恢复未完成执行",
                f"从第 {state.cycle} 个已记录动作之后继续，不重复执行已完成工具",
                min(86, 14 + state.cycle * 6),
                resumed_actions=len(state.actions),
                spent_cost=round(state.spent_cost, 3),
            )
        else:
            run_id = f"run-{uuid.uuid4().hex[:10]}"
            state = RunState()
            events: list[RunEvent] = []
            self._emit(events, sink, "observe", "读取当前工作区", "确认当前数据、目标和执行边界", 5, mode=plan.mode)
            if memory_hits:
                self._emit(
                    events,
                    sink,
                    "memory",
                    "回看相关经验",
                    f"找到 {len(memory_hits)} 条与当前目标相关的历史执行经验",
                    9,
                    memories=[{"goal": row["goal"], "score": row["score"]} for row in memory_hits],
                )
            if plan.constraints:
                self._emit(events, sink, "guard", "锁定执行约束", "；".join(plan.constraints), 11, allow_adaptation=plan.allow_adaptation)

        while state.cycle < self.budget.max_tools:
            if time.monotonic() - started > self.budget.max_seconds:
                state.findings.append("本次自主执行达到时间预算，已停止继续扩展动作")
                break
            decision = self.policy.decide(
                plan,
                state,
                self.tools.list_specs(),
                policy_bonus=lambda action_key: self.memory.policy_bonus(plan.mode, action_key),
            )
            if decision.step is None:
                break
            spec = self.tools.get(decision.step.tool)
            if state.spent_cost + spec.cost > self.budget.max_cost:
                state.findings.append("本次自主执行达到工具预算，已保留现有证据并停止扩展动作")
                break

            state.cycle += 1
            progress = min(86, 14 + state.cycle * 6)
            decision_record = {
                "cycle": state.cycle,
                "tool": decision.step.tool,
                "reason": decision.rationale,
                "score": decision.score,
                "learned_bonus": decision.learned_bonus,
                "alternatives": decision.alternatives,
            }
            state.decisions.append(decision_record)
            self._emit(
                events,
                sink,
                "decide",
                "自主决定下一步",
                decision.rationale,
                max(12, progress - 2),
                **decision_record,
            )
            self._emit(
                events,
                sink,
                "execute",
                decision.step.title,
                decision.step.detail,
                progress,
                tool=spec.name,
                risk=spec.risk,
                cost=spec.cost,
            )
            invocation_id = f"{run_id}:{state.cycle}:{spec.name}"
            action = {
                "invocation_id": invocation_id,
                "tool": spec.name,
                "risk": spec.risk,
                "cost": spec.cost,
                "input": decision.step.args,
                "decision": decision_record,
                "status": "completed",
            }
            try:
                result = self.tools.execute(
                    spec.name,
                    decision.step.args,
                    allow_adaptation=plan.allow_adaptation,
                    invocation_id=invocation_id,
                )
                action["result"] = result
                state.observations[spec.name] = result
                state.spent_cost += spec.cost
                self._consume(spec.name, result, state)
            except Exception as exc:
                action["status"] = "failed"
                action["error"] = f"{type(exc).__name__}: {exc}"
                action["result"] = {}
                state.findings.append(f"{decision.step.title}执行失败，系统已停止依赖该结果：{type(exc).__name__}")
            state.actions.append(action)
            if checkpoint_sink:
                checkpoint_sink(self._checkpoint(run_id, plan, state, events))

        self._emit(events, sink, "verify", "独立核对结论", "检查证据完整性、执行异常、策略门槛和用户约束", 92)
        state.findings = list(dict.fromkeys(item for item in state.findings if item))
        if not state.findings:
            state.findings = ["本次执行未发现阻断性问题"]
        verification = self.verifier.final(state.actions, state.findings, state.evidence, allow_adaptation=plan.allow_adaptation)
        learned = self._learned_events(state.actions)
        suggestions = self._suggestions(plan.mode, plan.compare, state.findings, learned)
        answer = self._answer(state.blocks, state.findings, suggestions, learned)
        reward = self._reward(verification, state, learned)
        action_keys = [row["tool"] for row in state.actions if row.get("status") == "completed"]
        self.memory.update_policy(plan.mode, [f"{plan.mode}|{key}" for key in action_keys], reward)
        self.memory.record_episode(
            self.catalog_key,
            plan.goal,
            plan.mode,
            reward,
            findings=state.findings,
            action_keys=action_keys,
            learned=learned,
        )
        self._emit(
            events,
            sink,
            "complete",
            "形成可执行结论",
            "已完成自主决策、证据核对和经验更新",
            100,
            reward=round(reward, 4),
            learned=len(learned),
        )
        result = {
            "run_id": run_id,
            "answer": answer,
            "plan": {
                "mode": plan.mode,
                "query": plan.query,
                "user_id": plan.user_id,
                "compare": plan.compare,
                "allow_adaptation": plan.allow_adaptation,
                "constraints": list(plan.constraints),
            },
            "events": [event.dict() for event in events],
            "findings": state.findings[:8],
            "evidence": state.evidence[:12],
            "suggestions": suggestions,
            "actions": state.actions,
            "decisions": state.decisions,
            "verification": verification,
            "autonomy": {
                "dynamic_replan": True,
                "cycles": state.cycle,
                "spent_cost": round(state.spent_cost, 3),
                "budget": {
                    "max_tools": self.budget.max_tools,
                    "max_cost": self.budget.max_cost,
                    "max_seconds": self.budget.max_seconds,
                },
                "memory_hits": len(memory_hits),
                "policy_learning": True,
                "constraints_respected": verification["checks"]["adaptation_respected"],
            },
            "evolution": {
                "learned": learned,
                "memory": self.memory.stats(self.catalog_key),
                "eval_gated": True,
                "automatic_rollback": True,
            },
            "durability": {
                "resumed": bool(resume),
                "checkpoint_resume": True,
                "idempotent_adaptive_tools": True,
            },
            "data": self.catalog.summary(),
            "owned_policy": True,
            "self_evolving": True,
        }
        if checkpoint_sink:
            checkpoint_sink({**self._checkpoint(run_id, plan, state, events), "status": "completed", "result": result})
        return result

    def _consume(self, tool: str, result: dict[str, Any], state: RunState) -> None:
        if tool == "data.inspect":
            state.findings.extend(result.get("issues", []))
            memory = result.get("memory", {})
            if memory.get("skills"):
                state.blocks.append(f"当前工作区已经积累 {memory['skills']} 条通过验证的策略经验。")
            rollbacks = result.get("rollbacks") or []
            if rollbacks:
                state.findings.append("系统检测到已学习策略出现回退，已自动恢复到稳健策略")
                state.blocks.append("系统在执行前发现历史策略出现回退，已经自动完成回滚。")
            return
        if tool == "search.run":
            rows = result.get("results", [])
            state.findings.extend(self.verifier.search(rows))
            state.evidence.extend(
                {"kind": "result", "title": row["title"], "detail": f"当前第 {row['rank']} 位", "score": row["score"]}
                for row in rows[:4]
            )
            if rows:
                state.blocks.append(f"搜索“{result['query']}”当前最靠前的是：" + "、".join(row["title"] for row in rows[:3]) + "。")
            else:
                state.blocks.append(f"搜索“{result['query']}”当前没有可展示结果。")
            return
        if tool == "search.diagnose":
            state.blocks.append("进一步诊断显示：" + result.get("diagnosis", "已完成查询证据检查") + "。")
            return
        if tool == "search.audit":
            if result.get("queries"):
                prefix = f"从 {result.get('available_queries', result['queries'])} 个已知查询中抽样 {result['queries']} 个，" if result.get("sampled") else f"用 {result['queries']} 个已知查询，"
                state.blocks.append(prefix + f"整体搜索质量约为 {result['quality']:.0%}。")
            else:
                state.blocks.append("当前没有人工复核查询，因此搜索只能做结构性检查。")
            return
        if tool == "search.evolve":
            state.findings.extend(self.verifier.experiment(result))
            if not result.get("evaluation_ready"):
                state.blocks.append("当前搜索证据不足，系统没有生成可晋升的策略经验。")
            elif result.get("trusted"):
                delta = result.get("delta", {})
                if result.get("activated"):
                    state.blocks.append(f"系统自主筛选出的搜索策略通过稳健门槛，并已用于当前工作区；质量变化 {delta.get('quality', 0):+.1%}。")
                else:
                    state.blocks.append(f"系统自主筛选出的搜索策略通过稳健门槛，已写入长期经验但没有改变当前策略；质量变化 {delta.get('quality', 0):+.1%}。")
            else:
                state.blocks.append("系统自动探索了多组搜索策略，但没有发现足够稳定的优势，因此没有晋升任何候选。")
            return
        if tool == "recommend.run":
            rows = result.get("results", [])
            state.findings.extend(self.verifier.recommend(rows))
            state.evidence.extend(
                {"kind": "result", "title": row["title"], "detail": f"当前第 {row['rank']} 位", "score": row["score"]}
                for row in rows[:4]
            )
            if rows:
                state.blocks.append(f"用户 {result['user_id']} 当前最靠前的是：" + "、".join(row["title"] for row in rows[:3]) + "。")
            else:
                state.blocks.append(f"用户 {result['user_id']} 当前没有可展示内容。")
            return
        if tool == "recommend.diagnose":
            state.blocks.append("进一步诊断显示：" + result.get("diagnosis", "已完成推荐约束检查") + "。")
            return
        if tool == "recommend.audit":
            prefix = f"抽样检查 {result.get('users', 0)}/{result.get('available_users', result.get('users', 0))} 个用户后，" if result.get("sampled") else ""
            state.blocks.append(prefix + f"当前内容覆盖约 {result.get('coverage', 0):.0%}，新鲜度约 {result.get('freshness', 0):.0%}，结果分散度约 {result.get('diversity', 0):.0%}。")
            return
        if tool == "recommend.evolve":
            state.findings.extend(self.verifier.experiment(result))
            if not result.get("evaluation_ready"):
                state.blocks.append("当前推荐证据不足，系统没有生成可晋升的策略经验。")
            elif result.get("trusted"):
                delta = result.get("delta", {})
                if result.get("activated"):
                    state.blocks.append(f"系统自主筛选出的推荐策略通过稳健门槛，并已用于当前工作区；覆盖变化 {delta.get('coverage', 0):+.1%}。")
                else:
                    state.blocks.append(f"系统自主筛选出的推荐策略通过稳健门槛，已写入长期经验但没有改变当前策略；覆盖变化 {delta.get('coverage', 0):+.1%}。")
            else:
                state.blocks.append("系统自动探索了多组推荐策略，但没有发现足够稳定的优势，因此没有晋升任何候选。")

    @staticmethod
    def _learned_events(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for action in actions:
            result = action.get("result", {})
            if action.get("status") != "completed" or not result.get("learned"):
                continue
            rows.append({
                "domain": "search" if action["tool"].startswith("search") else "recommend",
                "skill": result.get("skill", {}),
                "activated": bool(result.get("activated")),
                "delta": result.get("delta", {}),
                "candidates": result.get("candidate_count", 0),
                "generations": result.get("generations", 0),
            })
        return rows

    @staticmethod
    def _reward(verification: dict[str, Any], state: RunState, learned: list[dict[str, Any]]) -> float:
        reward = 0.48
        reward += 0.18 if verification.get("passed") else -0.20
        reward += 0.12 if state.evidence else 0.02
        reward += 0.12 if learned else 0.0
        reward += 0.08 if state.cycle <= 8 else 0.02
        reward -= 0.08 * sum(1 for row in state.actions if row.get("status") == "failed")
        return max(0.0, min(1.0, reward))

    @staticmethod
    def _suggestions(mode: str, compare: bool, findings: list[str], learned: list[dict[str, Any]]) -> list[str]:
        if mode == "search":
            rows = ["把最差的查询样本展开", "检查无结果与低相关查询", "允许自主优化后再复核一次"]
        elif mode == "recommend":
            rows = ["换一个用户继续看", "检查新用户首屏体验", "允许自主优化后再复核一次"]
        elif mode == "both":
            rows = ["先深入搜索问题", "先深入推荐问题", "允许系统持续学习并复核"]
        else:
            rows = ["先看搜索体验", "先看推荐体验", "导入我的真实数据"]
        if learned:
            rows[2] = "复核刚学到的策略经验"
        elif compare and any("没有形成" in item or "不" in item for item in findings):
            rows[2] = "继续探索新的候选策略"
        return rows

    @staticmethod
    def _answer(blocks: list[str], findings: list[str], suggestions: list[str], learned: list[dict[str, Any]]) -> str:
        body = "\n".join(f"- {item}" for item in findings[:4])
        nexts = "\n".join(f"- {item}" for item in suggestions[:3])
        lead = " ".join(blocks) if blocks else "我已经根据当前证据自主完成了本次检查。"
        learned_line = ""
        if learned:
            domains = "、".join("搜索" if row["domain"] == "search" else "推荐" for row in learned)
            learned_line = f"\n\n### 本次学习\n- {domains}策略产生了通过稳健门槛的新经验，后续任务会优先参考。"
        return f"### 结论\n{lead}\n\n### 需要注意\n{body}{learned_line}\n\n### 下一步\n{nexts}"

    @staticmethod
    def _rehydrate(payload: dict[str, Any], plan) -> tuple[str, RunState, list[RunEvent]]:
        saved_plan = payload.get("plan") or {}
        if saved_plan.get("mode") and saved_plan.get("mode") != plan.mode:
            raise ValueError("checkpoint 与当前任务模式不一致，拒绝恢复")
        state = RunState(
            cycle=max(0, int(payload.get("cycle", 0) or 0)),
            spent_cost=max(0.0, float(payload.get("spent_cost", 0.0) or 0.0)),
            actions=list(payload.get("actions") or []),
            observations=dict(payload.get("observations") or {}),
            findings=list(payload.get("findings") or []),
            evidence=list(payload.get("evidence") or []),
            blocks=list(payload.get("blocks") or []),
            decisions=list(payload.get("decisions") or []),
        )
        events = []
        for row in payload.get("events") or []:
            if not isinstance(row, dict):
                continue
            events.append(
                RunEvent(
                    phase=str(row.get("phase") or "execute"),
                    title=str(row.get("title") or "恢复记录"),
                    detail=str(row.get("detail") or ""),
                    progress=max(0, min(100, int(row.get("progress", 0) or 0))),
                    payload=dict(row.get("payload") or {}),
                    created_at=float(row.get("created_at", time.time()) or time.time()),
                )
            )
        return str(payload.get("run_id") or f"run-{uuid.uuid4().hex[:10]}"), state, events

    @staticmethod
    def _checkpoint(run_id: str, plan, state: RunState, events: list[RunEvent]) -> dict[str, Any]:
        return {
            "status": "running",
            "run_id": run_id,
            "goal": plan.goal,
            "plan": {
                "mode": plan.mode,
                "query": plan.query,
                "user_id": plan.user_id,
                "compare": plan.compare,
                "allow_adaptation": plan.allow_adaptation,
            },
            "cycle": state.cycle,
            "spent_cost": state.spent_cost,
            "actions": state.actions,
            "observations": state.observations,
            "findings": state.findings,
            "evidence": state.evidence,
            "blocks": state.blocks,
            "decisions": state.decisions,
            "events": [event.dict() for event in events],
            "updated_at": time.time(),
        }
