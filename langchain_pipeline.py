"""
langchain_pipeline.py  —  Shopy Phase 2: LangChain AI Pipeline
================================================================
Phase 2 — AI Logic & Architecture (CS-254 Final Project, Group 28)

LangChain Framework Implementation:
  - ChatGroq LLM via LangChain's official langchain-groq integration
  - PromptTemplate for structured metadata injection
  - LLMChain for chaining LLM calls
  - RunnableSequence (LCEL) for the full text-to-SQL pipeline
  - Fallback chain for SQL repair and answer synthesis

Pipeline Flow (5 mandatory stages per rubric §4.4):
  Stage 1: User Input  — question received from Gradio / Flask
  Stage 2: Metadata Injection — schema is injected into PromptTemplate
  Stage 3: Query Generation  — LLMChain generates a SELECT statement
  Stage 4: Safe Execution    — validate_select() guards + mysql.connector runs it
  Stage 5: Synthesis & Response — second LLMChain summarises raw results

Defense in Depth (Layer 3 — Principle of Least Privilege):
  The pipeline ONLY uses READ_DB_USER (readonly_app) which has SELECT-only
  permissions. Even a successful injection cannot modify data.
"""

import os
import re
import threading

import mysql.connector
from dotenv import load_dotenv

# ── LangChain imports ─────────────────────────────────────────────────────────
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

load_dotenv(override=True)


# =============================================================================
# SCHEMA — Metadata Injection (Phase 2, Stage 2)
# The full database schema is embedded into every LLM prompt so the model
# knows exactly which tables and columns exist in the Shopy database.
# =============================================================================
SHOPY_SCHEMA = """
Table: users           — id (PK), username, email, role, phone_number, bio, created_at
Table: categories      — id (PK), name, slug, description, is_active, created_at
Table: products        — id (PK), name, description, price, original_price, stock, sku, weight_grams, is_active, retailer_id (FK→users), category_id (FK→categories), created_at
Table: product_images  — id (PK), product_id (FK→products), image_url, alt_text, sort_order, is_primary, uploaded_by_user_id (FK→users), created_at
Table: discount_codes  — id (PK), code, campaign_name, discount_type (percent/fixed), discount_value, min_order_value, max_uses, uses_count, expires_at, is_active, created_at
Table: addresses       — id (PK), user_id (FK→users), label, full_name, phone, address_line, city, province, postal_code, country, is_default, created_at
Table: orders          — id (PK), customer_id (FK→users), total_amount, status, shipping_name, shipping_address, shipping_phone, notes, discount_code_id (FK→discount_codes), discount_amount, payment_method, payment_status, payment_ref, created_at
Table: order_items     — id (PK), order_id (FK→orders), product_id (FK→products), retailer_id (FK→users), product_name, quantity, unit_price, discount_per_unit
Table: cart            — id (PK), user_id (FK→users), product_id (FK→products), quantity, added_at
Table: wishlists       — id (PK), user_id (FK→users), product_id (FK→products), priority (1-5), target_price, notify_on_price_drop, note, added_at
Table: reviews         — id (PK), product_id (FK→products), user_id (FK→users), rating (1-5), title, comment, is_verified_purchase, created_at
Table: ai_chat_history — id (PK), user_id (FK→users), role, sender (user/bot), message, created_at
Table: notifications   — id (PK), user_id (FK→users), message, type, channel, action_url, is_read, created_at
Table: payments        — id (PK), order_id (FK→orders), payment_method, provider, amount_paid, payment_status, transaction_ref, paid_at, created_at
Table: shipments       — id (PK), order_id (FK→orders), courier_name, tracking_number, shipment_status, shipped_at, expected_delivery, delivered_at, shipping_cost, created_at

Key Relationships:
- orders.customer_id        → users.id
- orders.discount_code_id   → discount_codes.id
- order_items.order_id      → orders.id
- order_items.product_id    → products.id
- order_items.retailer_id   → users.id
- payments.order_id         → orders.id
- shipments.order_id        → orders.id
- products.retailer_id      → users.id
- products.category_id      → categories.id
- product_images.product_id → products.id
- reviews.product_id        → products.id
- reviews.user_id           → users.id
- addresses.user_id         → users.id
- cart.user_id              → users.id
- cart.product_id           → products.id
- wishlists.user_id         → users.id
- wishlists.product_id      → products.id
- notifications.user_id     → users.id
- ai_chat_history.user_id   → users.id

Schema Rules (IMPORTANT — strictly follow these):
- The 'products' table has NO 'views' column. Use COUNT(order_items) for popularity.
- Use orders.total_amount for order revenue; use payments.amount_paid for actual payment amounts.
- Retailer-specific analytics: always filter by products.retailer_id or order_items.retailer_id.
- To find shipping info, JOIN shipments ON shipments.order_id = orders.id.
- To find payment details, JOIN payments ON payments.order_id = orders.id.
- discount_codes.discount_type is either 'percent' or 'fixed'; discount_codes.discount_value holds the amount/percentage.
- orders.status values: 'pending', 'processing', 'shipped', 'delivered', 'cancelled'.
- shipments.shipment_status values: 'pending', 'packed', 'in_transit', 'delivered'.
- payments.payment_status values: 'pending', 'paid', 'refunded'.
- Only use tables and columns listed above. Do not invent columns.
""".strip()


