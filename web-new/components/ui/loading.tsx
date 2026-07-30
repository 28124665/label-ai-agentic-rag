'use client';

import { cn } from '@/lib/utils';
import { cva, type VariantProps } from 'class-variance-authority';

const loadingVariants = cva('animate-spin rounded-full border-current border-t-transparent', {
  variants: {
    size: {
      sm: 'h-4 w-4 border-2',
      md: 'h-8 w-8 border-2',
      lg: 'h-12 w-12 border-3',
    },
  },
  defaultVariants: {
    size: 'md',
  },
});

export interface LoadingProps extends VariantProps<typeof loadingVariants> {
  text?: string;
  className?: string;
}

export function Loading({ text, size = 'md', className }: LoadingProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-3', className)}>
      <div className={cn('text-primary', loadingVariants({ size }))} role="status" aria-label="加载中" />
      {text && <span className="text-sm text-muted-foreground">{text}</span>}
    </div>
  );
}
