import logging
from django.conf import settings
from langchain_community.utilities import SQLDatabase
from langchain_ollama import ChatOllama
from chatbot.sql_security import validate_sql, enforce_row_limit, SecurityViolation, ALLOWLIST_TABLES

logger = logging.getLogger(__name__)

_db = None

def get_sql_database():
    """Lazily load the SQLDatabase connection for Langchain."""
    global _db
    if _db is not None:
        return _db
        
    db_config = settings.DATABASES['default']
    user = db_config['USER']
    import urllib.parse
    password = urllib.parse.quote(db_config['PASSWORD'])
    host = db_config['HOST']
    port = db_config.get('PORT', '3306')
    name = db_config['NAME']
    
    # Format URI for SQLAlchemy
    uri = f"mysql+mysqldb://{user}:{password}@{host}:{port}/{name}"
    
    try:
        # Only include safe tables in the schema context passed to the LLM
        _db = SQLDatabase.from_uri(uri, include_tables=list(ALLOWLIST_TABLES))
    except Exception as e:
        logger.exception("Failed to connect SQLDatabase for Langchain.")
        return None
        
    return _db

def _get_sql_llm():
    # We MUST use a larger model for SQL generation because 0.5b hallucinates syntax.
    # We will use qwen3.5:4b (which the user already has downloaded).
    # Timeout added — this previously had none, unlike rag_engine's 170s.
    # Since this step only ever runs AFTER a RAG attempt already failed (see
    # matcher.py step 5), and it makes TWO sequential calls (SQL generation +
    # answer formatting), an unbounded call here could stack on top of RAG's
    # own timeout and blow well past the frontend's 180s abort. Kept shorter
    # than RAG's budget since this is explicitly a last-resort attempt.
    return ChatOllama(
        model=getattr(settings, "SQL_LLM_MODEL", "qwen3.5:4b"),
        temperature=0,
        timeout=getattr(settings, "SQL_LLM_TIMEOUT", 45),
    )

def query_live_data(user, question):
    """
    Takes a natural language question, generates a SQL query, validates it securely,
    executes it, and returns a natural language response.
    """
    db = get_sql_database()
    if db is None:
        return "The live database connection is temporarily unavailable."
        
    llm = _get_sql_llm()
    
    # 1. Ask LLM to generate SQL
    try:
        # Get schema for allowed tables
        schema_info = db.get_table_info()
        
        prompt = f"""You are a MySQL expert. Write a valid MySQL query to answer the question.
ONLY output the raw SQL query. Do not add any explanation.

Rules:
- You must ONLY use the following tables:
{schema_info}
- Never query passwords or salaries.

Example 1:
Question: How many users are in the database?
SQLQuery: SELECT COUNT(*) FROM core_user;

Example 2:
Question: Who is the manager of the IT department?
SQLQuery: SELECT manager_id FROM core_department WHERE name = 'IT';

Question: {question}
SQLQuery:"""
        
        raw_sql = llm.invoke(prompt).content
        
        # Clean up output (small models often add markdown even when told not to)
        sql_query = raw_sql.replace("```sql", "").replace("```", "").replace("\n", " ").strip()
        
        # Extract the SELECT statement using regex in case the LLM added conversational text
        import re
        match = re.search(r'(SELECT.*?)(?:;|$)', sql_query, re.IGNORECASE)
        if match:
            sql_query = match.group(1)
            
    except Exception as e:
        logger.exception("SQL Generation failed.")
        return "I had trouble understanding how to query that data."
        
    # 2. Validate SQL securely
    try:
        validate_sql(user, sql_query)
    except SecurityViolation as e:
        logger.warning(f"SQL Security Blocked Query: {sql_query}. Reason: {e}")
        return f"I cannot answer that: {str(e)}"
    except Exception as e:
        logger.exception("SQL validation error.")
        return "I'm not allowed to run that specific query."

    # 2b. Cap row count — validated query only, so it's safe to append/clamp LIMIT.
    sql_query = enforce_row_limit(sql_query)

    # 3. Execute SQL
    try:
        result = db.run(sql_query)
        if not result or result == '[]':
            return "I couldn't find any data matching your request."
    except Exception as e:
        logger.error(f"SQL Execution failed on query: {sql_query}. Error: {e}")
        return "An error occurred while fetching the data from the database."
        
    # 4. Generate Natural Language Answer from the result
    # NOTE: reuse the SAME model used for SQL generation here. It was
    # previously falling back to settings.RAG_LLM_MODEL (default
    # "qwen:0.5b") for this step — a much weaker model than the one that
    # generated the SQL, which could mangle the formatted answer even when
    # the query itself was correct.
    try:
        fast_llm = _get_sql_llm()
        answer_prompt = f"""Based on the following data from the database, answer the user's question concisely.

Question: {question}
Database Result: {result}

Answer:"""
        final_response = fast_llm.invoke(answer_prompt).content
        return (final_response or "").strip()
    except Exception as e:
        logger.exception("SQL Answer generation failed.")
        return f"I found the data, but had trouble formatting it: {result}"
