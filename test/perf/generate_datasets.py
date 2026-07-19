#!/usr/bin/env python3
# Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Generate synthetic datasets for RAG performance baseline testing."""

import argparse
import json
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATASETS_DIR = ROOT / "datasets"

FAQ_TOPICS = [
    ("账户密码", "如何重置账户密码？", "在登录页点击“忘记密码”，输入注册邮箱后查收重置链接完成操作。"),
    ("订单查询", "在哪里查看我的订单？", "登录后进入“我的订单”页面，可按时间/状态筛选全部订单。"),
    ("退款政策", "退款需要多久到账？", "退款通常在3-7个工作日内原路返回，节假日可能顺延。"),
    ("发票申请", "如何申请电子发票？", "在订单详情页点击“申请发票”，填写抬头和税号后发送至邮箱。"),
    ("配送时效", "快递一般几天送达？", "国内订单默认2-4个工作日送达，偏远地区可能增加1-2天。"),
    ("会员权益", "会员有哪些专属权益？", "会员可享受积分加倍、专属折扣、生日礼券及优先客服通道。"),
    ("积分规则", "积分如何获取和使用？", "每消费1元积1分，100积分可抵扣1元，积分有效期为12个月。"),
    ("售后服务", "商品质量问题如何处理？", "请上传商品照片并联系客服，我们将在24小时内给出解决方案。"),
    ("优惠券", "优惠券可以叠加使用吗？", "单笔订单仅限使用一张优惠券，不可与部分特价商品叠加。"),
    ("修改地址", "下单后还能修改地址吗？", "订单未发货前可在订单详情页修改一次收货地址。"),
]

TECH_TOPICS = [
    "Elasticsearch",
    "Redis",
    "RAGFlow",
    "LangChain",
    "Docker",
    "Kubernetes",
    "Python",
    "React",
    "MySQL",
    "Prometheus",
    "Grafana",
    "Kafka",
    "RabbitMQ",
    "MinIO",
    "OpenAI",
]

SIMPLE_QUERY_TEMPLATES = [
    "{topic}是什么？",
    "如何重置{topic}？",
    "{topic}的默认端口是多少？",
    "怎么查看{topic}状态？",
    "{topic}支持哪些格式？",
]

MEDIUM_QUERY_TEMPLATES = [
    "{topic1}和{topic2}有什么区别？",
    "如何在{topic1}中配置{topic2}？",
    "{topic1}的常用参数有哪些，如何调优？",
    "{topic1}出现{topic2}错误如何解决？",
    "{topic1}和{topic2}如何集成使用？",
]

COMPLEX_QUERY_TEMPLATES = [
    "在{topic1}和{topic2}的混合部署中，如何优化{topic3}的延迟和吞吐量？",
    "请对比{topic1}、{topic2}、{topic3}在一致性、可用性和性能上的差异。",
    "设计一个基于{topic1}和{topic2}的方案，要求支持{topic3}的高并发访问。",
    "{topic1}在{topic2}场景下出现{topic3}问题，如何定位根因并优化？",
    "如何在{topic1}集群中实现{topic2}的自动扩缩容，并保证{topic3}的稳定性？",
]


def _build_faq_doc(index: int) -> dict:
    topic_idx = index % len(FAQ_TOPICS)
    topic, question, answer = FAQ_TOPICS[topic_idx]
    return {
        "id": f"faq_{index:08d}",
        "title": f"{topic} - 问题 {(index // len(FAQ_TOPICS)) + 1}",
        "content": f"问题：{question}\n回答：{answer}",
        "doc_type": "faq",
        "category": topic,
    }


def _build_tech_doc(index: int) -> dict:
    topic_idx = index % len(TECH_TOPICS)
    topic = TECH_TOPICS[topic_idx]
    section = (index // len(TECH_TOPICS)) + 1
    paragraphs = [
        f"{topic} 技术文档第 {section} 节。",
        f"{topic} 是一款广泛应用于生产环境的开源组件，常用于构建 {random.choice(['检索', '缓存', '编排', '监控', '消息', '存储'])} 子系统。",
        f"在部署 {topic} 时，需要关注资源配置、网络延迟、持久化策略和安全认证等关键因素。",
        f"第 {section} 节主要介绍 {topic} 的核心概念、配置示例和常见问题排查方法。",
    ]
    return {
        "id": f"tech_{index:08d}",
        "title": f"{topic} 文档 - 第 {section} 章",
        "content": "\n".join(paragraphs),
        "doc_type": "technical",
        "category": topic,
    }


def generate_kb(name: str, count: int, builder) -> dict:
    random.seed(f"perf-baseline-{name}")
    return {
        "name": name,
        "description": f"Synthetic {name} dataset with {count} documents for performance baseline testing.",
        "count": count,
        "documents": [builder(i) for i in range(count)],
    }


def _build_queries() -> list:
    random.seed("perf-baseline-queries")
    queries = []
    index = 0

    def add(templates, complexity):
        nonlocal index
        for _ in range(40 if complexity in ("simple", "medium") else 20):
            template = random.choice(templates)
            if complexity == "simple":
                query = template.format(topic=random.choice(TECH_TOPICS + [t[0] for t in FAQ_TOPICS]))
            elif complexity == "medium":
                query = template.format(topic1=random.choice(TECH_TOPICS), topic2=random.choice(TECH_TOPICS))
            else:
                query = template.format(
                    topic1=random.choice(TECH_TOPICS),
                    topic2=random.choice(TECH_TOPICS),
                    topic3=random.choice(TECH_TOPICS),
                )
            queries.append({
                "id": f"q_{index:03d}",
                "query": query,
                "complexity": complexity,
            })
            index += 1

    add(SIMPLE_QUERY_TEMPLATES, "simple")
    add(MEDIUM_QUERY_TEMPLATES, "medium")
    add(COMPLEX_QUERY_TEMPLATES, "complex")
    random.shuffle(queries)
    return queries


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic RAG performance datasets.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DATASETS_DIR,
        help="Directory to write generated JSON files.",
    )
    parser.add_argument(
        "--small-count",
        type=int,
        default=1000,
        help="Number of FAQ documents in small_kb.json.",
    )
    parser.add_argument(
        "--medium-count",
        type=int,
        default=10000,
        help="Number of technical documents in medium_kb.json.",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    small_kb = generate_kb("small_kb", args.small_count, _build_faq_doc)
    medium_kb = generate_kb("medium_kb", args.medium_count, _build_tech_doc)
    queries = {"count": 100, "queries": _build_queries()}

    (args.output_dir / "small_kb.json").write_text(
        json.dumps(small_kb, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "medium_kb.json").write_text(
        json.dumps(medium_kb, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "queries.json").write_text(
        json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Generated datasets in {args.output_dir}:")
    print(f"  small_kb.json  : {small_kb['count']} FAQ docs")
    print(f"  medium_kb.json : {medium_kb['count']} tech docs")
    print(f"  queries.json   : {queries['count']} queries")


if __name__ == "__main__":
    main()
