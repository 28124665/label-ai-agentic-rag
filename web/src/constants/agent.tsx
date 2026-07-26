import { setInitialChatVariableEnabledFieldValue } from '@/utils/chat';
import {
  Circle,
  CircleDashed,
  CircleDotDashed,
  CircleSlash2,
} from 'lucide-react';
import { ChatVariableEnabledField, variableEnabledFieldMap } from './chat';

export enum ProgrammingLanguage {
  Python = 'python',
  Javascript = 'javascript',
}

export const CodeTemplateStrMap = {
  [ProgrammingLanguage.Python]: `def main(arg1: str, arg2: str) -> str:
    return f"result: {arg1 + arg2}"
`,
  [ProgrammingLanguage.Javascript]: `const axios = require('axios');
async function main({}) {
  try {
    const response = await axios.get('https://github.com/infiniflow/ragflow');
    return 'Body:' + response.data;
  } catch (error) {
    return 'Error:' + error.message;
  }
}`,
};

export enum AgentGlobals {
  SysQuery = 'sys.query',
  SysUserId = 'sys.user_id',
  SysConversationTurns = 'sys.conversation_turns',
  SysFiles = 'sys.files',
  SysHistory = 'sys.history',
}

export const AgentGlobalsSysQueryWithBrace = `{${AgentGlobals.SysQuery}}`;

export const variableCheckBoxFieldMap = Object.keys(
  variableEnabledFieldMap,
).reduce<Record<string, boolean>>((pre, cur) => {
  pre[cur] = setInitialChatVariableEnabledFieldValue(
    cur as ChatVariableEnabledField,
  );
  return pre;
}, {});

export const initialLlmBaseValues = {
  ...variableCheckBoxFieldMap,
  temperature: 0.1,
  top_p: 0.3,
  frequency_penalty: 0.7,
  presence_penalty: 0.4,
  max_tokens: 256,
};

export enum AgentCategory {
  AgentCanvas = 'agent_canvas',
  DataflowCanvas = 'dataflow_canvas',
}

export enum AgentQuery {
  Category = 'category',
}

export enum DataflowOperator {
  Begin = 'File',
  Note = 'Note',
  Parser = 'Parser',
  Tokenizer = 'Tokenizer',
  Splitter = 'Splitter',
  HierarchicalMerger = 'HierarchicalMerger',
  Extractor = 'Extractor',
}

export enum Operator {
  Begin = 'Begin',
  Retrieval = 'Retrieval',
  Categorize = 'Categorize',
  Message = 'Message',
  RewriteQuestion = 'RewriteQuestion',
  DuckDuckGo = 'DuckDuckGo',
  Wikipedia = 'Wikipedia',
  PubMed = 'PubMed',
  ArXiv = 'ArXiv',
  Google = 'Google',
  Bing = 'Bing',
  GoogleScholar = 'GoogleScholar',
  GitHub = 'GitHub',
  ExeSQL = 'ExeSQL',
  Switch = 'Switch',
  WenCai = 'WenCai',
  YahooFinance = 'YahooFinance',
  Note = 'Note',
  Crawler = 'Crawler',
  Invoke = 'Invoke',
  Email = 'Email',
  Iteration = 'Iteration',
  IterationStart = 'IterationItem',
  Code = 'CodeExec',
  WaitingDialogue = 'WaitingDialogue',
  Agent = 'Agent',
  Tool = 'Tool',
  TavilySearch = 'TavilySearch',
  TavilyExtract = 'TavilyExtract',
  UserFillUp = 'UserFillUp',
  StringTransform = 'StringTransform',
  SearXNG = 'SearXNG',
  PDFGenerator = 'PDFGenerator',
  Placeholder = 'Placeholder',
  DataOperations = 'DataOperations',
  ListOperations = 'ListOperations',
  VariableAssigner = 'VariableAssigner',
  VariableAggregator = 'VariableAggregator',
  File = 'File', // pipeline
  Parser = 'Parser',
  Tokenizer = 'Tokenizer',
  Splitter = 'Splitter',
  HierarchicalMerger = 'HierarchicalMerger',
  Extractor = 'Extractor',
  Loop = 'Loop',
  LoopStart = 'LoopItem',
  ExitLoop = 'ExitLoop',
  ExcelProcessor = 'ExcelProcessor',
  // Phase 2 RAG Enhancement Components
  Grader = 'Grader',
  HallucinationDetector = 'HallucinationDetector',
  QueryRewriter = 'QueryRewriter',
  SubQueryDecomposer = 'SubQueryDecomposer',
  HyDE = 'HyDE',
  RetryController = 'RetryController',
}

export enum ComparisonOperator {
  Equal = '=',
  NotEqual = '≠',
  GreatThan = '>',
  GreatEqual = '≥',
  LessThan = '<',
  LessEqual = '≤',
  Contains = 'contains',
  NotContains = 'not contains',
  StartWith = 'start with',
  EndWith = 'end with',
  Empty = 'empty',
  NotEmpty = 'not empty',
  In = 'in',
  NotIn = 'not in',
}

