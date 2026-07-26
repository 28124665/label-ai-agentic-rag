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
import { useTranslation } from 'react-i18next';
import { INextOperatorForm } from '../../interface';

const HallucinationDetectorForm = ({ form }: INextOperatorForm) => {
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
          name="pass_threshold"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.hallucinationDetector.passThresholdTip')}>
                {t('flow.hallucinationDetector.passThreshold')}
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
          name="filter_threshold"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.hallucinationDetector.filterThresholdTip')}>
                {t('flow.hallucinationDetector.filterThreshold')}
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
          name="regenerate_threshold"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.hallucinationDetector.regenerateThresholdTip')}>
                {t('flow.hallucinationDetector.regenerateThreshold')}
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
          name="rule_weight"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.hallucinationDetector.ruleWeightTip')}>
                {t('flow.hallucinationDetector.ruleWeight')}
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
          name="nli_weight"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.hallucinationDetector.nliWeightTip')}>
                {t('flow.hallucinationDetector.nliWeight')}
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
          name="llm_weight"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.hallucinationDetector.llmWeightTip')}>
                {t('flow.hallucinationDetector.llmWeight')}
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
          name="timeout"
          render={({ field }) => (
            <FormItem>
              <FormLabel tooltip={t('flow.hallucinationDetector.timeoutTip')}>
                {t('flow.hallucinationDetector.timeout')}
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

export default HallucinationDetectorForm;
