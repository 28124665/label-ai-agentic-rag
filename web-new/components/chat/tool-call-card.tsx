'use client';

import { useState } from 'react';
import {
  Database,
  Search,
  Globe,
  ChevronDown,
  ChevronRight,
  Clock,
  CheckCircle2,
  XCircle,
  Loader2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

export interface ToolCall {
  type: 'sql' | 'retrieval' | 'web' | string;
  name: string;
  input: any;
  output?: any;
  duration?: number;
  status?: 'running' | 'success' | 'error';
}

export interface ToolCallCardProps {
  toolCall: ToolCall;
  className?: string;
}

const toolIcons: Record<string, React.ElementType> = {
  sql: Database,
  retrieval: Search,
  web: Globe,
};

const toolLabels: Record<string, string> = {
  sql: 'SQL 查询',
  retrieval: '知识检索',
  web: 'Web 搜索',
};

const statusConfig = {
  running: { icon: Loader2, label: '执行中', className: 'text-blue-600' },
  success: { icon: CheckCircle2, label: '成功', className: 'text-green-600' },
  error: { icon: XCircle, label: '失败', className: 'text-red-600' },
};

export function ToolCallCard({ toolCall, className }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);

  const Icon = toolIcons[toolCall.type] || Database;
  const label = toolLabels[toolCall.type] || toolCall.name || '工具调用';
  const status = toolCall.status || 'success';
  const StatusIcon = statusConfig[status].icon;

  const formatDuration = (ms?: number) => {
    if (!ms) return null;
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  return (
    <div
      className={cn(
        'rounded-lg border bg-card text-card-foreground shadow-sm overflow-hidden',
        className
      )}
    >
      {/* Header */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-accent/50 transition-colors text-left"
      >
        <div className="flex-shrink-0">
          <Icon className="h-4 w-4 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{label}</span>
            {toolCall.name && toolCall.name !== label && (
              <span className="text-xs text-muted-foreground truncate">
                {toolCall.name}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {toolCall.duration != null && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Clock className="h-3 w-3" />
              {formatDuration(toolCall.duration)}
            </span>
          )}
          <StatusIcon className={cn('h-4 w-4', statusConfig[status].className)} />
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
        </div>
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t px-4 py-3 space-y-3 bg-muted/30">
          {toolCall.input != null && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">输入</div>
              <pre className="text-xs bg-background rounded-md p-3 overflow-x-auto border">
                {typeof toolCall.input === 'string'
                  ? toolCall.input
                  : JSON.stringify(toolCall.input, null, 2)}
              </pre>
            </div>
          )}
          {toolCall.output != null && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">输出</div>
              <pre className="text-xs bg-background rounded-md p-3 overflow-x-auto border max-h-60 overflow-y-auto">
                {typeof toolCall.output === 'string'
                  ? toolCall.output
                  : JSON.stringify(toolCall.output, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
