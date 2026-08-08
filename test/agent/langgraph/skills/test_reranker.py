"""Stage 5 候选重排单元测试（P2，§10.7）。

覆盖 SkillReranker 的 LLM 调用、超时降级、JSON 解析校验、
候选集外 skill_id 丢弃、分数 clamp、Top-K 截断与召回分数降级路径。
"""
from __future__ import annotations

import asyncio
import json

from agent.langgraph.skills.catalog_models import SkillCard
from agent.langgraph.skills.reranker import (
    SkillReranker,
    _build_candidates_block,
)
from agent.langgraph.skills.retrievers import ScoredCard


def _make_card(**overrides) -> SkillCard:
    """构造测试用 SkillCard。"""
    defaults = dict(
        skill_id="test_skill",
        version="1.0",
        skill_type="report",
        namespace="test.report.test_skill",
        name="Test",
        description="test desc",
    )
    defaults.update(overrides)
    return SkillCard(**defaults)


def _make_scored(skill_id: str, score: float, source: str = "bm25", **card_overrides) -> ScoredCard:
    """构造测试用 ScoredCard。"""
    card = _make_card(skill_id=skill_id, **card_overrides)
    return ScoredCard(card=card, score=score, source=source)


# ========== 降级路径 ==========


def test_rerank_llm_unavailable_degrades():
    """llm_call_func=None 时降级到召回分数排序。"""
    reranker = SkillReranker(llm_call_func=None)
    candidates = [
        _make_scored("s1", 0.5),
        _make_scored("s2", 0.9),
        _make_scored("s3", 0.7),
    ]
    decision = asyncio.run(reranker.rerank("query", candidates))
    assert decision.rerank_degraded is True
    # 降级后按召回分数降序
    assert [c.skill_id for c in decision.candidates] == ["s2", "s3", "s1"]
    assert decision.candidates[0].score == 0.9


def test_rerank_timeout_degrades():
    """LLM 调用超时时降级到召回分数排序。"""
    async def slow_llm(prompt: str) -> str:
        await asyncio.sleep(0.5)
        return "{}"

    reranker = SkillReranker(llm_call_func=slow_llm, timeout_ms=50)
    candidates = [_make_scored("s1", 0.8)]
    decision = asyncio.run(reranker.rerank("query", candidates))
    assert decision.rerank_degraded is True
    assert decision.candidates[0].skill_id == "s1"


def test_rerank_invalid_json_degrades():
    """LLM 返回非法 JSON 时降级。"""
    async def bad_json_llm(prompt: str) -> str:
        return "not a json {{{"

    reranker = SkillReranker(llm_call_func=bad_json_llm)
    candidates = [_make_scored("s1", 0.8)]
    decision = asyncio.run(reranker.rerank("query", candidates))
    assert decision.rerank_degraded is True
    assert decision.candidates[0].skill_id == "s1"


def test_rerank_all_invalid_falls_back():
    """LLM 返回的 skill_id 全部无效时降级到召回分数排序。"""
    async def mock_llm(prompt: str) -> str:
        return json.dumps({
            "candidates": [
                {"skill_id": "ghost1", "score": 0.9, "reason": ""},
                {"skill_id": "ghost2", "score": 0.8, "reason": ""},
            ],
            "multi_intent": False,
        })

    reranker = SkillReranker(llm_call_func=mock_llm)
    candidates = [_make_scored("s1", 0.7), _make_scored("s2", 0.3)]
    decision = asyncio.run(reranker.rerank("query", candidates))
    assert decision.rerank_degraded is True
    # 降级后按召回分数降序
    assert [c.skill_id for c in decision.candidates] == ["s1", "s2"]


def test_fallback_to_recall_scores_sorted():
    """降级时按召回分数降序排序并标注 reason。"""
    reranker = SkillReranker(llm_call_func=None)
    candidates = [
        _make_scored("low", 0.3, source="bm25"),
        _make_scored("high", 0.95, source="embedding"),
        _make_scored("mid", 0.6, source="rrf"),
    ]
    decision = asyncio.run(reranker.rerank("query", candidates))
    assert decision.rerank_degraded is True
    assert [c.skill_id for c in decision.candidates] == ["high", "mid", "low"]
    assert decision.candidates[0].score == 0.95
    assert "recall fallback" in decision.candidates[0].reason


# ========== 正常 LLM 调用路径 ==========


