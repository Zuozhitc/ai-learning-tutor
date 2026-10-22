"""Run: python -m tutor_rag search '分类输出概率为什么不是回归？'"""

import argparse
import json
import sys
import time
from pathlib import Path

from .engine import MODES, ROOT, Retriever


def evaluate(retriever, fixtures):
    reports = {}
    for mode in MODES:
        records = []
        for item in fixtures:
            start = time.perf_counter()
            hits = retriever.search(item["question"], mode)
            elapsed = (time.perf_counter() - start) * 1000
            ids = [h.card_id for h in hits]
            relevant = set(item["relevant_ids"])
            rank = next((i + 1 for i, key in enumerate(ids) if key in relevant), None)
            records.append(dict(id=item["id"], question=item["question"], kind=item["kind"],
                                retrieved_ids=ids, relevant_ids=sorted(relevant), rank=rank,
                                elapsed_ms=round(elapsed, 4)))
        in_scope = [r for r in records if r["relevant_ids"]]
        outside = [r for r in records if not r["relevant_ids"]]
        bridges = [r for r in records if r["kind"] == "bridge"]
        reports[mode] = dict(
            in_scope_count=len(in_scope), unsupported_count=len(outside),
            hit_at_1=sum(r["rank"] == 1 for r in in_scope) / max(1, len(in_scope)),
            hit_at_3=sum(r["rank"] is not None for r in in_scope) / max(1, len(in_scope)),
            mrr_at_3=sum(1 / r["rank"] if r["rank"] else 0 for r in in_scope) / max(1, len(in_scope)),
            bridge_hit_at_3=sum(r["rank"] is not None for r in bridges) / max(1, len(bridges)),
            unsupported_with_candidates=sum(bool(r["retrieved_ids"]) for r in outside),
            mean_search_ms=sum(r["elapsed_ms"] for r in records) / max(1, len(records)),
            records=records)
    return {"corpus_size": len(retriever.cards), "question_count": len(fixtures),
            "test_type": "author_written_diagnostic_not_blind_benchmark",
            "generation_tested": False, "reports": reports}


def report_markdown(report):
    rows = ["# 检索诊断报告", "", "这是同一作者编写的小型诊断集，不是独立盲测或教学效果验证。",
            "未调用生成模型；下列分数只描述教材卡召回。增强规则未根据本次分数反复调参。", "",
            f'教材卡：{report["corpus_size"]}；问题：{report["question_count"]}。', "",
            "| 方式 | Hit@1 | Hit@3 | MRR@3 | 关联卡 Hit@3 | 无支持问题仍有候选 | 平均检索 ms |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for mode, r in report["reports"].items():
        rows.append(f'| {mode} | {r["hit_at_1"]:.1%} | {r["hit_at_3"]:.1%} | {r["mrr_at_3"]:.3f} | '
                    f'{r["bridge_hit_at_3"]:.1%} | {r["unsupported_with_candidates"]}/{r["unsupported_count"]} | '
                    f'{r["mean_search_ms"]:.3f} |')
    rows += ["", "Hit@k 指人工标注的可接受教材卡至少有一张出现在前 k 个候选，不能据此推断完整回答正确。",
             "无支持问题即使有候选卡也不代表可回答；词法方法尤其无法可靠判断语义覆盖范围。", "",
             "## 逐题结果", ""]
    for mode, r in report["reports"].items():
        rows += [f"### {mode}", "", "| 问题 | 期望卡 | 实际前3卡 | 首个相关排名 |", "| --- | --- | --- | --- |"]
        for rec in r["records"]:
            rows.append(f'| {rec["question"]} | {", ".join(rec["relevant_ids"]) or "库中不支持"} | '
                        f'{", ".join(rec["retrieved_ids"]) or "无命中"} | {rec["rank"] or "—"} |')
        rows.append("")
    rows += ["## 限制", "", "- 当前中文分词采用字符二元组，概念增强依赖人工维护的别名表。",
             "- 无向量模型、向量数据库或大模型调用；未验证8B生成、引用忠实度与真实教学效果。",
             "- 无支持的问题与课程词汇重叠时仍会召回材料；需要生成端的依据检查及更多真实问题验证。",
             "- 下一步增加其他学习者的问题和人工相关性标注，再与真实嵌入模型检索做对比。", ""]
    return "\n".join(rows)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="AI基础教学资料检索原型，无需模型或外部依赖")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("search", "prompt"):
        p = commands.add_parser(command)
        p.add_argument("question")
        p.add_argument("--mode", choices=MODES, default="bm25")
        p.add_argument("--top-k", type=int, default=3)
        if command == "prompt":
            p.add_argument("--max-chars", type=int, default=5000)
    ev = commands.add_parser("evaluate")
    ev.add_argument("--cases", type=Path, default=ROOT / "eval" / "retrieval_cases.json")
    ev.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        retriever = Retriever()
        if args.command == "search":
            result = retriever.result(args.question, args.mode, args.top_k)
        elif args.command == "prompt":
            result = retriever.prompt(args.question, args.mode, args.top_k, args.max_chars)
        else:
            result = evaluate(retriever, json.loads(args.cases.read_text(encoding="utf-8")))
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                (args.out / "retrieval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                (args.out / "retrieval.md").write_text(report_markdown(result), encoding="utf-8")
                result = {k: v for k, v in result.items() if k != "reports"} | {
                    "reports": {k: {a: b for a, b in v.items() if a != "records"} for k, v in result["reports"].items()}}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