# =============================================================================
# LLM INITIALISATION — LangChain ChatGroq
# Using LangChain's official Groq integration (langchain-groq package).
# Primary model: llama-3.3-70b-specdec (best SQL accuracy on Groq free tier)
# Fallback model: llama-3.1-8b-instant (always available)
# =============================================================================
def _build_llm(model: str, temperature: float = 0.0, max_tokens: int = 300) -> ChatGroq:
    """Instantiate a LangChain ChatGroq LLM for the given model."""
    return ChatGroq(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=os.getenv("GROQ_API_KEY", ""),
    )

_PRIMARY_MODEL  = os.getenv("GROQ_MODEL", "").strip() or "llama-3.3-70b-specdec"
_FALLBACK_MODEL = "llama-3.1-8b-instant"


# =============================================================================
# PROMPT TEMPLATES — LangChain PromptTemplate (Metadata Injection)
# These templates inject the full database schema into every LLM call,
# fulfilling Phase 2 Stage 2: Metadata Injection.
# =============================================================================

# Stage 3 — SQL Generation prompt
SQL_GENERATION_TEMPLATE = PromptTemplate(
    input_variables=["schema", "role_scope", "question"],
    template=(
        "You are a read-only SQL assistant for MySQL. "
        "Your ONLY job is to return exactly ONE valid SQL SELECT statement.\n"
        "Rules:\n"
        "- Return ONLY the raw SQL. No markdown, no code fences, no explanations.\n"
        "- The query MUST start with SELECT.\n"
        "- Never generate DROP, DELETE, UPDATE, INSERT, ALTER, CREATE, GRANT, or REVOKE.\n"
        "- Use only the tables and columns from the schema below.\n\n"
        "Database Schema:\n{schema}\n\n"
        "{role_scope}"
        "User Question: {question}\n\n"
        "SQL:"
    ),
)

# Stage 5 — Answer Synthesis prompt
SYNTHESIS_TEMPLATE = PromptTemplate(
    input_variables=["persona", "question", "sql", "db_result"],
    template=(
        "{persona}\n\n"
        "Instructions:\n"
        "- Answer ONLY using the SQL query result provided below.\n"
        "- Be concise and business-focused.\n"
        "- If the result is empty, say no data was found.\n"
        "- Never reveal SQL or system internals in your answer.\n\n"
        "User Question: {question}\n"
        "SQL Executed: {sql}\n"
        "Database Result:\n{db_result}\n\n"
        "Answer:"
    ),
)

# SQL Repair prompt (used when Stage 4 execution fails)
SQL_REPAIR_TEMPLATE = PromptTemplate(
    input_variables=["schema", "role_scope", "question", "bad_sql", "error"],
    template=(
        "You are a read-only SQL assistant for MySQL. "
        "The SQL below failed. Fix it and return ONLY the corrected SELECT statement.\n"
        "No markdown, no explanations — just raw SQL.\n\n"
        "Database Schema:\n{schema}\n\n"
        "{role_scope}"
        "User Question: {question}\n"
        "Failed SQL: {bad_sql}\n"
        "Error: {error}\n\n"
        "Corrected SQL:"
    ),
)


# =============================================================================
# DATABASE — Principle of Least Privilege (Phase 3, Layer 3)
# Uses READ_DB_USER (readonly_app) — SELECT permissions only.
# Even a successful injection cannot mutate or delete data.
# =============================================================================
_DB_CONN_CACHE: dict = {}
_DB_CONN_LOCK = threading.Lock()


