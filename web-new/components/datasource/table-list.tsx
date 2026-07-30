'use client';

import { useState } from 'react';
import { Table2, ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { TableInfo, TableDetail, dataSourceService } from '@/services/datasource.service';
import { cn } from '@/lib/utils';

interface TableListProps {
  datasourceId: string;
  tables: TableInfo[];
  loading: boolean;
}

export function TableList({ datasourceId, tables, loading }: TableListProps) {
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set());
  const [tableDetails, setTableDetails] = useState<Record<string, TableDetail>>({});
  const [loadingDetails, setLoadingDetails] = useState<Set<string>>(new Set());

  const toggleTable = async (tableName: string) => {
    const newExpanded = new Set(expandedTables);
    
    if (newExpanded.has(tableName)) {
      newExpanded.delete(tableName);
      setExpandedTables(newExpanded);
    } else {
      newExpanded.add(tableName);
      setExpandedTables(newExpanded);
      
      // Load table details if not already loaded
      if (!tableDetails[tableName]) {
        setLoadingDetails(prev => new Set(prev).add(tableName));
        try {
          const detail = await dataSourceService.describeTable(datasourceId, tableName);
          setTableDetails(prev => ({ ...prev, [tableName]: detail }));
        } catch (error) {
          console.error('Failed to load table details:', error);
        } finally {
          setLoadingDetails(prev => {
            const newSet = new Set(prev);
            newSet.delete(tableName);
            return newSet;
          });
        }
      }
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-gray-400">
        <Loader2 className="h-5 w-5 animate-spin mr-2" />
        加载表列表中...
      </div>
    );
  }

  if (tables.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400 text-sm">
        暂无表信息
      </div>
    );
  }

  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="bg-gray-50 px-4 py-2 border-b">
        <h3 className="text-sm font-medium text-gray-700">
          表列表 ({tables.length})
        </h3>
      </div>
      <div className="divide-y max-h-[600px] overflow-auto">
        {tables.map((table) => {
          const isExpanded = expandedTables.has(table.name);
          const detail = tableDetails[table.name];
          const isLoading = loadingDetails.has(table.name);

          return (
            <div key={table.name} className="bg-white">
              <div
                className="flex items-center gap-2 px-4 py-3 hover:bg-gray-50 cursor-pointer"
                onClick={() => toggleTable(table.name)}
              >
                {isExpanded ? (
                  <ChevronDown className="h-4 w-4 text-gray-400 flex-shrink-0" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-gray-400 flex-shrink-0" />
                )}
                <Table2 className="h-4 w-4 text-blue-500 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm text-gray-900 truncate">
                    {table.name}
                  </div>
                  {table.comment && (
                    <div className="text-xs text-gray-500 truncate mt-0.5">
                      {table.comment}
                    </div>
                  )}
                </div>
                {table.row_count !== undefined && (
                  <span className="text-xs text-gray-400 flex-shrink-0">
                    {table.row_count.toLocaleString()} 行
                  </span>
                )}
              </div>

              {isExpanded && (
                <div className="bg-gray-50 border-t px-4 py-3 pl-10">
                  {isLoading ? (
                    <div className="flex items-center text-gray-400 text-sm">
                      <Loader2 className="h-4 w-4 animate-spin mr-2" />
                      加载表结构...
                    </div>
                  ) : detail ? (
                    <div className="space-y-2">
                      <div className="text-xs text-gray-600 font-medium mb-2">
                        字段信息 ({detail.columns.length})
                      </div>
                      <div className="space-y-1.5">
                        {detail.columns.map((col) => (
                          <div
                            key={col.name}
                            className="flex items-start gap-2 text-xs bg-white rounded px-2 py-1.5"
                          >
                            <span
                              className={cn(
                                'font-mono font-medium',
                                col.is_primary_key ? 'text-amber-600' : 'text-gray-900'
                              )}
                            >
                              {col.name}
                              {col.is_primary_key && (
                                <span className="ml-1 text-amber-600 text-[10px]">PK</span>
                              )}
                            </span>
                            <span className="text-gray-500 font-mono">{col.data_type}</span>
                            {col.nullable && (
                              <span className="text-gray-400 text-[10px]">NULL</span>
                            )}
                            {col.comment && (
                              <span className="text-gray-500 ml-auto truncate max-w-[200px]">
                                {col.comment}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="text-gray-400 text-sm">无法加载表结构</div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
