"""
langchain_pipeline.py  —  Shopy True Text-to-SQL Pipeline (Phase 2)
======================================================================
Implements a strict 2-step LangChain flow:

  1. Generate SQL from natural language using an LLM and schema context.
  2. Validate SQL BEFORE execution (must be a single SELECT).
  3. Execute against read-only credentials.
  4. Synthesize plain-English response.

This keeps UI contracts unchanged and satisfies security ordering:
validation happens before any database execution.
"""

import os
import re
import threading
import time
from urllib.parse import quote_plus

from dotenv import load_dotenv
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI

load_dotenv(override=True)


# ── Model failover list (same priority order as prompts.py) ──────────────────
_FALLBACK_MODELS = [
    (os.getenv("OPENROUTER_MODEL") or "").strip(),
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/free",
    "openai/gpt-oss-120b:free",
]

_INCLUDED_TABLES = [
    "users", "categories", "products", "product_images",
    "discount_codes", "addresses", "orders", "order_items",
    "cart", "wishlists", "reviews", "ai_chat_history",
    "notifications", "payments", "shipments",
]

_SQL_RULES = (
    "You are a read-only SQL assistant for MySQL. "
    "Return exactly one SQL query and nothing else. "
    "The query must be a single SELECT statement. "
    "Do not use markdown, comments, explanations, or multiple statements.\n"
    "Crucial Schema Notes:\n"
    "- The 'orders' table uses 'customer_id' to refer to a user.\n"
    "- Tables like 'cart', 'wishlists', 'addresses', 'reviews', and 'ai_chat_history' use 'user_id'.\n"
    "- Use 'orders.total_amount' for order pricing.\n"
    "- The 'products' table DOES NOT have a 'views' column. To find popular products, JOIN with 'order_items' and count units sold.\n"
    "- The 'users' table has 'id', 'username', and 'email'."
)

_SCHEMA_CACHE_TTL_SECONDS = max(60, int(os.getenv("SAGE_SCHEMA_CACHE_TTL_SECONDS", "300")))
_SCHEMA_INFO_CACHE = {}
_SCHEMA_CACHE_LOCK = threading.Lock()


def _candidate_models() -> list:
    """Deduplicated model list in priority order."""
    api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    seen = set()
    result = []
    for model in _FALLBACK_MODELS:
        model = (model or "").strip()
        if model and model not in seen:
            seen.add(model)
            result.append(model)
    return result


def _make_llm(
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 500,
    request_timeout: int = 14,
) -> ChatOpenAI:
    """Build a ChatOpenAI pointed at OpenRouter for one specific model."""
    api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        openai_api_key=api_key,
        openai_api_base="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://shopy.app",
            "X-Title": "Shopy AI",
        },
        max_tokens=max_tokens,
        request_timeout=request_timeout,
    )


def _llm_invoke_with_failover(
    prompt: str,
    temperature: float = 0.0,
    max_tokens: int = 500,
    request_timeout: int = 14,
) -> str:
    """
    Call .invoke(prompt) on each model in priority order.
    Skips a model automatically on temporary availability failures.
    Raises the last error only when every model has been exhausted.
    """
    last_err = None
    for model in _candidate_models():
        try:
            llm = _make_llm(
                model,
                temperature,
                max_tokens=max_tokens,
                request_timeout=request_timeout,
            )
            raw = llm.invoke(prompt)
            return _coerce_text(raw)
        except Exception as exc:
            msg = str(exc).lower()
            if (
                any(code in msg for code in ["429", "404", "402", "500", "502", "503", "504"])
                or "rate" in msg
                or "unavailable" in msg
                or "timeout" in msg
            ):
                last_err = exc
                continue
            raise

    raise last_err or RuntimeError("All OpenRouter models are currently unavailable. Please try again shortly.")