def _get_readonly_conn():
    """Return a cached read-only mysql.connector connection (readonly_app user)."""
    host     = os.getenv("DB_HOST", "localhost")
    db_name  = os.getenv("DB_NAME", "shopy")
    user     = (os.getenv("READ_DB_USER") or os.getenv("DB_USER") or "").strip()
    password = os.getenv("READ_DB_PASSWORD") or os.getenv("DB_PASSWORD", "")

    cache_key = f"{user}@{host}/{db_name}"
    with _DB_CONN_LOCK:
        conn = _DB_CONN_CACHE.get(cache_key)
        if conn:
            try:
                conn.ping(reconnect=True, attempts=2, delay=0)
                return conn
            except Exception:
                pass
        conn = mysql.connector.connect(
            host=host, database=db_name, user=user, password=password,
            connection_timeout=5, autocommit=True,
        )
        _DB_CONN_CACHE[cache_key] = conn
        return conn


def _run_select(sql: str) -> str:
    """Execute a validated SELECT and return results as a readable string."""
    conn = _get_readonly_conn()
    cur  = conn.cursor(dictionary=True)
    cur.execute(sql)
    rows = cur.fetchmany(200)
    cur.close()
    if not rows:
        return "No rows returned."
    return "\n".join(", ".join(f"{k}={v}" for k, v in row.items()) for row in rows)


# =============================================================================
# SECURITY — Output Validation (Phase 3, Layer 4)
# Validates that the LLM output is a pure SELECT before DB execution.
# =============================================================================
def _validate_select(sql: str) -> str:
    """
    Layer 4 — Output Validation:
    Asserts the generated query starts with SELECT and contains no DML keywords.
    Raises ValueError if the query is blocked.
    """
    candidate = sql.strip().strip("`").strip('"').strip("'")

    # Strip trailing semicolon (allow single statement only)
    if ";" in candidate:
        if candidate.count(";") > 1 or not candidate.endswith(";"):
            raise ValueError("Multiple SQL statements are not allowed.")
        candidate = candidate[:-1].strip()

    if not candidate:
        raise ValueError("Generated SQL is empty.")

    first_word = candidate.upper().split()[0] if candidate.split() else ""
    if first_word != "SELECT":
        raise ValueError(
            f"Security guard blocked query — must start with SELECT (got '{first_word or 'EMPTY'}')."
        )

    # Block any destructive keywords inside the query
    blocked_keywords = [
        "DROP", "DELETE", "TRUNCATE", "UPDATE", "INSERT",
        "ALTER", "CREATE", "EXEC", "GRANT", "REVOKE", "MERGE",
    ]
    for kw in blocked_keywords:
        if re.search(r"\b" + kw + r"\b", candidate.upper()):
            raise ValueError(f"Blocked SQL keyword detected: {kw}")

    # Block queries that select sensitive columns directly (Layer 4 — Output Validation)
    sensitive_columns = ["password", "api_key", "secret_key", "auth_token", "private_key"]
    candidate_lower = candidate.lower()
    for col in sensitive_columns:
        # Detect SELECT <col> or SELECT ..., <col>, ... patterns
        if re.search(r"\bselect\b.*\b" + re.escape(col) + r"\b", candidate_lower, re.DOTALL):
            raise ValueError(
                f"Security guard blocked query — selecting '{col}' column is not permitted."
            )

    return candidate


def _add_limit(sql: str, n: int = 200) -> str:
    """Append LIMIT clause if the query has no LIMIT and is not an aggregate."""
    upper = sql.upper()
    if " LIMIT " in upper:
        return sql
    agg_keywords = (" COUNT(", " SUM(", " AVG(", " MIN(", " MAX(")
    if any(a in upper for a in agg_keywords):
        return sql
    return f"{sql} LIMIT {n}"


