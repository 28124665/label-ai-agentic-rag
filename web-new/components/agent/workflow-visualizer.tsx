'use client';

import { cn } from '@/lib/utils';

interface WorkflowVisualizerProps {
  toolsConfig: any;
  routingConfig: any;
}

interface WorkflowNode {
  id: string;
  label: string;
  type: 'start' | 'router' | 'tool' | 'end';
  x: number;
  y: number;
}

interface WorkflowEdge {
  from: string;
  to: string;
  label?: string;
}

export function WorkflowVisualizer({ toolsConfig, routingConfig }: WorkflowVisualizerProps) {
  const enabledTools: string[] = toolsConfig?.tools || [];
  const strategy = routingConfig?.strategy || 'keyword';

  // Build workflow nodes
  const nodes: WorkflowNode[] = [
    { id: 'start', label: '开始', type: 'start', x: 50, y: 50 },
    { id: 'router', label: '路由节点', type: 'router', x: 50, y: 150 },
  ];

  // Add tool nodes
  const toolLabels: Record<string, string> = {
    rag: 'RAG Tool',
    database: 'Database Tool',
    web: 'Web Tool',
  };

  enabledTools.forEach((tool, index) => {
    nodes.push({
      id: `tool-${tool}`,
      label: toolLabels[tool] || tool,
      type: 'tool',
      x: 50 + (index - (enabledTools.length - 1) / 2) * 150,
      y: 250,
    });
  });

  // Add end node
  nodes.push({ id: 'end', label: '结束', type: 'end', x: 50, y: 350 });

  // Build edges
  const edges: WorkflowEdge[] = [
    { from: 'start', to: 'router' },
  ];

  if (enabledTools.length === 0) {
    edges.push({ from: 'router', to: 'end', label: '无工具' });
  } else {
    enabledTools.forEach((tool) => {
      edges.push({ from: 'router', to: `tool-${tool}`, label: strategy === 'keyword' ? '关键词匹配' : undefined });
      edges.push({ from: `tool-${tool}`, to: 'end' });
    });
  }

  // Calculate SVG dimensions — dynamically expand width to fit all tool nodes
  const svgWidth = Math.max(600, enabledTools.length * 150 + 200);
  const svgHeight = 400;

  // Node dimensions
  const nodeWidth = 120;
  const nodeHeight = 40;

  // Get node position
  const getNodePosition = (nodeId: string) => {
    const node = nodes.find((n) => n.id === nodeId);
    if (!node) return { x: 0, y: 0 };
    return {
      x: svgWidth / 2 + node.x,
      y: node.y,
    };
  };

  // Get node color
  const getNodeColor = (type: string) => {
    switch (type) {
      case 'start':
        return 'fill-green-500';
      case 'end':
        return 'fill-red-500';
      case 'router':
        return 'fill-blue-500';
      case 'tool':
        return 'fill-purple-500';
      default:
        return 'fill-gray-500';
    }
  };

  return (
    <div className="border rounded-lg bg-gray-50 p-4 overflow-auto">
      {enabledTools.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          <div className="text-4xl mb-2">🔧</div>
          <div className="text-sm">请先启用至少一个工具以查看工作流</div>
        </div>
      ) : (
        <svg width={svgWidth} height={svgHeight} className="mx-auto">
          {/* Edges */}
          {edges.map((edge, index) => {
            const fromPos = getNodePosition(edge.from);
            const toPos = getNodePosition(edge.to);
            const midX = (fromPos.x + toPos.x) / 2;
            const midY = (fromPos.y + toPos.y) / 2;

            return (
              <g key={index}>
                <line
                  x1={fromPos.x}
                  y1={fromPos.y + nodeHeight / 2}
                  x2={toPos.x}
                  y2={toPos.y - nodeHeight / 2}
                  stroke="#94a3b8"
                  strokeWidth="2"
                  markerEnd="url(#arrowhead)"
                />
                {edge.label && (
                  <text
                    x={midX}
                    y={midY}
                    textAnchor="middle"
                    className="text-xs fill-gray-600"
                    dy="-5"
                  >
                    {edge.label}
                  </text>
                )}
              </g>
            );
          })}

          {/* Arrow marker definition */}
          <defs>
            <marker
              id="arrowhead"
              markerWidth="10"
              markerHeight="10"
              refX="9"
              refY="3"
              orient="auto"
            >
              <path d="M0,0 L0,6 L9,3 z" fill="#94a3b8" />
            </marker>
          </defs>

          {/* Nodes */}
          {nodes.map((node) => {
            const pos = getNodePosition(node.id);
            const colorClass = getNodeColor(node.type);

            return (
              <g key={node.id}>
                <rect
                  x={pos.x - nodeWidth / 2}
                  y={pos.y - nodeHeight / 2}
                  width={nodeWidth}
                  height={nodeHeight}
                  rx="8"
                  className={cn(colorClass, 'stroke-white stroke-2')}
                />
                <text
                  x={pos.x}
                  y={pos.y}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  className="text-sm fill-white font-medium pointer-events-none"
                >
                  {node.label}
                </text>
              </g>
            );
          })}
        </svg>
      )}

      {/* Legend */}
      <div className="flex items-center justify-center gap-4 mt-4 text-xs">
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded bg-green-500" />
          <span className="text-gray-600">开始</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded bg-blue-500" />
          <span className="text-gray-600">路由</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded bg-purple-500" />
          <span className="text-gray-600">工具</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded bg-red-500" />
          <span className="text-gray-600">结束</span>
        </div>
      </div>

      {/* Workflow info */}
      <div className="mt-4 p-3 bg-white rounded border text-xs text-gray-600">
        <div className="font-medium mb-1">工作流说明</div>
        <ul className="space-y-1 list-disc list-inside">
          <li>用户输入从"开始"节点进入</li>
          <li>经过"路由节点"根据策略（{strategy}）选择工具</li>
          <li>调用选中的工具执行任务</li>
          <li>最终结果从"结束"节点返回</li>
        </ul>
      </div>
    </div>
  );
}
