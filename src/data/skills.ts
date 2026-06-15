export interface Skill {
  id: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  sampleQuestions?: string[];
  sandboxable?: boolean;
}

export const skills: Skill[] = [
  {
    id: 'websearch',
    name: 'Web Search',
    description: 'Discover datasets on modal agency portals via Tavily',
    category: 'tools',
    icon: '🌐',
    sampleQuestions: [
      'What FAA datasets exist that are not on data.transportation.gov?',
      'Find NHTSA crash datasets published outside the DOT portal',
    ],
    sandboxable: true,
  },
  {
    id: 'fileio',
    name: 'File I/O',
    description: 'Read, write, edit files & search code',
    category: 'tools',
    icon: '📁',
    sampleQuestions: [
      'Show me the latest gaps.json summary',
      'List all files in the workspace/reports folder',
    ],
    sandboxable: true,
  },
  {
    id: 'execute',
    name: 'Shell Execution',
    description: 'Run shell commands, Python scripts & system tools',
    category: 'tools',
    icon: '💻',
    sampleQuestions: [
      'Run a Python script to count datasets by category',
      'Parse gaps.json and show the top 10 missing datasets',
    ],
    sandboxable: true,
  },
  {
    id: 'transportation_research',
    name: 'Transportation Research',
    description: 'Catalog DOT portal, search data.gov, analyze gaps, export reports',
    category: 'tools',
    icon: '🚆',
    sampleQuestions: [
      'What aviation datasets are on catalog.data.gov but missing from data.transportation.gov?',
      'Which portal categories have stale or empty datasets?',
      'Run a full gap analysis and save a report',
    ],
    sandboxable: false,
  },
  {
    id: 'superpowers',
    name: 'Superpowers',
    description: 'TDD, planning & debugging methodology',
    category: 'skills',
    icon: '⚡',
    sampleQuestions: ['Help me plan a multi-step data catalog audit', 'Debug a gap analysis script using TDD'],
    sandboxable: false,
  },
  {
    id: 'code_review',
    name: 'Code Review',
    description: 'Systematic code quality & correctness analysis',
    category: 'skills',
    icon: '🔍',
    sampleQuestions: ['Review the gap analysis matching logic', 'Review this catalog fetch script'],
    sandboxable: false,
  },
];

export const comingSoonSkills: Array<{ name: string; icon: string; description: string }> = [
  { name: 'Socrata API', icon: '🔌', description: 'Query individual dataset views' },
  { name: 'Scheduled Harvest', icon: '⏰', description: 'Automated catalog monitoring' },
  { name: 'MCP Tools', icon: '🔧', description: 'Model Context Protocol integrations' },
];

export const skillCategories = {
  tools: {
    name: 'Tools',
    description: 'Agent capabilities',
  },
} as const;

export type SkillCategory = keyof typeof skillCategories;
