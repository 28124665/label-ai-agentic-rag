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
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from 'react-i18next';
import { INextOperatorForm } from '../../interface';

const HyDEForm = ({ form }: INextOperatorForm) => {
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
          name="enable_hyde"
          render={({ field }) => (
            <FormItem className="flex items-center justify-between">
              <FormLabel tooltip={t('flow.hyde.enableHydeTip')}>
                {t('flow.hyde.enableHyde')}
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
        <FormField
          control={form.control}
          name="hyde_prompt"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.hyde.hydePromptTip')}>
                {t('flow.hyde.hydePrompt')}
              </FormLabel>
              <FormControl>
                <Textarea rows={4} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="temperature"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.hyde.temperatureTip')}>
                {t('flow.hyde.temperature')}
              </FormLabel>
              <FormControl>
                <Input type="number" step="0.1" min="0" max="2" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="max_tokens"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.hyde.maxTokensTip')}>
                {t('flow.hyde.maxTokens')}
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

export default HyDEForm;
