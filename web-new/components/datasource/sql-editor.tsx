'use client';

import { useState, useRef, useCallback } from 'react';
import { Play, Loader2, Clock, AlertCircle, Table2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { dataSourceService, SQLResult } from '@/services/datasource.service';
import { useToast } from '@/components/ui/use-toast';
import { cn } from '@/lib/utils';

interface SQLEditorProps {
  datasourceId: string;
}

export function SQLEditor({ datasourceId }: SQLEditorProps) {
  const { toast } = useToast();
  const [sql, setSql] = useState('');
  const [executing, setExecuting] = useState(false);
  const [result, setResult] = useState<SQLResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleExecute = useCallback(async () => {
    const trimmedSql = sql.trim();
    if (!trimmedSql) {
      toast({ title: '请输入 SQL 语句', variant: 'destructive' });
      return;
    }

    setExecuting(true);
    setResult(null);
    setError(null);

    try {
      const res = await dataSourceService.executeSQL(datasourceId, trimmedSql);
      setResult(res);
    } catch (err: any) {
      setError(err.message || '执行失败');
    } finally {
      setExecuting(false);
    }
  }, [datasourceId, sql, toast]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Cmd/Ctrl + Enter to execute
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      handleExecute();
    }
    // Tab to insert spaces
    if (e.key === 'Tab') {
      e.preventDefault();
      const target = e.target as HTMLTextAreaElement;
      const start = target.selectionStart;
      const end = target.selectionEnd;
      const newValue = sql.substring(0, start) + '  ' + sql.substring(end);
      setSql(newValue);
      requestAnimationFrame(() => {
        target.selectionStart = target.selectionEnd = start + 2;
      });
    }
  };

  return (
    <div className="flex flex-col gap-3">
      {/* Editor */}
      <div className="relative border rounded-lg overflow-hidden">
        <div className="bg-gray-50 px-3 py-1.5 border-b flex items-center justify-between">
          <span className="text-xs font-medium text-gray-600">SQL 编辑器</span>
          <span className="text-[10px] text-gray-400">
            ⌘ + Enter 执行
          </span>
        </div>
        <Textarea
          ref={textareaRef}
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入 SQL 语句，例如：&#10;SELECT * FROM users LIMIT 10"
          className="min-h-[160px] font-mono text-sm border-0 rounded-none resize-y focus-visible:ring-0 focus-visible:ring-offset-0"
          spellCheck={false}
        />
      </div>

      {/* Execute button */}
      <div className="flex items-center gap-2">
        <Button
          onClick={handleExecute}
          disabled={executing || !sql.trim()}
          size="sm"
        >
          {executing ? (
            <>
              <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
              执行中...
            </>
          ) : (
            <>
              <Play className="h-4 w-4 mr-1.5" />
              执行
            </>
          )}
        </Button>
        {result && (
          <span className="flex items-center gap-1 text-xs text-gray-500">
            <Clock className="h-3 w-3" />
            {result.execution_time_ms}ms
            <span className="mx-1">·</span>
            {result.row_count} 行
            {result.truncated && <span className="text-amber-500">(已截断)</span>}
          </span>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
          <span className="font-mono whitespace-pre-wrap break-all">{error}</span>
        </div>
      )}

      {/* Results table */}
      {result && result.columns.length > 0 && (
        <div className="border rounded-lg overflow-hidden">
          <div className="bg-gray-50 px-3 py-1.5 border-b flex items-center gap-1.5">
            <Table2 className="h-3.5 w-3.5 text-gray-500" />
            <span className="text-xs font-medium text-gray-600">查询结果</span>
          </div>
          <div className="overflow-auto max-h-[400px]">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  {result.columns.map((col, i) => (
                    <th
                      key={i}
                      className="px-3 py-2 text-left font-medium text-gray-600 border-b whitespace-nowrap"
                    >
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                {result.rows.map((row, rowIdx) => (
                  <tr key={rowIdx} className="hover:bg-gray-50">
                    {result.columns.map((col, colIdx) => (
                      <td
                        key={colIdx}
                        className={cn(
                          'px-3 py-1.5 whitespace-nowrap max-w-[300px] truncate',
                          row[col] === null ? 'text-gray-400 italic' : 'text-gray-900'
                        )}
                        title={row[col] !== null ? String(row[col]) : 'NULL'}
                      >
                        {row[col] === null ? 'NULL' : String(row[col])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {result && result.columns.length === 0 && (
        <div className="p-3 bg-green-50 border border-green-200 rounded-lg text-green-700 text-sm">
          SQL 执行成功，无返回数据。
        </div>
      )}
    </div>
  );
}
