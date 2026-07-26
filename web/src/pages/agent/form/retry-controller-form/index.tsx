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

const RetryControllerForm = ({ form }: INextOperatorForm) => {
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
          name="max_retries"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.retryController.maxRetriesTip')}>
                {t('flow.retryController.maxRetries')}
              </FormLabel>
              <FormControl>
                <Input type="number" min="0" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="max_retry_tokens"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.retryController.maxRetryTokensTip')}>
                {t('flow.retryController.maxRetryTokens')}
              </FormLabel>
              <FormControl>
                <Input type="number" min="0" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="min_relevant_docs"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.retryController.minRelevantDocsTip')}>
                {t('flow.retryController.minRelevantDocs')}
              </FormLabel>
              <FormControl>
                <Input type="number" min="0" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="has_relevant"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.retryController.hasRelevantTip')}>
                {t('flow.retryController.hasRelevant')}
              </FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="relevant_count"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.retryController.relevantCountTip')}>
                {t('flow.retryController.relevantCount')}
              </FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="retry_count"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.retryController.retryCountTip')}>
                {t('flow.retryController.retryCount')}
              </FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="accumulated_retry_tokens"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.retryController.accumulatedRetryTokensTip')}>
                {t('flow.retryController.accumulatedRetryTokens')}
              </FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </form>
    </Form>
  );
};

export default RetryControllerForm;