# =============================================================================
# SQL EXTRACTION — Clean LLM output artifacts
# =============================================================================
def _strip_artifacts(text: str) -> str:
    """Remove <think> blocks and markdown code fences from LLM output."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    t = re.sub(r"```(?:sql)?", "", t, flags=re.IGNORECASE).replace("```", "").strip()
    return t


def _extract_sql(raw: str) -> str:
    """Extract the SQL SELECT statement from LLM output."""
    text = _strip_artifacts(str(raw))
    # Remove label prefixes like "SQL:", "Query:"
    text = re.sub(r"^[\s]*(SQL\s*QUERY|SQLQUERY|QUERY|SQL)\s*:\s*", "", text, flags=re.IGNORECASE)
    # Remove inline SQL comments
    text = re.sub(r"(--|#).*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Find the SELECT statement
    m = re.search(r"(?is)\bSELECT\b.*", text)
    return (m.group(0) if m else text).strip()


def _truncate(text: str, n: int = 2500) -> str:
    return text[:n].rstrip() + "\n...(truncated)" if len(text) > n else text


# =============================================================================
# QUICK LOCAL ANSWERS — No LLM or DB needed for trivial inputs
# =============================================================================
def _quick_local_answer(question: str, role: str) -> str:
    q = question.strip().lower()
    if re.fullmatch(r"(hi|hello|hey|salam|assalam[\w ]*)[!. ]*", q):
        return "Hello! I'm Sage, Shopy's AI analytics assistant. Ask me anything about your live data."
    if any(t in q for t in ["who made you", "who built you", "your name", "who are you"]):
        return "I'm Sage, built by Hammad and Mobeen (Group 28) for the CS-254 Final Project."
    if q in {"thanks", "thank you", "ok", "okay", "great"}:
        return "You're welcome! I'm here whenever you need analytics insights."
    if q == "help" or q.startswith("help "):
        return (
            "Ask me about revenue, top products, pending orders, or low stock."
            if role == "retailer" else
            "Ask me about products, prices, ratings, categories, or deals."
        )
    return ""


# =============================================================================
# LANGCHAIN CHAINS — LLMChain instances (Phase 2 core implementation)
# =============================================================================

def _invoke_sql_chain(question: str, role_scope: str, model: str) -> str:
    """
    Stage 2 + 3: Metadata Injection → Query Generation using LangChain LLMChain.
    
    LangChain components used:
      - PromptTemplate (SQL_GENERATION_TEMPLATE) injects schema into prompt
      - ChatGroq LLM generates the SQL SELECT statement
      - StrOutputParser extracts the string from the AIMessage
    """
    llm = _build_llm(model=model, temperature=0.0, max_tokens=260)

    # LangChain Expression Language (LCEL) chain: prompt | llm | parser
    chain = SQL_GENERATION_TEMPLATE | llm | StrOutputParser()

    return chain.invoke({
        "schema": SHOPY_SCHEMA,
        "role_scope": role_scope,
        "question": question.strip(),
    })


def _invoke_repair_chain(question: str, role_scope: str, bad_sql: str, error: str, model: str) -> str:
    """Repair chain: ask LLM to fix a failed SQL using LangChain."""
    llm = _build_llm(model=model, temperature=0.0, max_tokens=260)
    chain = SQL_REPAIR_TEMPLATE | llm | StrOutputParser()
    return chain.invoke({
        "schema": SHOPY_SCHEMA,
        "role_scope": role_scope,
        "question": question.strip(),
        "bad_sql": bad_sql,
        "error": error[:300],
    })


def _invoke_synthesis_chain(
    question: str, sql: str, db_result: str, role: str, model: str
) -> str:
    """
    Stage 5: Synthesis & Response using LangChain LLMChain.
    
    LangChain components used:
      - PromptTemplate (SYNTHESIS_TEMPLATE) structures the summarisation request
      - ChatGroq LLM synthesises a plain-English answer from raw DB results
      - StrOutputParser extracts the string
    """
    persona = (
        "You are Sage, a professional retail analytics assistant for Shopy. "
        "Give concise, business-focused insights with numbers."
        if role == "retailer" else
        "You are Sage, a friendly Shopy customer assistant. "
        "Be warm, helpful, and clear."
    )
    llm = _build_llm(model=model, temperature=0.2, max_tokens=360)
    chain = SYNTHESIS_TEMPLATE | llm | StrOutputParser()
    return chain.invoke({
        "persona": persona,
        "question": question.strip(),
        "sql": sql,
        "db_result": db_result,
    })


def _generate_sql_with_langchain(question: str, role_scope: str) -> str:
    """
    Generate and validate a SELECT query using LangChain chains.
    Tries primary model first, falls back to secondary, then repairs once on failure.
    """
    last_err = None
    for model in [_PRIMARY_MODEL, _FALLBACK_MODEL]:
        try:
            raw = _invoke_sql_chain(question, role_scope, model)
            sql = _extract_sql(raw)
            return _add_limit(_validate_select(sql))
        except Exception as e:
            last_err = e
            continue

    # One more retry with an explicit correction hint on fallback model
    try:
        retry_raw = _invoke_sql_chain(
            question + "\n\nIMPORTANT: Return ONLY a raw SELECT statement, no other text.",
            role_scope,
            _FALLBACK_MODEL,
        )
        sql = _extract_sql(retry_raw)
        return _add_limit(_validate_select(sql))
    except Exception as e:
        last_err = e

    raise last_err or ValueError("Could not generate a valid SELECT statement.")


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================
def ask_sage_langchain(question: str, role: str, user_id: int) -> dict:
    """
    Main LangChain pipeline entry point — called by app.py.
    
    Implements all 5 mandatory stages from Phase 2, §4.4:
      Stage 1: User Input         — 'question' parameter
      Stage 2: Metadata Injection — SHOPY_SCHEMA injected via PromptTemplate
      Stage 3: Query Generation   — LangChain ChatGroq + LLMChain
      Stage 4: Safe Execution     — _validate_select() + _run_select() (readonly_app)
      Stage 5: Synthesis          — LangChain ChatGroq summarises raw DB result

    Returns:
        dict with keys: query_ran, db_output, answer
    """
    generated_sql = ""
    db_output_str = ""
    answer        = ""

    try:
        # ── Stage 1: User Input ───────────────────────────────────────────────
        # Quick local answer for greetings / trivial queries (no LLM needed)
        quick = _quick_local_answer(question, role)
        if quick:
            return {
                "query_ran": "(local response — no DB query needed)",
                "db_output": "No database query required.",
                "answer":    quick,
            }

        # ── Stage 2: Metadata Injection (via PromptTemplate role scope) ───────
        role_scope = ""
        if role == "retailer":
            role_scope = (
                f"You are serving retailer user_id={user_id}. "
                f"Filter product and order results to retailer_id={user_id} "
                f"unless the user explicitly asks for store-wide totals.\n\n"
            )

        # ── Stage 3: Query Generation (LangChain LLMChain) ───────────────────
        generated_sql = _generate_sql_with_langchain(question, role_scope)

        # ── Stage 4: Safe Execution (read-only DB + validate_select guard) ───
        try:
            db_output_str = _truncate(_run_select(generated_sql))
        except Exception as exec_err:
            # SQL ran but failed — ask LangChain to repair it
            for model in [_PRIMARY_MODEL, _FALLBACK_MODEL]:
                try:
                    raw_repair = _invoke_repair_chain(
                        question, role_scope,
                        bad_sql=generated_sql,
                        error=str(exec_err),
                        model=model,
                    )
                    repaired_sql = _add_limit(_validate_select(_extract_sql(raw_repair)))
                    db_output_str = _truncate(_run_select(repaired_sql))
                    generated_sql = repaired_sql
                    break
                except Exception:
                    continue
            else:
                raise exec_err  # Both repair attempts failed

        # ── Stage 5: Synthesis & Response (LangChain LLMChain) ───────────────
        try:
            raw_answer = _invoke_synthesis_chain(
                question, generated_sql, db_output_str, role, _PRIMARY_MODEL
            )
            answer = _strip_artifacts(raw_answer) or "No response generated."
        except Exception:
            # Fallback to secondary model for synthesis
            try:
                raw_answer = _invoke_synthesis_chain(
                    question, generated_sql, db_output_str, role, _FALLBACK_MODEL
                )
                answer = _strip_artifacts(raw_answer) or "No response generated."
            except Exception:
                # Last resort: return raw data without LLM synthesis
                if db_output_str.strip() in {"No rows returned.", ""}:
                    answer = "I checked the live database and found no matching data for that question."
                else:
                    answer = f"Here is the live database result:\n\n{db_output_str}"

    except ValueError as ve:
        # Security guard rejection (Layer 2 or Layer 4)
        generated_sql = generated_sql or "(security guard rejected the query)"
        db_output_str = f"Query blocked by security layer: {str(ve)[:300]}"
        answer        = "I could not execute that safely. Please rephrase your question in plain business terms."

    except Exception as exc:
        generated_sql = generated_sql or "(not generated)"
        db_output_str = f"Pipeline error: {str(exc)[:300]}"
        answer        = (
            "I couldn't complete that request right now. "
            "Please try rephrasing your question in plain business terms."
        )

    return {
        "query_ran": generated_sql,
        "db_output": db_output_str,
        "answer":    answer,
    }