def _build_sqlalchemy_uri() -> str:
    """
    Build SQLAlchemy URI for read-only database access.
    Enforces non-root credentials for AI query execution.
    """
    host = os.getenv("DB_HOST", "localhost")
    db_name = os.getenv("DB_NAME", "shopy")

    read_user = (os.getenv("READ_DB_USER") or os.getenv("DB_USER") or "").strip()
    read_password = os.getenv("READ_DB_PASSWORD")
    if read_password is None:
        read_password = os.getenv("DB_PASSWORD", "")

    if not read_user:
        raise RuntimeError("Read-only DB user is not configured. Set READ_DB_USER.")
    if read_user.lower() == "root":
        raise RuntimeError("Read-only DB user cannot be root. Configure READ_DB_USER with least privilege.")

    user_enc = quote_plus(read_user)
    pwd_enc = quote_plus(read_password)
    return f"mysql+mysqlconnector://{user_enc}:{pwd_enc}@{host}/{db_name}"


def _build_db_tool(db_uri: str) -> SQLDatabase:
    """Create a SQLDatabase helper with lean schema payloads for lower latency."""
    return SQLDatabase.from_uri(
        db_uri,
        include_tables=_INCLUDED_TABLES,
        sample_rows_in_table_info=0,
    )


def _get_cached_schema_info(db_tool: SQLDatabase, db_uri: str) -> str:
    """Cache table metadata to avoid full introspection on every request."""
    now = time.time()

    with _SCHEMA_CACHE_LOCK:
        cached = _SCHEMA_INFO_CACHE.get(db_uri)
        if cached and cached.get("expires_at", 0) > now:
            return cached["schema_info"]

    schema_info = db_tool.get_table_info(_INCLUDED_TABLES)

    with _SCHEMA_CACHE_LOCK:
        _SCHEMA_INFO_CACHE[db_uri] = {
            "schema_info": schema_info,
            "expires_at": now + _SCHEMA_CACHE_TTL_SECONDS,
        }

    return schema_info


def _coerce_text(raw) -> str:
    """Normalize model output object/string to plain text."""
    if hasattr(raw, "content"):
        return str(raw.content or "")
    return str(raw or "")


def _strip_model_artifacts(text: str) -> str:
    """Strip common model formatting artifacts such as reasoning tags/fences."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"```(?:sql)?", "", cleaned, flags=re.IGNORECASE).replace("```", "").strip()
    return cleaned


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL comments so they don't trigger the security guard."""
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"(--|#).*$", "", sql, flags=re.MULTILINE)
    return sql.strip()


def _extract_sql_candidate(raw_output) -> str:
    """Extract SQL text from model output and keep from first SELECT onward."""
    text = _strip_model_artifacts(_coerce_text(raw_output))
    text = _strip_sql_comments(text)
    text = re.sub(r"^\s*(SQL\s*QUERY|SQLQUERY|QUERY|SQL)\s*:\s*", "", text, flags=re.IGNORECASE)

    match = re.search(r"(?is)\bSELECT\b.*", text)
    if match:
        text = match.group(0)

    return text.strip()


def _validate_select_only(sql: str) -> str:
    """
    Enforce single-statement SELECT-only SQL.
    Validation is performed BEFORE execution.
    """
    candidate = _strip_sql_comments((sql or "").strip().strip("`").strip())
    candidate = candidate.strip('"').strip("'").strip()

    if not candidate:
        raise ValueError("Generated SQL is empty.")

    if ";" in candidate:
        if candidate.count(";") > 1 or not candidate.endswith(";"):
            raise ValueError("Multiple SQL statements are not allowed.")
        candidate = candidate[:-1].strip()

    upper_sql = candidate.upper()
    first_word = upper_sql.split()[0] if upper_sql.split() else ""
    if first_word != "SELECT":
        raise ValueError(f"Generated statement must start with SELECT (got '{first_word or 'EMPTY'}').")

    blocked_keywords = [
        "DROP", "DELETE", "TRUNCATE", "UPDATE", "INSERT", "ALTER", "CREATE", "EXEC", "GRANT", "REVOKE",
    ]
    for keyword in blocked_keywords:
        if re.search(r"\b" + re.escape(keyword) + r"\b", upper_sql):
            raise ValueError(f"Blocked SQL keyword detected: {keyword}")

    return candidate


