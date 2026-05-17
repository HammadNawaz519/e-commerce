"""
langchain_pipeline.py  —  Shopy Direct SQL Pipeline (Phase 2 Optimized)
=========================================================================
Zero LangChain. Zero SQLAlchemy. Pure native mysql.connector.

Flow:
  1. Quick local answer check (instant).
  2. Build schema from hardcoded table definitions (no DB round-trip).
  3. Ask LLM to generate a SELECT query.
  4. Validate SELECT-only (security guard).
  5. Execute directly via mysql.connector on read-only credentials.
  6. Ask LLM to synthesize a plain-English answer.
"""

import os
import re
import threading
import time

import mysql.connector
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

# ── Quality / token budgets ───────────────────────────────────────────────────
_SAGE_QUALITY = (os.getenv("SAGE_QUALITY") or "fast").strip().lower()
if _SAGE_QUALITY not in ("fast", "medium"):
    _SAGE_QUALITY = "fast"

# ── Model lists ───────────────────────────────────────────────────────────────
_GROQ_MODELS_FAST   = ["llama-3.1-8b-instant", "llama-3.3-70b-specdec", "gemma2-9b-it"]
_GROQ_MODELS_MEDIUM = ["llama-3.3-70b-specdec", "llama-3.1-70b-versatile", "llama-3.1-8b-instant"]
_GROK_MODELS        = ["grok-2-1212", "grok-beta"]
_OPENROUTER_MODELS  = [
    (os.getenv("OPENROUTER_MODEL") or "").strip(),
    "openrouter/free",
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
]

# ── Hardcoded schema (no DB introspection needed at query time) ───────────────
_HARDCODED_SCHEMA = """
Table: users          — id, username, email, role, created_at
Table: categories     — id, name, description
Table: products       — id, name, price, original_price, stock, is_active, retailer_id, category_id, created_at
Table: product_images — id, product_id, image_url, is_primary
Table: orders         — id, customer_id, status, total_amount, created_at
Table: order_items    — id, order_id, product_id, retailer_id, quantity, unit_price
Table: cart           — id, user_id, product_id, quantity
Table: wishlists      — id, user_id, product_id
Table: reviews        — id, product_id, user_id, rating, comment, created_at
Table: addresses      — id, user_id, street, city, country
Table: payments       — id, order_id, amount, status, method, created_at
Table: shipments      — id, order_id, status, tracking_number, updated_at
Table: discount_codes — id, code, discount_percent, is_active
Table: notifications  — id, user_id, message, is_read, created_at
Table: ai_chat_history— id, user_id, role_type, question, answer, created_at

Key relationships:
- orders.customer_id  → users.id
- order_items.order_id → orders.id  |  order_items.product_id → products.id
- products.retailer_id → users.id   |  products.category_id  → categories.id
- reviews.product_id  → products.id |  reviews.user_id        → users.id
- cart / wishlists / addresses / notifications  use  user_id → users.id

Schema Rules:
- 'products' has NO 'views' column. Use order_items COUNT for popularity.
- Use orders.total_amount for order value.
- Retailer-specific queries: filter products/order_items by retailer_id.
""".strip()

_SQL_RULES = (
    "You are a read-only SQL assistant for MySQL. "
    "Return exactly one SQL SELECT statement and nothing else. "
    "No markdown, no code fences, no comments, no explanations.\n\n"
    "Schema:\n" + _HARDCODED_SCHEMA
)

# ── DB connection cache (one connection per process) ──────────────────────────
_DB_CONN_CACHE: dict = {}
_DB_CONN_LOCK  = threading.Lock()

# ── Schema string cache (already hardcoded, kept for API compat) ──────────────
_SCHEMA_INFO_CACHE: dict = {}
_SCHEMA_CACHE_LOCK = threading.Lock()
_SCHEMA_CACHE_TTL  = 300


def _get_readonly_conn():
    """Return a cached read-only mysql.connector connection."""
    host     = os.getenv("DB_HOST",         "localhost")
    db_name  = os.getenv("DB_NAME",         "shopy")
    user     = (os.getenv("READ_DB_USER")   or os.getenv("DB_USER") or "").strip()
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
    """Execute a validated SELECT and return result as a readable string."""
    conn = _get_readonly_conn()
    cur  = conn.cursor(dictionary=True)
    cur.execute(sql)
    rows = cur.fetchmany(200)
    cur.close()
    if not rows:
        return "No rows returned."
    lines = []
    for row in rows:
        lines.append(", ".join(f"{k}={v}" for k, v in row.items()))
    return "\n".join(lines)


# ── Active provider detection ─────────────────────────────────────────────────
def _active_provider() -> str:
    if (os.getenv("GROQ_API_KEY") or "").strip():
        return "groq"
    if (os.getenv("GROK_API_KEY") or "").strip():
        return "grok"
    if (os.getenv("OPENROUTER_API_KEY") or "").strip():
        return "openrouter"
    raise RuntimeError("No AI provider configured. Set GROQ_API_KEY or OPENROUTER_API_KEY.")


