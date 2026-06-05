"""
Column Pruner Agent (Idea B: Column Pruning)
- Receives the list of SELECTED tables and all their columns.
- Uses LLM to remove columns irrelevant to the question.
- Mandatory to keep Primary Keys and Foreign Keys to ensure SQL Generator can JOIN.
"""
import json
import re
import structlog
from src.agents.llm_router import invoke
from src.config import config
from src.db import DatabaseManager

log = structlog.get_logger("column_pruner")

COLUMN_PRUNER_SYSTEM = """You are an expert Database Architect.
Your task is to prune irrelevant columns from the given database tables to reduce context size.

INPUT:
- User Question
- Selected Tables with all their columns.
- Foreign Key relationships.

OUTPUT FORMAT:
Strict JSON format containing a dictionary mapping table names to a list of REQUIRED columns.
{
  "table_name_1": ["col1", "col2"],
  "table_name_2": ["col1", "col2"]
}

RULES:
1. ONLY include columns that are directly related to answering the user's question (e.g. for filtering, aggregating, or selecting).
2. CRITICAL: You MUST ALWAYS include ALL Primary Key columns for the tables.
3. CRITICAL: You MUST ALWAYS include ALL Foreign Key columns that connect these selected tables.
4. DO NOT include audit columns (created_at, updated_at, etc.) or irrelevant data columns unless requested.
"""

COLUMN_PRUNER_PROMPT = """QUESTION: {question}

SELECTED TABLES & COLUMNS:
{tables_and_columns}

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

def prune_columns_for_query(question: str, db: DatabaseManager, selected_tables: list[str]) -> dict:
    if not selected_tables:
        return {}
        
    schema = db.get_schema()
    
    tc_parts = []
    for table in schema.tables:
        if table.table_name in selected_tables:
            cols = ', '.join(f"{c['name']} ({c['type']})" for c in table.columns)
            tc_parts.append(f"Table: {table.table_name}\nColumns: {cols}")
            
    tables_and_columns = '\n\n'.join(tc_parts)
    
    rel_parts = []
    for rel in schema.relationships:
        if rel['from_table'] in selected_tables and rel['to_table'] in selected_tables:
            rel_parts.append(f"- {rel['from_table']}.{rel['from_column']} -> {rel['to_table']}.{rel['to_column']}")
            
    if rel_parts:
        relationships_str = '\n'.join(rel_parts)
    else:
        relationships_str = 'None'
        
    prompt = COLUMN_PRUNER_PROMPT.format(
        question=question,
        tables_and_columns=tables_and_columns,
        relationships=relationships_str
    )
    
    try:
        raw_resp = invoke(
            prompt=prompt,
            model=config.LLM_MODEL_FLASH,
            temperature=0.0,
            max_tokens=1024,
            system_prompt=COLUMN_PRUNER_SYSTEM
        )
        
        pruned_result = _safe_json_parse(raw_resp)
        log.info('column_pruner_success', tables=list(pruned_result.keys()))
        return pruned_result
    except Exception as e:
        log.error('column_pruner_failed', error=str(e))
        return None
