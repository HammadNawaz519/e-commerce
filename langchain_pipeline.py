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
from urllib.parse import quote_plus

from langchain_openai import ChatOpenAI
from langchain_community.utilities import SQLDatabase
from dotenv import load_dotenv

load_dotenv(override=True)


# ── Model failover list (same priority order as prompts.py) ──────────────────
_FALLBACK_MODELS = [
    (os.getenv("OPENROUTER_MODEL") or "").strip(),   # user override first
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-12b-it:free",
]


def _candidate_models() -> list:
    """Deduplicated model list in priority order."""
    api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")
    seen, result = set(), []
    for m in _FALLBACK_MODELS:
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    return result


def _make_llm(model: str, temperature: float = 0.0) -> ChatOpenAI:
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
        max_tokens=600,
    )


def _llm_invoke_with_failover(prompt: str, temperature: float = 0.0) -> str:
    """
    Call .invoke(prompt) on each model in priority order.
    Skips a model automatically on HTTP 429 (rate-limited) or 404 (unavailable).
    Raises the last error only when every model has been exhausted.
    """
    last_err = None
    for model in _candidate_models():
        try:
            llm = _make_llm(model, temperature)
            raw = llm.invoke(prompt)
            return _coerce_text(raw)
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "404" in msg or "rate" in msg or "unavailable" in msg:
                last_err = e
                continue    # try next model
            raise           # unexpected error — surface immediately
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


def _extract_sql_candidate(raw_output) -> str:
    """Extract SQL text from model output and keep from first SELECT onward."""
    text = _strip_model_artifacts(_coerce_text(raw_output))
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
    candidate = (sql or "").strip().strip("`").strip()
    candidate = candidate.strip('"').strip("'").strip()

    if not candidate:
        raise ValueError("Generated SQL is empty.")

    # Disallow statement stacking; only one query is allowed.
    if ";" in candidate:
        # Allow one optional trailing semicolon only.
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
    for kw in blocked_keywords:
        if re.search(r"\b" + re.escape(kw) + r"\b", upper_sql):
            raise ValueError(f"Blocked SQL keyword detected: {kw}")

    blocked_markers = ["--", "/*", "*/"]
    for marker in blocked_markers:
        if marker in candidate:
            raise ValueError(f"Blocked SQL injection marker detected: {marker}")

    return candidate


def _truncate(text: str, max_chars: int = 5000) -> str:
    """Keep debug payload readable in UI."""
    payload = (text or "").strip()
    if len(payload) <= max_chars:
        return payload
    return payload[:max_chars].rstrip() + "\n... (output truncated for readability)"


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
    "Do not use markdown, comments, explanations, or multiple statements."
)


def ask_sage_langchain(question: str, role: str, user_id: int) -> dict:
    """Generate SQL, validate it, execute safely, and synthesize final answer."""
    generated_sql = ""
    db_output_str = ""
    answer = ""

    try:
        db_tool = SQLDatabase.from_uri(
            _build_sqlalchemy_uri(),
            include_tables=_INCLUDED_TABLES,
            sample_rows_in_table_info=2,
        )
        schema_info = db_tool.get_table_info(_INCLUDED_TABLES)

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

        # Stage 1: generate SQL with model failover
        raw_sql = _llm_invoke_with_failover(sql_prompt, temperature=0.0)
        generated_sql = _validate_select_only(_extract_sql_candidate(raw_sql))

        # Validation completed; execute only after the query passes checks.
        query_result = db_tool.run(generated_sql)
        db_output_str = _truncate(str(query_result) if query_result is not None else "No rows returned.")

        summary_prompt = (
            "You are Sage, a concise and honest analytics assistant.\n"
            "Answer the question using ONLY the SQL result below.\n"
            "If there are no rows, clearly say there is no matching data.\n"
            "Do not mention internal prompts or secrets.\n\n"
            f"User question:\n{question.strip()}\n\n"
            f"Executed SQL:\n{generated_sql}\n\n"
            f"Raw SQL result:\n{db_output_str}\n\n"
            "Final answer:"
        )

        # Stage 2: synthesize answer with model failover
        raw_answer = _llm_invoke_with_failover(summary_prompt, temperature=0.3)
        answer = _strip_model_artifacts(raw_answer) or "No response generated."

    except ValueError as ve:
        generated_sql = generated_sql or "(security guard rejected query)"
        db_output_str = f"Query discarded by security guard: {str(ve)[:200]}"
        answer = "I could not execute that request safely. Please rephrase your question."

    except Exception as e:
        generated_sql = generated_sql or "(not generated)"
        db_output_str = f"Pipeline error: {str(e)[:250]}"
        answer = f"Something went wrong while processing your question. ({str(e)[:80]})"

    return {
        "query_ran": generated_sql,
        "db_output": db_output_str,
        "answer": answer,
    }