def _candidate_models() -> list:
    provider = _active_provider()
    if provider == "groq":
        pool = _GROQ_MODELS_MEDIUM if _SAGE_QUALITY == "medium" else _GROQ_MODELS_FAST
        override = (os.getenv("GROQ_MODEL") or "").strip()
    elif provider == "grok":
        pool = _GROK_MODELS
        override = (os.getenv("GROK_MODEL") or "").strip()
    else:
        pool = _OPENROUTER_MODELS
        override = ""

    seen, result = set(), []
    for m in ([override] if override else []) + pool:
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m); result.append(m)
    if not result:
        raise RuntimeError(f"No models available for provider: {provider}")
    return result


def _token_budget(kind: str) -> int:
    if kind == "sql":
        return 260 if _SAGE_QUALITY == "medium" else 180
    return 360 if _SAGE_QUALITY == "medium" else 240


# ── Core LLM caller ───────────────────────────────────────────────────────────
def _llm_invoke_with_failover(prompt: str, temperature: float = 0.0,
                               max_tokens: int = 300, request_timeout: int = 10) -> str:
    provider = _active_provider()
    models   = _candidate_models()
    last_err = None

    for model in models:
        try:
            if provider == "groq":
                url     = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {os.getenv('GROQ_API_KEY','').strip()}",
                           "Content-Type": "application/json"}
            elif provider == "grok":
                url     = "https://api.x.ai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {os.getenv('GROK_API_KEY','').strip()}",
                           "Content-Type": "application/json"}
            else:
                url     = "https://openrouter.ai/api/v1/chat/completions"
                headers = {"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY','').strip()}",
                           "Content-Type": "application/json",
                           "HTTP-Referer": "https://shopy.app", "X-Title": "Shopy AI"}

            payload = {"model": model,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": temperature, "max_tokens": max_tokens}

            resp = requests.post(url, headers=headers, json=payload, timeout=request_timeout)

            if resp.status_code != 200:
                last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                continue

            rj  = resp.json()
            msg = rj["choices"][0]["message"]
            txt = msg.get("content") or msg.get("reasoning") or ""
            return str(txt).strip()

        except Exception as exc:
            s = str(exc).lower()
            if any(c in s for c in ["429","404","402","500","502","503","504","timeout","unavailable","rate"]):
                last_err = exc; continue
            raise

    raise last_err or RuntimeError("All AI models exhausted.")


# ── SQL helpers ───────────────────────────────────────────────────────────────
def _strip_artifacts(text: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    t = re.sub(r"```(?:sql)?", "", t, flags=re.IGNORECASE).replace("```", "").strip()
    return t


def _extract_sql(raw) -> str:
    text = _strip_artifacts(str(raw))
    text = re.sub(r"^[\s]*(SQL\s*QUERY|SQLQUERY|QUERY|SQL)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(--|#).*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    m = re.search(r"(?is)\bSELECT\b.*", text)
    return (m.group(0) if m else text).strip()


def _validate_select(sql: str) -> str:
    candidate = sql.strip().strip("`").strip('"').strip("'")
    if ";" in candidate:
        if candidate.count(";") > 1 or not candidate.endswith(";"):
            raise ValueError("Multiple SQL statements are not allowed.")
        candidate = candidate[:-1].strip()
    if not candidate:
        raise ValueError("Generated SQL is empty.")
    first = candidate.upper().split()[0] if candidate.split() else ""
    if first != "SELECT":
        raise ValueError(f"Generated statement must start with SELECT (got '{first or 'EMPTY'}').")
    blocked = ["DROP","DELETE","TRUNCATE","UPDATE","INSERT","ALTER","CREATE","EXEC","GRANT","REVOKE"]
    for kw in blocked:
        if re.search(r"\b" + kw + r"\b", candidate.upper()):
            raise ValueError(f"Blocked keyword: {kw}")
    return candidate


def _add_limit(sql: str, n: int = 200) -> str:
    upper = sql.upper()
    if " LIMIT " in upper:
        return sql
    agg = (" COUNT("," SUM("," AVG("," MIN("," MAX(")
    if any(a in upper for a in agg):
        return sql
    return f"{sql} LIMIT {n}"


def _generate_sql(sql_prompt: str) -> str:
    last_err = None
    prompt   = sql_prompt
    for _ in range(2):
        raw = _llm_invoke_with_failover(prompt, temperature=0.0,
                                        max_tokens=_token_budget("sql"), request_timeout=8)
        try:
            return _add_limit(_validate_select(_extract_sql(raw)))
        except ValueError as e:
            last_err = e
            prompt = sql_prompt + "\n\nPrevious output was invalid. Return exactly one SELECT query only."
    raise last_err or ValueError("Could not generate a valid SELECT.")


# ── Quick local answers ───────────────────────────────────────────────────────
def _quick_local_answer(question: str, role: str) -> str:
    q = question.strip().lower()
    if re.fullmatch(r"(hi|hello|hey|salam|assalam[\w ]*)[!. ]*", q):
        return "Hello! I'm Sage. Ask me anything about your live Shopy data."
    if any(t in q for t in ["who made you","who built you","your name","who are you"]):
        return "I'm Sage, the Shopy AI assistant built by Hammad and Mobeen."
    if q in {"thanks","thank you","ok","okay","great"}:
        return "You're welcome! I'm here whenever you need analytics help."
    if q == "help" or q.startswith("help "):
        return ("You can ask about revenue, top products, pending orders, and low stock."
                if role == "retailer" else
                "You can ask about products, prices, ratings, categories, and deals.")
    return ""


# ── Fallback synthesizer (no LLM) ────────────────────────────────────────────
def _local_fallback(question: str, sql: str, db_out: str) -> str:
    if not db_out or db_out.strip() in {"No rows returned.", "[]", ""}:
        return "I checked the live database and found no matching data for that question."
    return f"Here is the live result:\n\n{db_out}\n\n(SQL: {sql})"


def _truncate(text: str, n: int = 2500) -> str:
    return text[:n].rstrip() + "\n...(truncated)" if len(text) > n else text


# ── Public entry point ────────────────────────────────────────────────────────
def ask_sage_langchain(question: str, role: str, user_id: int) -> dict:
    """Generate SQL, validate, execute, synthesize. Returns {query_ran, db_output, answer}."""
    generated_sql = ""
    db_output_str = ""
    answer        = ""

    try:
        # 1. Quick local check
        quick = _quick_local_answer(question, role)
        if quick:
            return {"query_ran": "(local response — no DB query needed)",
                    "db_output": "No database query required.", "answer": quick}

        # 2. Role scope
        role_scope = ""
        if role == "retailer":
            role_scope = (f"You are serving retailer user_id={user_id}. "
                          f"Scope product/order results to retailer_id={user_id} "
                          f"unless asked for store-wide totals.\n")

        # 3. Build SQL generation prompt (schema already hardcoded — no DB round-trip)
        sql_prompt = (
            f"{_SQL_RULES}\n\n"
            f"{role_scope}"
            f"User question: {question.strip()}\n\n"
            "SQL:"
        )

        # 4. Generate + validate SQL
        generated_sql = _generate_sql(sql_prompt)

        # 5. Execute directly
        try:
            db_output_str = _truncate(_run_select(generated_sql))
        except Exception as exec_err:
            # One repair attempt
            repair_prompt = (
                f"{_SQL_RULES}\n\n{role_scope}"
                f"The previous SQL failed. Fix it.\n"
                f"Failed SQL: {generated_sql}\n"
                f"Error: {str(exec_err)[:200]}\n"
                f"User question: {question.strip()}\n\nCorrected SQL:"
            )
            raw2 = _llm_invoke_with_failover(repair_prompt, temperature=0.0,
                                             max_tokens=_token_budget("sql"), request_timeout=8)
            generated_sql = _add_limit(_validate_select(_extract_sql(raw2)))
            db_output_str = _truncate(_run_select(generated_sql))

        # 6. Synthesize answer
        persona = ("You are Sage, a professional retail analytics assistant. "
                   "Give concise, business-focused insights."
                   if role == "retailer" else
                   "You are Sage, a friendly Shopy customer care agent. "
                   "Be warm and helpful.")

        summary_prompt = (
            f"{persona}\n"
            f"Answer ONLY using the SQL result below. "
            f"If no rows, say no data found.\n\n"
            f"Question: {question.strip()}\n"
            f"SQL: {generated_sql}\n"
            f"Result:\n{db_output_str}\n\n"
            "Answer:"
        )

        try:
            raw_ans = _llm_invoke_with_failover(summary_prompt, temperature=0.2,
                                                max_tokens=_token_budget("summary"),
                                                request_timeout=10)
            answer = _strip_artifacts(raw_ans) or "No response generated."
        except Exception:
            answer = _local_fallback(question, generated_sql, db_output_str)

    except ValueError as ve:
        generated_sql = generated_sql or "(security guard rejected query)"
        db_output_str = f"Query discarded by security guard: {str(ve)[:200]}"
        answer        = "I could not execute that safely. Please rephrase your question."

    except Exception as exc:
        generated_sql = generated_sql or "(not generated)"
        db_output_str = f"Pipeline error: {str(exc)[:250]}"
        answer        = ("I couldn't complete that request right now. "
                         "Please try rephrasing in plain business terms.")

    return {"query_ran": generated_sql, "db_output": db_output_str, "answer": answer}