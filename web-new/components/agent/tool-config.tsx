'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, Database, Globe, BookOpen } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

interface ToolConfigProps {
  config: any;
  onChange: (config: any) => void;
}

const AVAILABLE_TOOLS = [
  {
    key: 'rag',
    label: 'RAG Tool',
    description: '基于知识库的检索增强生成工具',
    icon: BookOpen,
    configKey: 'rag_config',
    fields: [
      { key: 'top_k', label: 'Top K', type: 'number', default: 5, placeholder: '返回文档数量' },
      { key: 'similarity_threshold', label: '相似度阈值', type: 'number', default: 0.7, placeholder: '0-1' },
      { key: 'knowledge_base_ids', label: '知识库 ID（逗号分隔）', type: 'text', default: '', placeholder: 'kb_id1,kb_id2' },
    ],
  },
  {
    key: 'database',
    label: 'Database Tool',
    description: '数据库查询工具，支持 SQL 生成和执行',
    icon: Database,
    configKey: 'database_config',
    fields: [
      { key: 'datasource_id', label: '数据源 ID', type: 'text', default: '', placeholder: '数据源标识' },
      { key: 'max_rows', label: '最大返回行数', type: 'number', default: 100, placeholder: '100' },
      { key: 'allow_write', label: '允许写操作', type: 'checkbox', default: false },
    ],
  },
  {
    key: 'web',
    label: 'Web Tool',
    description: '网页搜索和爬取工具',
    icon: Globe,
    configKey: 'web_config',
    fields: [
      { key: 'search_engine', label: '搜索引擎', type: 'select', default: 'duckduckgo', options: ['duckduckgo', 'google', 'bing'] },
      { key: 'max_results', label: '最大结果数', type: 'number', default: 5, placeholder: '5' },
      { key: 'enable_crawl', label: '启用页面爬取', type: 'checkbox', default: false },
    ],
  },
];

export function ToolConfig({ config, onChange }: ToolConfigProps) {
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({});
  const enabledTools: string[] = config?.tools || [];

  const toggleTool = (toolKey: string) => {
    const newTools = enabledTools.includes(toolKey)
      ? enabledTools.filter((t) => t !== toolKey)
      : [...enabledTools, toolKey];
    onChange({ ...config, tools: newTools });
  };

  const toggleExpand = (toolKey: string) => {
    setExpandedTools((prev) => ({ ...prev, [toolKey]: !prev[toolKey] }));
  };

  const updateToolField = (toolKey: string, configKey: string, fieldKey: string, value: any) => {
    const toolConfig = config?.[configKey] || {};
    onChange({
      ...config,
      [configKey]: { ...toolConfig, [fieldKey]: value },
    });
  };

  return (
    <div className="space-y-3">
      {AVAILABLE_TOOLS.map((tool) => {
        const isEnabled = enabledTools.includes(tool.key);
        const isExpanded = expandedTools[tool.key];
        const Icon = tool.icon;

        return (
          <div
            key={tool.key}
            className={cn(
              'border rounded-lg transition-colors',
              isEnabled ? 'border-primary/30 bg-primary/5' : 'border-gray-200'
            )}
          >
            <div className="flex items-center p-4">
              <input
                type="checkbox"
                checked={isEnabled}
                onChange={() => toggleTool(tool.key)}
                className="h-4 w-4 rounded border-gray-300"
              />
              <Icon className={cn('h-5 w-5 ml-3', isEnabled ? 'text-primary' : 'text-gray-400')} />
              <div className="ml-3 flex-1">
                <div className="font-medium text-sm">{tool.label}</div>
                <div className="text-xs text-gray-500">{tool.description}</div>
              </div>
              {isEnabled && (
                <button
                  onClick={() => toggleExpand(tool.key)}
                  className="text-gray-400 hover:text-gray-600"
                >
                  {isExpanded ? (
                    <ChevronDown className="h-4 w-4" />
                  ) : (
                    <ChevronRight className="h-4 w-4" />
                  )}
                </button>
              )}
            </div>

            {isEnabled && isExpanded && (
              <div className="px-4 pb-4 pt-1 border-t border-gray-100 space-y-3">
                {tool.fields.map((field) => {
                  const toolConfig = config?.[tool.configKey] || {};
                  const value = toolConfig[field.key] ?? field.default;

                  if (field.type === 'checkbox') {
                    return (
                      <div key={field.key} className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={!!value}
                          onChange={(e) =>
                            updateToolField(tool.key, tool.configKey, field.key, e.target.checked)
                          }
                          className="h-4 w-4 rounded border-gray-300"
                        />
                        <Label className="text-sm">{field.label}</Label>
                      </div>
                    );
                  }

                  if (field.type === 'select') {
                    return (
                      <div key={field.key}>
                        <Label className="text-sm">{field.label}</Label>
                        <select
                          value={value}
                          onChange={(e) =>
                            updateToolField(tool.key, tool.configKey, field.key, e.target.value)
                          }
                          className="mt-1 w-full h-9 rounded-md border border-input bg-background px-3 text-sm"
                        >
                          {field.options?.map((opt) => (
                            <option key={opt} value={opt}>
                              {opt}
                            </option>
                          ))}
                        </select>
                      </div>
                    );
                  }

                  return (
                    <div key={field.key}>
                      <Label className="text-sm">{field.label}</Label>
                      <Input
                        type={field.type}
                        value={value}
                        onChange={(e) =>
                          updateToolField(
                            tool.key,
                            tool.configKey,
                            field.key,
                            field.type === 'number' ? parseFloat(e.target.value) || 0 : e.target.value
                          )
                        }
                        placeholder={field.placeholder}
                        className="mt-1"
                      />
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
