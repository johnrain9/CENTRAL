#!/usr/bin/env python3
"""
Phase 1: Extract compaction summaries from worker acompact JSONL files.

Walks ~/.claude/projects/*/subagents/agent-acompact-*.jsonl, extracts:
  - The compaction summary (final assistant text)
  - The parent task ID (from parent session's first user message)
  - Metadata (session ID, file paths mentioned, timestamp)

Outputs: state/compaction-extracts/raw-summaries.jsonl
"""

import json
import glob
import os
import re
import sys
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state" / "compaction-extracts"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# Map project directory names to repo identifiers
PROJECT_DIRS = {
    "-Users-paul-projects-ecosystem": "ecosystem",
    "-Users-paul-projects-CENTRAL": "CENTRAL",
    "-Users-paul-projects-photo_auto_tagging": "photo_auto_tagging",
    "-Users-paul-projects-Dispatcher": "Dispatcher",
}

# Subsystem taxonomy for file-path-based hints
SUBSYSTEM_MAP = {
    "agentic_loop/": "agentic-loop",
    "context_manager/": "context",
    "inference/": "inference",
    "persistence/": "persistence",
    "tool_executor/": "tool-executor",
    "message_router/": "message-router",
    "session_manager/": "session-manager",
    "web/": "web",
    "runtime.rs": "runtime",
    "main.rs": "runtime",
    "task_manager/": "task-manager",
}


