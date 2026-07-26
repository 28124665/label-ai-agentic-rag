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
import { RAGFlowSelect } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { useTranslation } from 'react-i18next';
import { INextOperatorForm } from '../../interface';

const StrategyOptions = [
  { value: 'synonym_rewrite', label: 'Synonym Rewrite' },
  { value: 'sub_query_decompose', label: 'Sub Query Decompose' },
  { value: 'hyde', label: 'HyDE' },
];

const QueryRewriterForm = ({ form }: INextOperatorForm) => {
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
          name="strategy"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.queryRewriter.strategyTip')}>
                {t('flow.queryRewriter.strategy')}
              </FormLabel>
              <FormControl>
                <RAGFlowSelect options={StrategyOptions} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="use_llm_for_synonyms"
          render={({ field }) => (
            <FormItem className="flex items-center justify-between">
              <FormLabel tooltip={t('flow.queryRewriter.useLlmForSynonymsTip')}>
                {t('flow.queryRewriter.useLlmForSynonyms')}
              </FormLabel>
              <FormControl>
                <Switch
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
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

export default QueryRewriterForm;
