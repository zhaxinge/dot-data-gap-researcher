"""
DOT Data Gap Research Agent — deepagents-powered LangGraph agent
for analyzing public-facing transportation datasets on data.transportation.gov.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, LocalShellBackend
from langgraph.checkpoint.memory import MemorySaver
from langchain_nvidia_ai_endpoints import ChatNVIDIA

# Ensure backend/ is on path for tools package
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Workspace directories (Windows-compatible)
WORKSPACE_DIR = Path(os.getenv("DOT_AGENT_WORKSPACE", BACKEND_DIR / "workspace")).resolve()
SANDBOX_WORKSPACE_DIR = "/workspace"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
(WORKSPACE_DIR / "data").mkdir(parents=True, exist_ok=True)
(WORKSPACE_DIR / "reports").mkdir(parents=True, exist_ok=True)

SKILLS_DIR = BACKEND_DIR / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

checkpointer = MemorySaver()

MODEL_MAP = {
    "nemotron": "nvidia/nemotron-3-super-120b-a12b",
    "llama": "meta/llama-3.3-70b-instruct",
    "deepseek": "deepseek-ai/deepseek-r1-0528",
    "claude": "meta/llama-3.3-70b-instruct",
}

MODEL_DISPLAY_NAMES = {
    "nemotron": "Nemotron (NVIDIA)",
    "llama": "Llama 3.3 (Meta)",
    "deepseek": "DeepSeek R1 (DeepSeek)",
    "claude": "Claude-style (Meta fallback)",
}

INTERRUPT_TOOLS = {
    "write_file": True,
    "edit_file": True,
    "execute": True,
}

DEFAULT_SKILL_IDS = ["websearch", "fileio", "execute", "transportation_research"]


def _get_model(model_id: str = "llama"):
    """Return an NVIDIA NIM chat model for the given model ID."""
    api_key = os.getenv("NVIDIA_API_KEY")
    model_name = MODEL_MAP.get(model_id, MODEL_MAP["llama"])
    print(f"[Agent] Using model: {model_name} (id={model_id})")
    return ChatNVIDIA(
        model=model_name,
        api_key=api_key,
        temperature=0.3,
    )


def _build_extra_tools(skill_ids: list[str]) -> list:
    """Build additional tools based on user-selected skills."""
    tools = []

    if "websearch" in skill_ids:
        try:
            from langchain_community.tools.tavily_search import TavilySearchResults

            tavily_key = os.getenv("TAVILY_API_KEY")
            if tavily_key:
                tools.append(TavilySearchResults(max_results=5, api_key=tavily_key))
                print("[Agent] Added Tavily web search tool")
        except ImportError:
            print("[Agent] Tavily not available")

    if "transportation_research" in skill_ids:
        try:
            from tools import ALL_TOOLS

            tools.extend(ALL_TOOLS)
            print(f"[Agent] Added {len(ALL_TOOLS)} transportation catalog tools")
        except Exception as e:
            print(f"[Agent] Transportation tools not available: {e}")

    return tools


def _load_skill_content(skill_ids: list[str]) -> str:
    """Load skill markdown files based on selected skills."""
    skill_files = {
        "transportation_research": "transportation_data_research.md",
        "superpowers": "superpowers.md",
        "cudf": "cudf.md",
        "code_review": "code_review.md",
        "cuopt": "cuopt.md",
    }

    skill_content = []
    for sid in skill_ids:
        filename = skill_files.get(sid)
        if filename:
            filepath = SKILLS_DIR / filename
            if filepath.exists():
                skill_content.append(filepath.read_text(encoding="utf-8"))
                print(f"[Agent] Loaded skill: {filename}")

    return "\n\n---\n\n".join(skill_content)


def _get_skill_sources() -> list[str]:
    """Get list of skill source directories if any skills exist."""
    if SKILLS_DIR.exists() and any(SKILLS_DIR.iterdir()):
        return ["/skills/"]
    return []


def _build_system_prompt(
    skill_ids: list[str],
    model_id: str,
    hitl_enabled: bool,
    sandbox_enabled: bool = False,
) -> str:
    """Create a system prompt for DOT open data gap research."""
    model_name = MODEL_DISPLAY_NAMES.get(model_id, "AI Model")
    workspace = str(SANDBOX_WORKSPACE_DIR if sandbox_enabled else WORKSPACE_DIR)

    enabled = []
    if "websearch" in skill_ids:
        enabled.append("- Web Search (tavily_search_results_json): discover datasets on modal agency portals")
    if "fileio" in skill_ids:
        enabled.append("- File I/O (read_file, write_file, edit_file, ls, glob, grep): manage research artifacts")
    if "execute" in skill_ids:
        enabled.append("- Shell Execution (execute): run Python scripts and data processing")
    if "transportation_research" in skill_ids:
        enabled.append("- fetch_dot_catalog: download data.transportation.gov/data.json")
        enabled.append("- search_data_gov: query catalog.data.gov for DOT datasets")
        enabled.append("- analyze_catalog_gaps: deterministic diff and gap detection")
        enabled.append("- export_gap_report: write markdown report to workspace/reports/")
    if "superpowers" in skill_ids:
        enabled.append("- Superpowers: structured planning and debugging methodology")

    builtin = ["- Planning (write_todos): organize multi-step research tasks"]
    caps_text = "\n".join(enabled + builtin)

    research_rules = ""
    if "transportation_research" in skill_ids:
        research_rules = """