def test_rerank_successful_llm_call():
    """LLM 返回合法 JSON 时正确解析重排结果。"""
    async def mock_llm(prompt: str) -> str:
        return json.dumps({
            "candidates": [
                {"skill_id": "s2", "score": 0.95, "reason": "best match"},
                {"skill_id": "s1", "score": 0.6, "reason": "partial"},
            ],
            "multi_intent": False,
            "missing_context": ["time_range"],
            "ambiguity_reason": None,
        })

    reranker = SkillReranker(llm_call_func=mock_llm)
    candidates = [_make_scored("s1", 0.5), _make_scored("s2", 0.4)]
    decision = asyncio.run(reranker.rerank("query", candidates))
    assert decision.rerank_degraded is False
    assert len(decision.candidates) == 2
    assert decision.candidates[0].skill_id == "s2"
    assert decision.candidates[0].score == 0.95
    assert decision.candidates[0].reason == "best match"
    assert decision.multi_intent is False
    assert decision.missing_context == ["time_range"]
    assert decision.ambiguity_reason is None


def test_rerank_skill_id_not_in_candidates_dropped():
    """LLM 返回的候选集外 skill_id 被丢弃。"""
    async def mock_llm(prompt: str) -> str:
        return json.dumps({
            "candidates": [
                {"skill_id": "s1", "score": 0.9, "reason": ""},
                {"skill_id": "unknown_skill", "score": 0.99, "reason": ""},
            ],
            "multi_intent": False,
        })

    reranker = SkillReranker(llm_call_func=mock_llm)
    candidates = [_make_scored("s1", 0.5), _make_scored("s2", 0.4)]
    decision = asyncio.run(reranker.rerank("query", candidates))
    assert decision.rerank_degraded is False
    ids = [c.skill_id for c in decision.candidates]
    assert ids == ["s1"]
    assert "unknown_skill" not in ids


def test_rerank_score_clamped():
    """LLM 返回的分数被 clamp 到 [0, 1]。"""
    async def mock_llm(prompt: str) -> str:
        return json.dumps({
            "candidates": [
                {"skill_id": "s1", "score": 1.5, "reason": ""},
                {"skill_id": "s2", "score": -0.5, "reason": ""},
            ],
            "multi_intent": False,
        })

    reranker = SkillReranker(llm_call_func=mock_llm)
    candidates = [_make_scored("s1", 0.5), _make_scored("s2", 0.4)]
    decision = asyncio.run(reranker.rerank("query", candidates))
    scores = {c.skill_id: c.score for c in decision.candidates}
    assert scores["s1"] == 1.0
    assert scores["s2"] == 0.0


# ========== 截断 ==========


def test_rerank_truncates_to_top_k():
    """输出候选数被截断到 output_top_k（5）。"""
    raw = [{"skill_id": f"s{i}", "score": 0.9 - i * 0.05, "reason": ""} for i in range(7)]

    async def mock_llm(prompt: str) -> str:
        return json.dumps({"candidates": raw, "multi_intent": False})

    reranker = SkillReranker(llm_call_func=mock_llm, output_top_k=5)
    candidates = [_make_scored(f"s{i}", 0.5) for i in range(7)]
    decision = asyncio.run(reranker.rerank("query", candidates))
    assert len(decision.candidates) == 5


def test_rerank_truncates_candidates():
    """输入候选数被截断到 max_candidates（10），超出部分不进入 prompt。"""
    captured = {"prompt": ""}

    async def mock_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps({
            "candidates": [{"skill_id": "s0", "score": 0.9, "reason": ""}],
            "multi_intent": False,
        })

    reranker = SkillReranker(llm_call_func=mock_llm, max_candidates=10)
    candidates = [_make_scored(f"s{i}", 0.5) for i in range(12)]
    asyncio.run(reranker.rerank("query", candidates))
    # 前 10 个候选进入 prompt，s10/s11 不在
    assert "[s0]" in captured["prompt"]
    assert "[s9]" in captured["prompt"]
    assert "[s10]" not in captured["prompt"]
    assert "[s11]" not in captured["prompt"]


# ========== _build_candidates_block ==========


def test_build_candidates_block_includes_positive_negative():
    """_build_candidates_block 包含正例和负例文本。"""
    card = _make_card(
        skill_id="s1",
        name="测试Skill",
        description="描述",
        positive_examples=["质量异常", "缺陷率上升"],
        negative_examples=["闲聊", "打招呼"],
    )
    scored = ScoredCard(card=card, score=0.5, source="bm25")
    block = _build_candidates_block([scored])
    assert "[s1]" in block
    assert "测试Skill" in block
    assert "质量异常" in block
    assert "缺陷率上升" in block
    assert "闲聊" in block
    assert "打招呼" in block
    assert "正例" in block
    assert "负例" in block