export const SwitchOperatorOptions = [
  { value: ComparisonOperator.Equal, label: 'equal', icon: 'equal' },
  { value: ComparisonOperator.NotEqual, label: 'notEqual', icon: 'not-equals' },
  { value: ComparisonOperator.GreatThan, label: 'gt', icon: 'Less' },
  {
    value: ComparisonOperator.GreatEqual,
    label: 'ge',
    icon: 'Greater-or-equal',
  },
  { value: ComparisonOperator.LessThan, label: 'lt', icon: 'Less' },
  { value: ComparisonOperator.LessEqual, label: 'le', icon: 'less-or-equal' },
  { value: ComparisonOperator.Contains, label: 'contains', icon: 'Contains' },
  {
    value: ComparisonOperator.NotContains,
    label: 'notContains',
    icon: 'not-contains',
  },
  {
    value: ComparisonOperator.StartWith,
    label: 'startWith',
    icon: 'list-start',
  },
  { value: ComparisonOperator.EndWith, label: 'endWith', icon: 'list-end' },
  {
    value: ComparisonOperator.Empty,
    label: 'empty',
    icon: <Circle className="size-4" />,
  },
  {
    value: ComparisonOperator.NotEmpty,
    label: 'notEmpty',
    icon: <CircleSlash2 className="size-4" />,
  },
  {
    value: ComparisonOperator.In,
    label: 'in',
    icon: <CircleDotDashed className="size-4" />,
  },
  {
    value: ComparisonOperator.NotIn,
    label: 'notIn',
    icon: <CircleDashed className="size-4" />,
  },
];

export const AgentStructuredOutputField = 'structured';

export enum JsonSchemaDataType {
  String = 'string',
  Number = 'number',
  Boolean = 'boolean',
  Array = 'array',
  Object = 'object',
}

export enum SwitchLogicOperator {
  And = 'and',
  Or = 'or',
}

export const WebhookJWTAlgorithmList = [
  'hs256',
  'hs384',
  'hs512',
  'rs256',
  'rs384',
  'rs512',
  'es256',
  'es384',
  'es512',
  'ps256',
  'ps384',
  'ps512',
  'none',
] as const;

export enum AgentDialogueMode {
  Conversational = 'conversational',
  Task = 'task',
  Webhook = 'Webhook',
}

export const initialBeginValues = {
  mode: AgentDialogueMode.Conversational,
  prologue: `Hi! I'm your assistant. What can I do for you?`,
};

// Phase 2 RAG Enhancement Components - Initial Form Values
export const initialGraderValues = {
  query: AgentGlobalsSysQueryWithBrace,
  documents: '',
  eval_mode: 'llm',
  batch_size: 5,
  relevance_threshold: 0.7,
  max_eval_tokens: 2000,
  timeout: 30,
  max_retry_on_parse_error: 2,
  backup_llm_model: '',
  outputs: {
    graded_documents: {
      type: 'Array<Object>',
      value: [],
    },
    relevant_count: {
      type: 'integer',
      value: 0,
    },
  },
};

export const initialHallucinationDetectorValues = {
  answer: '',
  documents: '',
  pass_threshold: 0.85,
  filter_threshold: 0.6,
  regenerate_threshold: 0.3,
  rule_weight: 0.4,
  nli_weight: 0.4,
  llm_weight: 0.2,
  timeout: 30,
  outputs: {
    faithfulness_score: {
      type: 'number',
      value: 0,
    },
    action: {
      type: 'string',
      value: '',
    },
  },
};

export const initialQueryRewriterValues = {
  ...initialLlmBaseValues,
  query: AgentGlobalsSysQueryWithBrace,
  strategy: 'synonym_rewrite',
  use_llm_for_synonyms: false,
  outputs: {
    rewritten_query: {
      type: 'string',
      value: '',
    },
    complexity_type: {
      type: 'string',
      value: '',
    },
  },
};

export const initialSubQueryDecomposerValues = {
  ...initialLlmBaseValues,
  query: AgentGlobalsSysQueryWithBrace,
  min_count: 2,
  max_count: 5,
  top_k: 10,
  dedup_threshold: 0.92,
  rrf_k: 60,
  outputs: {
    sub_queries: {
      type: 'Array<string>',
      value: [],
    },
  },
};

export const initialHyDEValues = {
  ...initialLlmBaseValues,
  query: AgentGlobalsSysQueryWithBrace,
  enable_hyde: false,
  hyde_prompt: '',
  temperature: 0.3,
  max_tokens: 256,
  outputs: {
    hyde_query: {
      type: 'string',
      value: '',
    },
  },
};

export const initialRetryControllerValues = {
  has_relevant: 'sys.has_relevant',
  relevant_count: 'sys.relevant_count',
  retry_count: 'sys.retry_count',
  accumulated_retry_tokens: 'sys.accumulated_retry_tokens',
  max_retries: 3,
  max_retry_tokens: 2000,
  min_relevant_docs: 2,
  is_chitchat: 'sys.is_chitchat',
  web_search_fallback_triggered: 'sys.web_search_fallback_triggered',
  strategy: 'sys.selected_strategy',
  outputs: {
    should_retry: {
      type: 'boolean',
      value: false,
    },
    stop_reason: {
      type: 'string',
      value: '',
    },
  },
};

export const BeginId = 'begin';

export const EmptyDsl = {
  graph: {
    nodes: [
      {
        id: BeginId,
        type: 'beginNode',
        position: {
          x: 50,
          y: 200,
        },
        data: {
          label: 'Begin',
          name: 'begin',
          form: initialBeginValues,
        },
        sourcePosition: 'left',
        targetPosition: 'right',
      },
    ],
    edges: [],
  },
  components: {
    begin: {
      obj: {
        component_name: 'Begin',
        params: {},
      },
      downstream: [], // other edge target is downstream, edge source is current node id
      upstream: [], // edge source is upstream, edge target is current node id
    },
  },
  retrieval: [], // reference
  history: [],
  path: [],
  variables: [],
  globals: {
    [AgentGlobals.SysQuery]: '',
    [AgentGlobals.SysUserId]: '',
    [AgentGlobals.SysConversationTurns]: 0,
    [AgentGlobals.SysFiles]: [],
    [AgentGlobals.SysHistory]: [],
  },
};