6. ALWAYS call fetch_dot_catalog and analyze_catalog_gaps before concluding what is missing.
7. Use search_data_gov to build the reference catalog from catalog.data.gov.
8. Use Tavily only for cross-portal discovery (FAA, FHWA, FRA, FTA, NHTSA, etc.) — not as sole evidence.
9. Never invent dataset names; cite tool output and URLs.
10. Finish with export_gap_report when the user asks for a full analysis."""

    hitl_note = ""
    if hitl_enabled:
        hitl_note = """
NOTE: Some tools (write_file, edit_file, execute) require human approval.
The user will be asked to approve, edit, or reject your tool calls before they execute."""

    skill_content = _load_skill_content(skill_ids)
    skill_section = f"\n\n---\n\n{skill_content}" if skill_content else ""

    return f"""You are a DOT Open Data Gap Analyst — a research agent specialized in
identifying missing and underrepresented public-facing transportation datasets on
https://data.transportation.gov compared to federal catalogs and modal agency sources.

Your foundation model is: {model_name}

Your enabled capabilities:
{caps_text}

CRITICAL RULES:
1. Answer the user's question DIRECTLY. Do NOT use the 'task' tool — respond yourself.
2. File tools require ABSOLUTE paths. Your workspace is: {workspace}
   Always use paths like: {workspace}/data/gaps.json
3. For gap analysis questions, follow the transportation research methodology skill.
4. Produce structured output: Executive Summary, Gaps by Modal, Gaps by Category,
   Data Quality Issues, Recommended Additions, Sources.
5. Be concise, evidence-based, and technically accurate.{research_rules}
{hitl_note}{skill_section}"""


def _build_backend(skill_ids: list[str], sandbox_map: dict[str, bool]):
    """Build execution backend. Returns (backend, sandbox_instance_or_None)."""
    any_sandboxed = any(sandbox_map.get(sid, False) for sid in skill_ids)

    if any_sandboxed:
        try:
            from docker_sandbox import DockerSandboxBackend

            backend = DockerSandboxBackend()
            sandboxed_tools = [k for k, v in sandbox_map.items() if v]
            print(f"[Agent] Docker sandbox created for tools: {sandboxed_tools}")
            return backend, backend
        except Exception as e:
            print(f"[Agent] WARNING: Failed to create Docker sandbox: {e}. Falling back to local.")

    workspace = str(WORKSPACE_DIR)
    print(f"[Agent] Sandbox mode OFF — using local workspace: {workspace}")

    if "execute" in skill_ids:
        backend = LocalShellBackend(
            root_dir=workspace,
            timeout=120.0,
            max_output_bytes=100000,
            inherit_env=True,
        )
        print("[Agent] Shell execution enabled via LocalShellBackend")
    else:
        backend = FilesystemBackend(root_dir=workspace)
        print("[Agent] Using FilesystemBackend")

    return backend, None


def create_agent(
    skill_ids: list[str] | None = None,
    model_id: str = "llama",
    hitl_enabled: bool = False,
    sandbox_map: dict[str, bool] | None = None,
):
    """
    Create a DOT Data Gap Research Agent.
    Returns (agent, sandbox_instance_or_None).
    """
    if skill_ids is None:
        skill_ids = DEFAULT_SKILL_IDS.copy()
    if sandbox_map is None:
        sandbox_map = {}

    model = _get_model(model_id)
    extra_tools = _build_extra_tools(skill_ids)
    any_sandboxed = any(sandbox_map.get(sid, False) for sid in skill_ids)
    system_prompt = _build_system_prompt(skill_ids, model_id, hitl_enabled, any_sandboxed)
    skill_sources = _get_skill_sources()

    backend, sandbox = _build_backend(skill_ids, sandbox_map)

    agent_kwargs: dict = {
        "model": model,
        "tools": extra_tools if extra_tools else None,
        "system_prompt": system_prompt,
        "backend": backend,
        "checkpointer": checkpointer,
    }

    if hitl_enabled:
        agent_kwargs["interrupt_on"] = INTERRUPT_TOOLS

    if skill_sources:
        agent_kwargs["skills"] = skill_sources

    agent = create_deep_agent(**agent_kwargs)
    sandboxed = [k for k, v in sandbox_map.items() if v]
    print(f"[Agent] Created DOT gap research agent: skills={skill_ids}, hitl={hitl_enabled}, sandboxed={sandboxed}")
    return agent, sandbox
