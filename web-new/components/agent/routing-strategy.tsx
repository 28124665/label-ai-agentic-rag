'use client';

import { useState } from 'react';
import { Plus, Trash2, GripVertical } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

interface RoutingStrategyProps {
  config: any;
  onChange: (config: any) => void;
}

interface RoutingRule {
  id: string;
  keywords: string[];
  priority: number;
  target_tool?: string;
}

export function RoutingStrategy({ config, onChange }: RoutingStrategyProps) {
  const strategy = config?.strategy || 'keyword';
  const rules: RoutingRule[] = config?.rules || [];

  const updateStrategy = (newStrategy: string) => {
    onChange({ ...config, strategy: newStrategy });
  };

  const addRule = () => {
    const newRule: RoutingRule = {
      id: `rule-${Date.now()}`,
      keywords: [],
      priority: rules.length + 1,
      target_tool: '',
    };
    onChange({ ...config, rules: [...rules, newRule] });
  };

  const updateRule = (ruleId: string, updates: Partial<RoutingRule>) => {
    const updatedRules = rules.map((rule) =>
      rule.id === ruleId ? { ...rule, ...updates } : rule
    );
    onChange({ ...config, rules: updatedRules });
  };

  const deleteRule = (ruleId: string) => {
    const updatedRules = rules.filter((rule) => rule.id !== ruleId);
    onChange({ ...config, rules: updatedRules });
  };

  const addKeyword = (ruleId: string) => {
    const rule = rules.find((r) => r.id === ruleId);
    if (!rule) return;
    updateRule(ruleId, { keywords: [...rule.keywords, ''] });
  };

  const updateKeyword = (ruleId: string, index: number, value: string) => {
    const rule = rules.find((r) => r.id === ruleId);
    if (!rule) return;
    const newKeywords = [...rule.keywords];
    newKeywords[index] = value;
    updateRule(ruleId, { keywords: newKeywords });
  };

  const deleteKeyword = (ruleId: string, index: number) => {
    const rule = rules.find((r) => r.id === ruleId);
    if (!rule) return;
    const newKeywords = rule.keywords.filter((_, i) => i !== index);
    updateRule(ruleId, { keywords: newKeywords });
  };

  const moveRule = (ruleId: string, direction: 'up' | 'down') => {
    const index = rules.findIndex((r) => r.id === ruleId);
    if (index === -1) return;

    const newIndex = direction === 'up' ? index - 1 : index + 1;
    if (newIndex < 0 || newIndex >= rules.length) return;

    const newRules = [...rules];
    [newRules[index], newRules[newIndex]] = [newRules[newIndex], newRules[index]];
    
    // Update priorities
    const updatedRules = newRules.map((rule, i) => ({ ...rule, priority: i + 1 }));
    onChange({ ...config, rules: updatedRules });
  };

  return (
    <div className="space-y-4">
      <div>
        <Label>路由策略</Label>
        <select
          value={strategy}
          onChange={(e) => updateStrategy(e.target.value)}
          className="mt-1 w-full h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="keyword">关键词匹配</option>
          <option value="llm">LLM 智能路由</option>
          <option value="priority">优先级顺序</option>
        </select>
        <p className="text-xs text-gray-500 mt-1">
          {strategy === 'keyword' && '根据用户输入中的关键词匹配到对应工具'}
          {strategy === 'llm' && '使用 LLM 分析用户意图，智能选择最合适的工具'}
          {strategy === 'priority' && '按照配置的优先级顺序依次尝试工具'}
        </p>
      </div>

      {strategy === 'keyword' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>匹配规则</Label>
            <Button size="sm" variant="outline" onClick={addRule}>
              <Plus className="h-3.5 w-3.5 mr-1" />
              添加规则
            </Button>
          </div>

          {rules.length === 0 ? (
            <div className="text-center py-8 text-gray-400 text-sm border border-dashed rounded-lg">
              暂无规则，点击添加按钮创建匹配规则
            </div>
          ) : (
            <div className="space-y-3">
              {rules.map((rule, index) => (
                <div
                  key={rule.id}
                  className="border rounded-lg p-4 bg-gray-50"
                >
                  <div className="flex items-start gap-3">
                    <div className="flex flex-col gap-1 pt-1">
                      <button
                        onClick={() => moveRule(rule.id, 'up')}
                        disabled={index === 0}
                        className="text-gray-400 hover:text-gray-600 disabled:opacity-30"
                      >
                        <GripVertical className="h-4 w-4" />
                      </button>
                    </div>

                    <div className="flex-1 space-y-3">
                      <div className="flex items-center gap-2">
                        <Label className="text-sm font-medium">
                          规则 {index + 1}
                        </Label>
                        <div className="flex-1" />
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => deleteRule(rule.id)}
                          className="text-red-600 hover:text-red-700 hover:bg-red-50"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>

                      <div>
                        <Label className="text-xs text-gray-600">关键词</Label>
                        <div className="mt-1 space-y-2">
                          {rule.keywords.map((keyword, kIndex) => (
                            <div key={kIndex} className="flex items-center gap-2">
                              <Input
                                value={keyword}
                                onChange={(e) =>
                                  updateKeyword(rule.id, kIndex, e.target.value)
                                }
                                placeholder="输入关键词"
                                className="flex-1"
                              />
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => deleteKeyword(rule.id, kIndex)}
                                className="text-red-600 hover:text-red-700 hover:bg-red-50"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                          ))}
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => addKeyword(rule.id)}
                          >
                            <Plus className="h-3 w-3 mr-1" />
                            添加关键词
                          </Button>
                        </div>
                      </div>

                      <div>
                        <Label className="text-xs text-gray-600">目标工具</Label>
                        <select
                          value={rule.target_tool || ''}
                          onChange={(e) =>
                            updateRule(rule.id, { target_tool: e.target.value })
                          }
                          className="mt-1 w-full h-9 rounded-md border border-input bg-background px-3 text-sm"
                        >
                          <option value="">自动选择</option>
                          <option value="rag">RAG Tool</option>
                          <option value="database">Database Tool</option>
                          <option value="web">Web Tool</option>
                        </select>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {strategy === 'priority' && (
        <div className="space-y-3">
          <Label>工具优先级</Label>
          <p className="text-xs text-gray-500">
            按照以下顺序依次尝试调用工具，直到成功为止
          </p>
          <div className="space-y-2">
            {rules.map((rule, index) => (
              <div
                key={rule.id}
                className="flex items-center gap-3 p-3 border rounded-lg bg-gray-50"
              >
                <div className="flex-shrink-0 w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center font-semibold text-sm">
                  {index + 1}
                </div>
                <div className="flex-1">
                  <div className="font-medium text-sm">
                    {rule.target_tool || '未配置'}
                  </div>
                </div>
                <div className="flex gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => moveRule(rule.id, 'up')}
                    disabled={index === 0}
                  >
                    ↑
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => moveRule(rule.id, 'down')}
                    disabled={index === rules.length - 1}
                  >
                    ↓
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => deleteRule(rule.id)}
                    className="text-red-600 hover:text-red-700 hover:bg-red-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            ))}
            <Button size="sm" variant="outline" onClick={addRule} className="w-full">
              <Plus className="h-3.5 w-3.5 mr-1" />
              添加工具
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
