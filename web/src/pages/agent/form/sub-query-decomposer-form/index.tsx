import { NextLLMSelect } from '@/components/llm-select/next';
import { MessageHistoryWindowSizeFormField } from '@/components/message-history-window-size-item';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { useTranslation } from 'react-i18next';
import { INextOperatorForm } from '../../interface';

const SubQueryDecomposerForm = ({ form }: INextOperatorForm) => {
  const { t } = useTranslation();

  return (
    <Form {...form}>
      <form
        className="space-y-6"
        onSubmit={(e) => {
          e.preventDefault();
        }}
      >
        <FormField
          control={form.control}
          name="llm_id"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('chat.modelTip')}>
                {t('chat.model')}
              </FormLabel>
              <FormControl>
                <NextLLMSelect {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="min_count"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.subQueryDecomposer.minCountTip')}>
                {t('flow.subQueryDecomposer.minCount')}
              </FormLabel>
              <FormControl>
                <Input type="number" min="1" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="max_count"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.subQueryDecomposer.maxCountTip')}>
                {t('flow.subQueryDecomposer.maxCount')}
              </FormLabel>
              <FormControl>
                <Input type="number" min="1" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="top_k"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.subQueryDecomposer.topKTip')}>
                {t('flow.subQueryDecomposer.topK')}
              </FormLabel>
              <FormControl>
                <Input type="number" min="1" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="dedup_threshold"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.subQueryDecomposer.dedupThresholdTip')}>
                {t('flow.subQueryDecomposer.dedupThreshold')}
              </FormLabel>
              <FormControl>
                <Input type="number" step="0.01" min="0" max="1" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="rrf_k"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.subQueryDecomposer.rrfKTip')}>
                {t('flow.subQueryDecomposer.rrfK')}
              </FormLabel>
              <FormControl>
                <Input type="number" min="1" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <MessageHistoryWindowSizeFormField />
      </form>
    </Form>
  );
};

export default SubQueryDecomposerForm;
