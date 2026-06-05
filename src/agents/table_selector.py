"""
Table Selector Agent (Idea A: Two-Stage Table Selection)
- Receives a list of ALL tables (Name and short Description only).
- Uses LLM to infer and SELECT the exact list of necessary tables.
- Helps reduce Context Size and increase SQL Generator accuracy.
"""
import json
import re
import structlog
from src.agents.llm_router import invoke
from src.config import config
from src.db import DatabaseManager

log = structlog.get_logger("table_selector")

TABLE_SELECTOR_SYSTEM = """You are an expert Database Architect.
Your task is to identify EXACTLY which tables are needed to answer the user's question.

INPUT:
- User Question
- List of available tables with their descriptions.
- Foreign Key relationships between tables.

OUTPUT FORMAT:
Strict JSON format containing only a list of table names. No markdown, no explanations.
{"selected_tables": ["table1", "table2"]}

RULES:
- Select the absolute minimum number of tables required to answer the question.
- Do NOT select tables that are loosely related but not needed for the final query.
- Use the descriptions to understand the relationships.
- CRITICAL: If you select Table A and Table B, and they do not have a direct Foreign Key, you MUST also select the bridge/intermediate tables that connect them.
- CRITICAL: If the question mentions a specific proper noun (like "AC/DC", "Nirvana"), consider which table might contain this entity (e.g. Artist/NgheSi) and include it.
"""

TABLE_SELECTOR_PROMPT = """QUESTION: {question}

AVAILABLE TABLES:
{tables_summary}

RELATIONSHIPS (FOREIGN KEYS):
{relationships}
"""

def _safe_json_parse(text: str) -> dict:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    text = text.strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError('Cannot parse JSON from LLM response.')

def select_tables_for_query(question: str, db: DatabaseManager, semantic_descriptions: dict) -> list[str]:
    schema = db.get_schema()
    
    summary_parts = []
    for table in schema.tables:
        desc = semantic_descriptions.get(table.table_name, 'No description')
        summary_parts.append(f"- {table.table_name}: {desc}")
        
    tables_summary = '\n'.join(summary_parts)
    
    rel_parts = []
    for rel in schema.relationships:
        rel_parts.append(f"- {rel['from_table']}.{rel['from_column']} -> {rel['to_table']}.{rel['to_column']}")
        
    if rel_parts:
        relationships_str = '\n'.join(rel_parts)
    else:
        relationships_str = 'None'
        
    prompt = TABLE_SELECTOR_PROMPT.format(
        question=question,
        tables_summary=tables_summary,
        relationships=relationships_str
    )
    
    try:
        raw_resp = invoke(
            prompt=prompt,
            model=config.LLM_MODEL_FLASH,
            temperature=0.0,
            max_tokens=512,
            system_prompt=TABLE_SELECTOR_SYSTEM
        )
        
        result = _safe_json_parse(raw_resp)
        selected = result.get('selected_tables', [])
        valid_tables_map = {t.table_name.lower(): t.table_name for t in schema.tables}
        selected_valid = []
        for t in selected:
            t_lower = t.lower()
            if t_lower in valid_tables_map and valid_tables_map[t_lower] not in selected_valid:
                selected_valid.append(valid_tables_map[t_lower])
        
        log.info('table_selector_success', selected_tables=selected_valid, total_tables=len(schema.tables))
        return selected_valid
    except Exception as e:
        log.error('table_selector_failed', error=str(e))
        return [t.table_name for t in schema.tables]