def extract_summary(acompact_path: str) -> str | None:
    """Extract the final assistant text from an acompact JSONL file."""
    events = []
    with open(acompact_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Walk backwards to find the last assistant message with text content
    for e in reversed(events):
        msg = e.get("message", {})
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    return c["text"]
        elif isinstance(content, str) and content.strip():
            return content
    return None


def extract_task_id(parent_session_path: str) -> str | None:
    """Extract the task ID from the parent session's first user message."""
    try:
        with open(parent_session_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = e.get("message", {})
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = [c.get("text", "") for c in content
                                  if isinstance(c, dict) and c.get("type") == "text"]
                    content = " ".join(text_parts)
                if not isinstance(content, str):
                    continue

                # Look for task ID patterns: ECO-123, CENTRAL-OPS-45, PQ-7, etc.
                match = re.search(r'\b([A-Z][\w-]*-\d+)\b', content)
                if match:
                    return match.group(1)
                # Also check for "do task XXX" pattern
                match = re.search(r'do task\s+(\S+)', content, re.IGNORECASE)
                if match:
                    return match.group(1)
                return None
    except (FileNotFoundError, PermissionError):
        return None


def extract_file_paths(text: str) -> list[str]:
    """Extract Rust source file paths mentioned in the summary."""
    # Match patterns like src/foo/bar.rs, agentic_loop/mod.rs, etc.
    paths = re.findall(r'(?:src/)?(\w+/[\w/]*\.rs)', text)
    # Also match standalone filenames like runtime.rs
    paths += re.findall(r'\b(\w+\.rs)\b', text)
    return sorted(set(paths))


def classify_subsystems(file_paths: list[str], text: str) -> list[str]:
    """Guess which subsystems are touched based on file paths and text."""
    subsystems = set()
    for path in file_paths:
        for prefix, subsystem in SUBSYSTEM_MAP.items():
            if prefix in path:
                subsystems.add(subsystem)
                break
    # Also check text for subsystem keywords
    text_lower = text.lower()
    keyword_map = {
        "agentic loop": "agentic-loop",
        "turn cycle": "agentic-loop",
        "stream consumer": "agentic-loop",
        "context assembl": "context",
        "compaction": "context",
        "summary generat": "context",
        "inference": "inference",
        "provider": "inference",
        "anthropic": "inference",
        "openai": "inference",
        "persistence": "persistence",
        "sqlite": "persistence",
        "migration": "persistence",
        "tool executor": "tool-executor",
        "path validator": "tool-executor",
        "approval": "tool-executor",
        "message router": "message-router",
        "broadcast": "message-router",
        "request.reply": "message-router",
        "session manager": "session-manager",
        "session_manager": "session-manager",
        "rollback": "session-manager",
        "websocket": "web",
        "http handler": "web",
        "api endpoint": "web",
        "runtime": "runtime",
        "startup": "runtime",
    }
    for keyword, subsystem in keyword_map.items():
        if keyword in text_lower:
            subsystems.add(subsystem)
    return sorted(subsystems)


def extract_timestamp(acompact_path: str) -> str | None:
    """Get the timestamp of the first event in the acompact file."""
    try:
        with open(acompact_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    ts = e.get("timestamp")
                    if ts:
                        return ts
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, PermissionError):
        pass
    return None


def identify_repo(acompact_path: str) -> str:
    """Identify which repo this acompact file belongs to."""
    for dir_name, repo in PROJECT_DIRS.items():
        if dir_name in acompact_path:
            return repo
    return "unknown"


def is_worker_session(parent_session_path: str) -> bool:
    """Check if a session is a worker session (starts with task prompt)."""
    try:
        with open(parent_session_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = e.get("message", {})
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = [c.get("text", "") for c in content
                                  if isinstance(c, dict) and c.get("type") == "text"]
                    content = " ".join(text_parts)
                if not isinstance(content, str):
                    continue
                # Worker prompts typically start with ## Objective or contain task IDs
                indicators = ["## Objective", "## Context", "## Scope",
                              "## Deliverables", "## Acceptance",
                              "Dispatch from CENTRAL", "do task"]
                return any(ind in content for ind in indicators)
    except (FileNotFoundError, PermissionError):
        pass
    return False


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    all_acompact = []
    for project_dir in CLAUDE_PROJECTS.iterdir():
        if not project_dir.is_dir():
            continue
        pattern = str(project_dir / "*/subagents/agent-acompact-*.jsonl")
        all_acompact.extend(glob.glob(pattern))

    print(f"Found {len(all_acompact)} acompact files across all projects")

    results = []
    worker_count = 0
    interactive_count = 0

    for acompact_path in sorted(all_acompact):
        # Identify parent session
        parts = acompact_path.split("/subagents/")
        parent_dir = parts[0]
        parent_session_id = os.path.basename(parent_dir)
        parent_jsonl = parent_dir + ".jsonl"

        repo = identify_repo(acompact_path)
        is_worker = is_worker_session(parent_jsonl)
        task_id = extract_task_id(parent_jsonl) if is_worker else None

        if is_worker:
            worker_count += 1
        else:
            interactive_count += 1

        summary = extract_summary(acompact_path)
        if not summary:
            continue

        file_paths = extract_file_paths(summary)
        subsystems = classify_subsystems(file_paths, summary)
        timestamp = extract_timestamp(acompact_path)

        agent_id = os.path.basename(acompact_path).replace(".jsonl", "").replace("agent-acompact-", "")

        record = {
            "agent_id": agent_id,
            "repo": repo,
            "session_id": parent_session_id,
            "session_type": "worker" if is_worker else "interactive",
            "task_id": task_id,
            "timestamp": timestamp,
            "subsystems_hint": subsystems,
            "file_paths_mentioned": file_paths,
            "summary_length": len(summary),
            "summary": summary,
        }
        results.append(record)

    # Write output
    output_path = STATE_DIR / "raw-summaries.jsonl"
    with open(output_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    print(f"\nExtracted {len(results)} compaction summaries")
    print(f"  Worker sessions: {worker_count}")
    print(f"  Interactive sessions: {interactive_count}")
    print(f"  Repos: {set(r['repo'] for r in results)}")
    print(f"\nOutput: {output_path}")

    # Print subsystem distribution
    from collections import Counter
    subsystem_counts = Counter()
    for r in results:
        for s in r["subsystems_hint"]:
            subsystem_counts[s] += 1
    print("\nSubsystem mentions:")
    for s, c in subsystem_counts.most_common():
        print(f"  {s}: {c}")


if __name__ == "__main__":
    main()
