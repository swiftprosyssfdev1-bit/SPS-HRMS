import re
import sqlparse
from sqlparse.sql import IdentifierList, Identifier
from sqlparse.tokens import Keyword, DML

# Safe tables to query
ALLOWLIST_TABLES = {
    "core_user",
    "core_department",
    "leaves_leaverequest",
    "leaves_leavebalance",
    "attendance_attendancerecord",
    "projects_project",
    "projects_projectassignment",
}

# Extremely sensitive columns that should never be returned by SQL
DENYLIST_COLUMNS = {
    "password", 
    "is_superuser", 
    "is_staff",
    "salary",       # if we ever add it to core_user
    "account_no",   # if they join bank details
    "ifsc_code",
}

class SecurityViolation(Exception):
    pass

# Hard cap on rows a chatbot-generated SELECT can return. Protects the DB
# from an accidental "SELECT * FROM core_user" with no WHERE clause, and
# keeps the result small enough to safely pass into the second (answer-
# formatting) LLM call.
MAX_ROW_LIMIT = 20

def _extract_tables(parsed):
    """Extract table names from a parsed SQL statement."""
    # This is a basic extractor. It might not catch all complex subqueries,
    # but it's a good first line of defense.
    tables = set()
    from_seen = False
    for item in parsed.tokens:
        if from_seen:
            if isinstance(item, IdentifierList):
                for identifier in item.get_identifiers():
                    tables.add(identifier.get_real_name().lower())
            elif isinstance(item, Identifier):
                tables.add(item.get_real_name().lower())
            elif item.ttype is Keyword:
                from_seen = False
        elif item.ttype is Keyword and item.value.upper() == 'FROM':
            from_seen = True
    return tables

def _extract_columns(query_string):
    """Basic regex to find requested columns to check against denylist."""
    # A robust SQL parser would be better, but regex catches naive attempts
    query_lower = query_string.lower()
    for col in DENYLIST_COLUMNS:
        if re.search(r'\b' + col + r'\b', query_lower):
            return True
    return False

def validate_sql(user, query_string):
    """
    Validates a SQL query before execution.
    1. Must be a SELECT statement.
    2. Must only access ALLOWLIST_TABLES.
    3. Must not access DENYLIST_COLUMNS.
    4. Applies role-based blocks.
    """
    if not query_string or not query_string.strip():
        raise SecurityViolation("Empty query.")

    parsed = sqlparse.parse(query_string)[0]
    
    # 1. Must be SELECT
    if parsed.get_type() != 'SELECT':
        raise SecurityViolation("Only SELECT queries are allowed.")
    
    # 2. Check tables
    tables = _extract_tables(parsed)
    for table in tables:
        if table and table not in ALLOWLIST_TABLES:
            raise SecurityViolation(f"Access to table '{table}' is forbidden.")

    # 3. Check columns
    if _extract_columns(query_string):
        raise SecurityViolation("Query attempts to access a forbidden column.")
        
    # 4. Role-based restrictions
    # For now, only HR, Admin, or Managers can run cross-user live queries.
    # Normal employees should be restricted. (You can expand this logic)
    if user.role not in ['HR', 'SENIOR_HR', 'ADMIN']:
        # If normal user, we could theoretically rewrite the query to append "WHERE user_id = {user.id}"
        # But for safety, we simply block them from arbitrary DB access and rely on dynamic intents.
        raise SecurityViolation("Standard employees do not have permission to execute live database queries.")

    return True


def enforce_row_limit(query_string, max_rows=MAX_ROW_LIMIT):
    """Appends a LIMIT clause if the query doesn't already have one, or
    clamps an existing LIMIT down to max_rows if it's larger. Call this
    AFTER validate_sql() has confirmed the query is a safe SELECT.

    Simple regex-based approach (not full parsing) — good enough since
    the query has already been validated as a single SELECT statement
    with no subqueries beyond what validate_sql permits."""
    query = query_string.strip().rstrip(';').strip()

    existing = re.search(r'\bLIMIT\s+(\d+)\b', query, re.IGNORECASE)
    if existing:
        current_limit = int(existing.group(1))
        if current_limit > max_rows:
            query = query[:existing.start()] + f"LIMIT {max_rows}" + query[existing.end():]
        return query

    return f"{query} LIMIT {max_rows}"
