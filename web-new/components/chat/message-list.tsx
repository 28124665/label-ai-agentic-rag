'use client';

import { Message } from '@/services/conversation.service';
import { MessageItem } from './message-item';

interface MessageListProps {
  messages: Message[];
}

export function MessageList({ messages }: MessageListProps) {
  if (messages.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center text-gray-400">
          <div className="text-6xl mb-4">👋</div>
          <div className="text-lg font-medium mb-2">开始对话</div>
          <div className="text-sm">输入消息开始聊天</div>
        </div>
      </div>
    );
  }

  return (
    <div className="py-6">
      {messages.map((message) => (
        <MessageItem key={message.id} message={message} />
      ))}
    </div>
  );
}
