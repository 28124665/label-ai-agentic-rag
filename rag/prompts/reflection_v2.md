# 工具调用结果反思

## 用户问题
{{ question }}

## 工具调用结果
{% for r in tool_results %}
### 工具：{{ r.tool }}
{{ r.content }}
{% endfor %}

## 反思任务
请基于上述工具调用结果，对信息收集情况进行结构化反思，评估当前信息是否足以回答用户问题。

## 输出要求
请严格输出以下 JSON 格式，不要输出其他任何内容（不要包含 markdown 代码块标记）：

{
  "key_findings": ["关键发现1", "关键发现2"],
  "info_sufficient": true,
  "gaps": ["信息缺口1"],
  "next_action": "proceed_to_generate"
}

### 字段说明
- key_findings：从工具结果中提取的关键发现（字符串列表）
- info_sufficient：当前信息是否充分回答用户问题（布尔值）
- gaps：仍存在的信息缺口（字符串列表，无缺口时为空列表）
- next_action：建议的下一步动作，取以下三者之一：
  - proceed_to_generate：信息充分，进入答案生成
  - need_more_retrieval：信息不足，需要补充知识库检索
  - need_web_search：信息不足，需要联网搜索

## 约束
- 反思内容简洁，控制在 200 字以内
- 仅输出 JSON，不要包含额外解释
