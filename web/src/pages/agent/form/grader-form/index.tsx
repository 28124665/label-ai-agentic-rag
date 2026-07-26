import { NextLLMSelect } from '@/components/llm-select/next';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { RAGFlowSelect } from '@/components/ui/select';
import { useTranslation } from 'react-i18next';
import { INextOperatorForm } from '../../interface';

const EvalModeOptions = [
  { value: 'llm', label: 'LLM-as-Judge' },
  { value: 'local_nli', label: 'NLI (Natural Language Inference)' },
];

const GraderForm = ({ form }: INextOperatorForm) => {
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
          name="eval_mode"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.grader.evalModeTip')}>
                {t('flow.grader.evalMode')}
              </FormLabel>
              <FormControl>
                <RAGFlowSelect options={EvalModeOptions} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="batch_size"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.grader.batchSizeTip')}>
                {t('flow.grader.batchSize')}
              </FormLabel>
              <FormControl>
                <Input type="number" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="relevance_threshold"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.grader.relevanceThresholdTip')}>
                {t('flow.grader.relevanceThreshold')}
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
          name="max_eval_tokens"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.grader.maxEvalTokensTip')}>
                {t('flow.grader.maxEvalTokens')}
              </FormLabel>
              <FormControl>
                <Input type="number" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="timeout"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.grader.timeoutTip')}>
                {t('flow.grader.timeout')}
              </FormLabel>
              <FormControl>
                <Input type="number" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </form>
    </Form>
  );
};

export default GraderForm;
