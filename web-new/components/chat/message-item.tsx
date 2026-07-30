'use client';

import { User, Bot } from 'lucide-react';
import { Message } from '@/services/conversation.service';
import { cn } from '@/lib/utils';
import { ToolCallCard } from '@/components/chat/tool-call-card';
import { ReferenceCard } from '@/components/chat/reference-card';

interface MessageItemProps {
  message: Message;
}

export function MessageItem({ message }: MessageItemProps) {
  const isUser = message.role === 'user';

  return (
    <div className={cn('px-6 py-4', isUser ? 'bg-white' : 'bg-gray-50')}>
      <div className="max-w-3xl mx-auto flex gap-4">
        {/* Avatar */}
        <div className="flex-shrink-0">
          {isUser ? (
            <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
              <User className="h-5 w-5 text-white" />
            </div>
          ) : (
            <div className="w-8 h-8 rounded-full bg-gray-600 flex items-center justify-center">
              <Bot className="h-5 w-5 text-white" />
            </div>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm mb-1">
            {isUser ? '你' : 'AI 助手'}
          </div>
          <div className="text-sm text-gray-700 whitespace-pre-wrap break-words">
            {message.content || '...'}
          </div>
          
          {/* Tool calls */}
          {message.tool_calls && message.tool_calls.length > 0 && (
            <div className="mt-2 space-y-2">
              {message.tool_calls.map((toolCall, index) => (
                <ToolCallCard key={index} toolCall={toolCall} />
              ))}
            </div>
          )}

          {/* References */}
          {message.references && message.references.length > 0 && (
            <div className="mt-2">
              <ReferenceCard references={message.references} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
