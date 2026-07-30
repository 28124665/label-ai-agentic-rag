'use client';

import { useRouter } from 'next/navigation';
import { MessageSquare } from 'lucide-react';
import { Conversation } from '@/services/conversation.service';
import { formatRelativeTime } from '@/lib/utils';
import { cn } from '@/lib/utils';

interface ConversationListProps {
  conversations: Conversation[];
  loading: boolean;
}

export function ConversationList({ conversations, loading }: ConversationListProps) {
  const router = useRouter();

  if (loading) {
    return (
      <div className="p-4 text-center text-gray-400 text-sm">
        加载中...
      </div>
    );
  }

  if (conversations.length === 0) {
    return (
      <div className="p-4 text-center text-gray-400 text-sm">
        暂无对话
      </div>
    );
  }

  return (
    <div className="py-2">
      {conversations.map((conversation) => (
        <div
          key={conversation.id}
          onClick={() => router.push(`/chat/${conversation.id}`)}
          className="px-4 py-3 hover:bg-gray-50 cursor-pointer border-b border-gray-100 transition-colors"
        >
          <div className="flex items-start gap-3">
            <div className="flex-shrink-0 mt-0.5">
              <MessageSquare className="h-5 w-5 text-gray-400" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-medium text-sm text-gray-900 truncate">
                {conversation.title}
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-xs text-gray-500">
                  {conversation.message_count} 条消息
                </span>
                {conversation.last_message_at && (
                  <>
                    <span className="text-gray-300">·</span>
                    <span className="text-xs text-gray-500">
                      {formatRelativeTime(conversation.last_message_at)}
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