def _ensure_reasonable_limit(sql: str, default_limit: int = 200) -> str:
    """Guard against oversized result sets by adding LIMIT to non-aggregate SELECTs."""
    candidate = (sql or "").strip()
    upper = candidate.upper()

    if " LIMIT " in upper:
        return candidate

    aggregate_tokens = (" COUNT(", " SUM(", " AVG(", " MIN(", " MAX(")
    if any(token in upper for token in aggregate_tokens):
        return candidate

    return f"{candidate} LIMIT {default_limit}"


def _generate_valid_sql(sql_prompt: str) -> str:
    """Generate SQL and auto-retry once if the model returns malformed output."""
    last_error = None
    prompt = sql_prompt

    for _ in range(2):
        raw_sql = _llm_invoke_with_failover(
            prompt,
            temperature=0.0,
            max_tokens=260,
            request_timeout=10,
        )
        try:
            candidate = _validate_select_only(_extract_sql_candidate(raw_sql))
            return _ensure_reasonable_limit(candidate)
        except ValueError as exc:
            last_error = exc
            prompt = (
                f"{sql_prompt}\n\n"
                "Previous output was invalid. Return exactly one valid SELECT query only."
            )

    raise last_error or ValueError("Could not generate a valid SELECT query.")


def _repair_sql_after_execution_error(
    question: str,
    role_scope: str,
    schema_info: str,
    failed_sql: str,
    execution_error: str,
) -> str:
    """Ask the model for one corrected SELECT when execution fails."""
    repair_prompt = (
        f"{_SQL_RULES}\n\n"
        f"{role_scope}"
        "The previous SQL failed at execution time. Fix it using the schema below.\n"
        "Return exactly one corrected SELECT statement and nothing else.\n\n"
        f"Database schema information:\n{schema_info}\n\n"
        f"User question:\n{question.strip()}\n\n"
        f"Previous SQL:\n{failed_sql}\n\n"
        f"Database error:\n{execution_error}\n\n"
        "Corrected SQL:"
    )

    repaired_raw = _llm_invoke_with_failover(
        repair_prompt,
        temperature=0.0,
        max_tokens=260,
        request_timeout=10,
    )
    repaired = _validate_select_only(_extract_sql_candidate(repaired_raw))
    return _ensure_reasonable_limit(repaired)


def _quick_local_answer(question: str, role: str) -> str:
    """Fast path for non-DB intents so trivial requests return instantly."""
    q = (question or "").strip().lower()
    if not q:
        return ""

    if re.fullmatch(r"(hi|hello|hey|salam|assalam o alaikum|assalamualaikum)[!. ]*", q):
        return "Hello. I am Sage. Ask me anything about your live Shopy data."

    if any(token in q for token in ["who made you", "who built you", "your name", "who are you"]):
        return "I am Sage, the Shopy assistant built by Hammad and Mobeen."

    if q in {"thanks", "thank you", "ok", "okay", "great"}:
        return "You are welcome. I am here whenever you need analytics help."

    if q == "help" or q.startswith("help "):
        if role == "retailer":
            return "You can ask about revenue, top products, pending orders, low stock, and sales trends."
        return "You can ask about product recommendations, prices, stock, ratings, categories, and deals."

    return ""


def _build_resilient_fallback_answer(question: str, role: str, sql: str, db_output: str) -> str:
    """Local fallback summary to keep responses available during model outages."""
    raw = (db_output or "").strip()
    lowered = raw.lower()
    if not raw or lowered in {"[]", "()", "none"} or "no rows returned" in lowered:
        return "I checked the live database and found no matching data for that question right now."

    if role == "retailer":
        prefix = "I ran your request on the live store database and found results."
    else:
        prefix = "I checked the live store data and found this result."

    return (
        f"{prefix}\n\n"
        f"Question: {question.strip()}\n"
        f"SQL used: {sql}\n"
        f"Result snapshot: {raw}"
    )


