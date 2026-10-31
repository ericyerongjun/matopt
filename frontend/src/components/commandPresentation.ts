import {
  BookOpen, Braces, Calculator, ChartNoAxesCombined, CodeXml, FilePenLine,
  FileSpreadsheet, FileText, FlaskConical, FolderOpen, Globe, GraduationCap,
  Bot, Brain, Database, Hourglass, ListChecks, MessageSquare, MousePointer2, OctagonX, StickyNote, PanelsTopLeft, PencilRuler, Presentation,
  ScanText, Search, ShieldCheck, Sigma, Terminal, Wrench, type LucideIcon,
} from "lucide-react";
import type { CommandInfo } from "../services/skills.ts";

type CommandIdentity = Pick<CommandInfo, "name"> & Partial<Pick<CommandInfo, "kind">>;
export interface CommandPresentation { label: string; Icon: LucideIcon; description?: string }

const TOOLS: Record<string, CommandPresentation> = {
  python: { label: "Python analysis", Icon: CodeXml, description: "Calculate, analyze data, and make charts." },
  bash: { label: "Run a command", Icon: Terminal, description: "Run a command for this task." },
  web_search: { label: "Search the web", Icon: Globe, description: "Find current information and sources." },
  web_fetch: { label: "Read a webpage", Icon: Globe, description: "Read and explore an online source." },
  read_file: { label: "Read files", Icon: FileText, description: "Read a file in this chat." },
  write_file: { label: "Write a file", Icon: FilePenLine, description: "Save your work as a file." },
  edit_file: { label: "Edit a file", Icon: FilePenLine, description: "Make changes to an existing file." },
  list_files: { label: "Browse files", Icon: FolderOpen, description: "Find files available in this chat." },
  grep: { label: "Search files", Icon: Search, description: "Find specific information in your files." },
  parse_document: { label: "Read a document", Icon: ScanText, description: "Read text, equations, and tables from a document." },
  render_ui: { label: "Create an interactive view", Icon: PanelsTopLeft, description: "Explore a result with interactive controls." },
  create_ui_app: { label: "Build an interactive app", Icon: PanelsTopLeft, description: "Turn an idea into something you can use." },
  register_ui_action: { label: "Add an interaction", Icon: MousePointer2, description: "Add a useful action to an interactive view." },
  check_equivalence: { label: "Check equivalence", Icon: Sigma, description: "Check whether two expressions are equivalent." },
  wolfram_alpha: { label: "Wolfram Alpha", Icon: Calculator, description: "Look up facts and solve mathematical questions." },
  update_plan: { label: "Plan a task", Icon: ListChecks, description: "Organize the steps needed to complete a task." },
  search_skills: { label: "Find a skill", Icon: Search, description: "Find a skill suited to your task." },
  load_skill: { label: "Use a skill", Icon: BookOpen, description: "Apply a skill to this task." },
  data_agent: { label: "Analyze data", Icon: ChartNoAxesCombined, description: "Explore data and explain what it shows." },
  bash_poll: { label: "Check a background command", Icon: Terminal, description: "Read new output from a command still running." },
  find_files: { label: "Find files", Icon: Search, description: "Find files by name in this chat." },
  spawn_agent: { label: "Start a sub-agent", Icon: Bot, description: "Hand part of the task to a helper agent." },
  wait_agent: { label: "Wait for a sub-agent", Icon: Hourglass, description: "Wait for a helper agent to finish." },
  send_message: { label: "Message a sub-agent", Icon: MessageSquare, description: "Send instructions to a helper agent." },
  followup_task: { label: "Give a follow-up task", Icon: Bot, description: "Ask a helper agent to continue with more work." },
  interrupt_agent: { label: "Interrupt a sub-agent", Icon: OctagonX, description: "Stop a helper agent's current work." },
  list_agents: { label: "List sub-agents", Icon: Bot, description: "See the helper agents working on this task." },
  memory_search: { label: "Search memories", Icon: Brain, description: "Look for something learned in earlier chats." },
  memory_read: { label: "Read a memory", Icon: Brain, description: "Read a saved memory." },
  memory_note: { label: "Save a memory note", Icon: StickyNote, description: "Remember something for later chats." },
  list_mcp_resources: { label: "List connected resources", Icon: Database, description: "See resources offered by connected MCP servers." },
  read_mcp_resource: { label: "Read a connected resource", Icon: Database, description: "Read a resource from a connected MCP server." },
};

const SKILLS: Record<string, CommandPresentation> = {
  "math-verification": { label: "Verify mathematics", Icon: ShieldCheck },
  "sympy-compute": { label: "Symbolic mathematics", Icon: Sigma },
  "numerical-optimization": { label: "Numerical optimization", Icon: Calculator },
  "statistical-inference": { label: "Statistical inference", Icon: ChartNoAxesCombined },
  "ml-experiment": { label: "Machine learning experiment", Icon: FlaskConical },
  "data-eda": { label: "Explore data", Icon: ChartNoAxesCombined },
  "data-agent": { label: "Analyze data", Icon: ChartNoAxesCombined },
  "data-notebook": { label: "Create a notebook", Icon: Braces },
  "data-report": { label: "Create a report", Icon: FileText },
  "data-slides": { label: "Create a presentation", Icon: Presentation },
  "frontend-design": { label: "Frontend design", Icon: PencilRuler },
  "web-artifacts-builder": { label: "Build a web artifact", Icon: PanelsTopLeft },
  "academy-guide": { label: "Learning guide", Icon: GraduationCap },
  pdf: { label: "Work with PDFs", Icon: FileText },
  docx: { label: "Create a document", Icon: FileText },
  xlsx: { label: "Work with spreadsheets", Icon: FileSpreadsheet },
  pptx: { label: "Create a presentation", Icon: Presentation },
};

const WORDS: Record<string, string> = { api: "API", csv: "CSV", css: "CSS", eda: "EDA", html: "HTML", json: "JSON", latex: "LaTeX", mcp: "MCP", ml: "ML", pdf: "PDF", sql: "SQL", ui: "UI", ux: "UX" };

/** Human-readable names are presentation only; the canonical command stays intact. */
export function commandPresentation(command: CommandIdentity): CommandPresentation {
  const key = command.name.toLowerCase().replace(/_/g, "-");
  const skill = SKILLS[key] ?? SKILLS[key.replace(/^(anthropic|matopt)[:-]/, "")];
  const tool = TOOLS[key.replace(/-/g, "_")];
  // Saved user messages retain canonical slash text, but no command kind.
  const known = command.kind === "tool" ? tool : command.kind === "skill" ? skill : skill ?? tool;
  if (known) return known;
  const words = command.name.replace(/^(anthropic|matopt|data)[:-]/i, "").split(/[-_:/\s]+/).filter(Boolean);
  const label = words.map((word, index) => WORDS[word.toLowerCase()] ?? (index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word)).join(" ");
  return { label: label || (command.kind === "skill" ? "Custom skill" : "Custom tool"), Icon: command.kind === "skill" ? BookOpen : Wrench };
}
