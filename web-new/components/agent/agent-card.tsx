'use client';

import { useRouter } from 'next/navigation';
import { Bot, Wrench, Route, AlertCircle } from 'lucide-react';
import { Agent } from '@/services/agent.service';
import { cn } from '@/lib/utils';

interface AgentCardProps {
  agent: Agent;
}

export function AgentCard({ agent }: AgentCardProps) {
  const router = useRouter();

  const enabledTools = agent.tools_config?.tools || [];
  const toolCount = enabledTools.length;

  return (
    <div
      onClick={() => router.push(`/agent/${agent.id}`)}
      className="bg-white rounded-lg border p-5 hover:shadow-md cursor-pointer transition-all hover:border-primary/50"
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              'w-10 h-10 rounded-lg flex items-center justify-center',
              agent.is_active ? 'bg-primary/10' : 'bg-gray-100'
            )}
          >
            <Bot
              className={cn(
                'h-5 w-5',
                agent.is_active ? 'text-primary' : 'text-gray-400'
              )}
            />
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-gray-900 truncate">
              {agent.name}
            </div>
            <div
              className={cn(
                'text-xs mt-0.5',
                agent.is_active ? 'text-green-600' : 'text-gray-400'
              )}
            >
              {agent.is_active ? '已启用' : '未启用'}
            </div>
          </div>
        </div>
      </div>

      {agent.description && (
        <p className="text-sm text-gray-500 mb-4 line-clamp-2">
          {agent.description}
        </p>
      )}

      <div className="flex items-center gap-4 text-xs text-gray-500">
        <div className="flex items-center gap-1">
          <Wrench className="h-3.5 w-3.5" />
          <span>{toolCount} 个工具</span>
        </div>
        {agent.routing_config?.strategy && (
          <div className="flex items-center gap-1">
            <Route className="h-3.5 w-3.5" />
            <span>{agent.routing_config.strategy}</span>
          </div>
        )}
        {agent.degradation_config?.max_retries !== undefined && (
          <div className="flex items-center gap-1">
            <AlertCircle className="h-3.5 w-3.5" />
            <span>重试 {agent.degradation_config.max_retries} 次</span>
          </div>
        )}
      </div>

      {enabledTools.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {enabledTools.slice(0, 3).map((tool) => (
            <span
              key={tool}
              className="px-2 py-0.5 text-xs bg-blue-50 text-blue-600 rounded"
            >
              {tool}
            </span>
          ))}
          {enabledTools.length > 3 && (
            <span className="px-2 py-0.5 text-xs bg-gray-50 text-gray-500 rounded">
              +{enabledTools.length - 3}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