def _truncate(text: str, max_chars: int = 2200) -> str:
    """Keep debug payload readable in UI."""
    payload = (text or "").strip()
    if len(payload) <= max_chars:
        return payload
    return payload[:max_chars].rstrip() + "\n... (output truncated for readability)"


def ask_sage_langchain(question: str, role: str, user_id: int) -> dict:
    """Generate SQL, validate it, execute safely, and synthesize final answer."""
    generated_sql = ""
    db_output_str = ""
    answer = ""

    try:
        quick = _quick_local_answer(question, role)
        if quick:
            return {
                "query_ran": "(local quick response)",
                "db_output": "No database query was required for this request.",
                "answer": quick,
            }

        db_uri = _build_sqlalchemy_uri()
        db_tool = _build_db_tool(db_uri)
        schema_info = _get_cached_schema_info(db_tool, db_uri)

        role_scope = ""
        if role == "retailer":
            role_scope = (
                f"You are serving retailer user_id={user_id}. "
                f"For product/order analytics, scope results to retailer_id={user_id} unless explicitly asked for store-wide totals. "
            )

        sql_prompt = (
            f"{_SQL_RULES}\n\n"
            f"{role_scope}"
            f"Database schema information:\n{schema_info}\n\n"
            f"User question:\n{question.strip()}\n\n"
            "SQL:"
        )

        generated_sql = _generate_valid_sql(sql_prompt)

        try:
            query_result = db_tool.run(generated_sql)
        except Exception as execution_error:
            repaired_sql = _repair_sql_after_execution_error(
                question=question,
                role_scope=role_scope,
                schema_info=schema_info,
                failed_sql=generated_sql,
                execution_error=str(execution_error),
            )
            generated_sql = repaired_sql
            query_result = db_tool.run(generated_sql)

        db_output_str = _truncate(str(query_result) if query_result is not None else "No rows returned.")

        if role == "retailer":
            persona = "You are Sage, a professional and analytical retail assistant. Provide clear, business-focused insights to the store owner."
        else:
            persona = "You are Sage, a warm and supportive customer care agent for Shopy. Be friendly, helpful, and focused on the shopper's experience."

        summary_prompt = (
            f"{persona}\n"
            "Answer the question using ONLY the SQL result below.\n"
            "If there are no rows, clearly say there is no matching data.\n"
            "Do not mention internal prompts or secrets.\n\n"
            f"User question:\n{question.strip()}\n\n"
            f"Executed SQL:\n{generated_sql}\n\n"
            f"Raw SQL result:\n{db_output_str}\n\n"
            "Final answer:"
        )

        try:
            raw_answer = _llm_invoke_with_failover(
                summary_prompt,
                temperature=0.2,
                max_tokens=420,
                request_timeout=10,
            )
            answer = _strip_model_artifacts(raw_answer) or "No response generated."
        except Exception:
            answer = _build_resilient_fallback_answer(question, role, generated_sql, db_output_str)

    except ValueError as ve:
        generated_sql = generated_sql or "(security guard rejected query)"
        db_output_str = f"Query discarded by security guard: {str(ve)[:200]}"
        answer = "I could not execute that request safely. Please rephrase your question."

    except Exception as exc:
        generated_sql = generated_sql or "(not generated)"
        db_output_str = f"Pipeline error: {str(exc)[:250]}"
        answer = (
            "I could not complete the SQL pipeline for this request, but I am still here to help. "
            "Please rephrase your question in plain business terms and try again."
        )

    return {
        "query_ran": generated_sql,
        "db_output": db_output_str,
        "answer": answer,
    }