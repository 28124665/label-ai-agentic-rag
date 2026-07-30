'use client';

import { FileText, Database, Globe, ExternalLink } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface Reference {
  type: 'document' | 'database' | 'web' | string;
  title: string;
  content: string;
  source: string;
  score?: number;
}

export interface ReferenceCardProps {
  references: Reference[];
  className?: string;
}

const referenceIcons: Record<string, React.ElementType> = {
  document: FileText,
  database: Database,
  web: Globe,
};

const referenceLabels: Record<string, string> = {
  document: '文档',
  database: '数据库',
  web: '网页',
};

const typeColors: Record<string, string> = {
  document: 'bg-blue-100 text-blue-700',
  database: 'bg-purple-100 text-purple-700',
  web: 'bg-green-100 text-green-700',
};

export function ReferenceCard({ references, className }: ReferenceCardProps) {
  if (!references || references.length === 0) return null;

  return (
    <div className={cn('space-y-2', className)}>
      <div className="text-xs font-medium text-muted-foreground">
        引用来源 ({references.length})
      </div>
      <div className="grid gap-2">
        {references.map((ref, index) => {
          const Icon = referenceIcons[ref.type] || FileText;
          const label = referenceLabels[ref.type] || ref.type;
          const colorClass = typeColors[ref.type] || 'bg-gray-100 text-gray-700';

          return (
            <div
              key={index}
              className="rounded-lg border bg-card text-card-foreground shadow-sm p-3 hover:bg-accent/50 transition-colors"
            >
              <div className="flex items-start gap-3">
                <div className="flex-shrink-0 mt-0.5">
                  <div className={cn('w-7 h-7 rounded-md flex items-center justify-center', colorClass)}>
                    <Icon className="h-3.5 w-3.5" />
                  </div>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={cn('text-xs px-1.5 py-0.5 rounded font-medium', colorClass)}>
                      {label}
                    </span>
                    <span className="text-sm font-medium truncate">{ref.title}</span>
                    {ref.score != null && (
                      <span className="text-xs text-muted-foreground flex-shrink-0">
                        {(ref.score * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground line-clamp-2 mb-1">
                    {ref.content}
                  </p>
                  <div className="flex items-center gap-1 text-xs text-muted-foreground">
                    <span className="truncate">{ref.source}</span>
                    {ref.source.startsWith('http') && (
                      <ExternalLink className="h-3 w-3 flex-shrink-0" />
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
