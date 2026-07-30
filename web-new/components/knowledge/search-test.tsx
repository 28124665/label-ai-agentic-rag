'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Search, Loader2, FileText } from 'lucide-react';
import { knowledgeService, type RetrievalResult } from '@/services/knowledge.service';

interface SearchTestProps {
  datasetId: string;
}

export function SearchTest({ datasetId }: SearchTestProps) {
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(5);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<RetrievalResult[]>([]);
  const [executionTime, setExecutionTime] = useState<number | null>(null);

  const handleSearch = async () => {
    if (!query.trim()) return;

    setLoading(true);
    try {
      const response = await knowledgeService.retrieval(datasetId, query, topK);
      setResults(response.results);
      setExecutionTime(response.execution_time_ms);
    } catch (error) {
      console.error('检索失败:', error);
      setResults([]);
      setExecutionTime(null);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !loading) {
      handleSearch();
    }
  };

  return (
    <div className="space-y-4">
      {/* 搜索输入 */}
      <div className="space-y-2">
        <Label>查询文本</Label>
        <div className="flex gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入要检索的内容..."
            disabled={loading}
            className="flex-1"
          />
          <Input
            type="number"
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
            min={1}
            max={20}
            className="w-20"
            disabled={loading}
          />
          <Button onClick={handleSearch} disabled={loading || !query.trim()}>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" />
            )}
          </Button>
        </div>
        <div className="text-xs text-gray-500">
          Top K: 返回最相似的前 {topK} 个结果
        </div>
      </div>

      {/* 检索结果 */}
      {loading && (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
          <span className="ml-2 text-gray-500">检索中...</span>
        </div>
      )}

      {!loading && executionTime !== null && (
        <div className="text-sm text-gray-600">
          检索完成，耗时 {executionTime} ms，找到 {results.length} 个结果
        </div>
      )}

      {!loading && results.length > 0 && (
        <div className="space-y-3">
          {results.map((result, index) => (
            <div
              key={result.chunk_id}
              className="border rounded-lg p-4 hover:bg-gray-50 transition-colors"
            >
              <div className="flex items-start justify-between mb-2">
                <div className="flex items-center gap-2 text-sm">
                  <div className="flex items-center gap-1 text-blue-600 font-medium">
                    <span className="bg-blue-100 text-blue-700 px-2 py-0.5 rounded text-xs">
                      #{index + 1}
                    </span>
                  </div>
                  <FileText className="h-4 w-4 text-gray-400" />
                  <span className="text-gray-700">{result.document_name}</span>
                </div>
                <div className="flex items-center gap-1 text-sm">
                  <span className="text-gray-500">相似度:</span>
                  <span className="font-medium text-green-600">
                    {(result.similarity * 100).toFixed(1)}%
                  </span>
                </div>
              </div>
              <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
                {result.content}
              </div>
              {result.metadata && Object.keys(result.metadata).length > 0 && (
                <div className="mt-2 pt-2 border-t">
                  <div className="text-xs text-gray-500">
                    元数据: {JSON.stringify(result.metadata, null, 2)}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {!loading && executionTime !== null && results.length === 0 && (
        <div className="flex flex-col items-center justify-center py-8 text-gray-400">
          <Search className="h-12 w-12 mb-3" />
          <div className="text-sm">未找到相关结果</div>
          <div className="text-xs mt-1">尝试使用不同的查询词</div>
        </div>
      )}
    </div>
  );
}
