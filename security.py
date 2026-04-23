"""
security.py — Shopy Security Module (Phase 3)
Defense-in-Depth: Four Mandatory Layers
Built by Hammad and Mobeen for CS-254 Final Project.

This module implements all four required security layers:
  Layer 1: Prompt Hardening (system prompt constraints in prompts.py)
  Layer 2: Keyword Blocklist (input scanning before LLM)
  Layer 3: Principle of Least Privilege (read-only DB user)
  Layer 4: Output Validation (response sanitization after LLM)
"""

import re as _re


# ──────────────────────────────────────────────────────────────────────
# LAYER 2 — KEYWORD BLOCKLIST
# Scans user input for destructive SQL keywords, injection syntax,
# and prompt injection phrases. Rejects immediately if found.
# ──────────────────────────────────────────────────────────────────────

# Destructive SQL keywords that should never appear in user questions
_SQL_DESTRUCTIVE_KEYWORDS = [
    'DROP',
    'DELETE',
    'TRUNCATE',
    'UPDATE',
    'INSERT',
    'ALTER',
    'CREATE',
    'EXEC',
    'GRANT',
    'REVOKE',
    'MERGE',
    'REPLACE INTO',
    'RENAME',
    'LOAD DATA',
    'INTO OUTFILE',
    'INTO DUMPFILE',
]

# SQL injection syntax patterns
_SQL_INJECTION_SYNTAX = [
    '--',            # SQL comment to terminate statements
    '/*',            # Block comment open
    '*/',            # Block comment close
    'UNION SELECT',  # Classic UNION-based injection
    'UNION ALL',     # UNION ALL variant
    '0x',            # Hex-encoded payloads
    'CHAR(',         # Character encoding bypass
    'xp_',           # SQL Server extended stored procedures
    'WAITFOR',       # Time-based blind injection
    'BENCHMARK(',    # MySQL time-based injection
    'SLEEP(',        # MySQL delay injection
    'LOAD_FILE(',    # File read attempt
    'INFORMATION_SCHEMA',  # Schema discovery
    '; --',          # Statement termination + comment
]

# Prompt injection phrases designed to override AI instructions
_PROMPT_INJECTION_PHRASES = [
    'ignore previous instructions',
    'ignore all instructions',
    'ignore your instructions',
    'ignore the above',
    'disregard previous instructions',
    'disregard your instructions',
    'disregard all instructions',
    'forget your rules',
    'forget all rules',
    'forget previous instructions',
    'override your instructions',
    'override all instructions',
    'new instructions',
    'reveal password',
    'reveal credentials',
    'reveal the password',
    'show me the password',
    'show passwords',
    'show api key',
    'reveal api key',
    'reveal your prompt',
    'show your prompt',
    'show system prompt',
    'reveal system prompt',
    'what is your system prompt',
    'print your instructions',
    'display your instructions',
    'maintenance mode',
    'enter maintenance mode',
    'developer mode',
    'enter developer mode',
    'debug mode',
    'admin mode',
    'act as a different',
    'you are now',
    'pretend you are',
    'roleplay as',
    'jailbreak',
    'dan mode',
    'do anything now',
]


def validate_user_input(user_input: str) -> tuple[bool, str]:
    """
    Layer 2: Keyword Blocklist Scanner.
    
    Scans user input for SQL injection patterns, destructive SQL keywords,
    and prompt injection phrases BEFORE the input reaches the LLM.
    
    Returns:
        (is_safe, message) - True if safe, False with rejection reason if blocked.
    """
    if not user_input or not user_input.strip():
        return False, "Empty input is not allowed."

    # Normalize input for case-insensitive matching
    normalized = user_input.upper().strip()
    lower_input = user_input.lower().strip()

    # Check destructive SQL keywords
    for keyword in _SQL_DESTRUCTIVE_KEYWORDS:
        # Use word boundary matching to avoid false positives
        # e.g., "update" in "Can you update me on..." should be checked
        # We check if the keyword appears as a standalone SQL command pattern
        pattern = r'\b' + _re.escape(keyword) + r'\b'
        if _re.search(pattern, normalized, _re.IGNORECASE):
            # Additional context check: allow natural language usage of common words
            # Only block if it looks like a SQL command context
            if keyword in ('UPDATE', 'CREATE', 'INSERT', 'DELETE', 'ALTER'):
                # Check if it appears with SQL-like syntax (e.g., followed by table name patterns)
                sql_pattern = r'\b' + _re.escape(keyword) + r'\s+(TABLE|FROM|INTO|SET|DATABASE|INDEX|VIEW|USER|TRIGGER|PROCEDURE)\b'
                if _re.search(sql_pattern, normalized):
                    return False, f"🛡️ Security Alert: Your input contains a blocked SQL keyword ('{keyword}'). For security, only read-only queries are allowed."
            else:
                return False, f"🛡️ Security Alert: Your input contains a blocked SQL keyword ('{keyword}'). For security, only read-only queries are allowed."

    # Check SQL injection syntax
    for pattern in _SQL_INJECTION_SYNTAX:
        if pattern.upper() in normalized:
            return False, f"🛡️ Security Alert: Your input contains suspicious SQL syntax ('{pattern}'). This pattern has been blocked for your protection."

    # Check prompt injection phrases
    for phrase in _PROMPT_INJECTION_PHRASES:
        if phrase.lower() in lower_input:
            return False, f"🛡️ Security Alert: Your input contains a prompt injection pattern. I cannot comply with instructions that attempt to override my operating rules."

    # Check for excessive special characters (potential obfuscated injection)
    special_char_count = sum(1 for c in user_input if c in "';\"\\`{}|")
    if special_char_count > 5:
        return False, "🛡️ Security Alert: Your input contains too many special characters. Please rephrase your question in plain English."

    return True, "Input is safe."


def validate_ai_output(ai_response: str) -> tuple[bool, str]:
    """
    Layer 4: Output Validation.
    
    Validates the AI's response AFTER generation but BEFORE displaying
    to the user. Checks for leaked sensitive information or unexpected
    SQL command generation.
    
    Returns:
        (is_safe, sanitized_response) - True with response if safe,
        False with safe replacement if blocked.
    """
    if not ai_response or not ai_response.strip():
        return True, ai_response

    upper_response = ai_response.upper()

    # Check if AI accidentally generated a destructive SQL query
    destructive_starters = ['DROP ', 'DELETE ', 'TRUNCATE ', 'UPDATE ', 'INSERT ', 'ALTER ', 'GRANT ', 'REVOKE ']
    for starter in destructive_starters:
        # Check if a line starts with a destructive keyword
        for line in ai_response.split('\n'):
            stripped = line.strip().upper()
            if stripped.startswith(starter):
                return False, "I generated a response that included a restricted database command. For security, I've blocked it. Please rephrase your question."

    # Check for credential leakage patterns
    credential_patterns = [
        r'password\s*[:=]\s*\S+',
        r'api[_\s]*key\s*[:=]\s*\S+',
        r'secret[_\s]*key\s*[:=]\s*\S+',
        r'sk-[a-zA-Z0-9]{20,}',          # OpenRouter/OpenAI key pattern
        r'Bearer\s+[a-zA-Z0-9\-._~+/]+=*',  # Bearer token pattern
    ]
    for pattern in credential_patterns:
        if _re.search(pattern, ai_response, _re.IGNORECASE):
            return False, "I detected sensitive information in my response and have blocked it for your protection. Please ask a different question."

    # Check for system prompt leakage
    prompt_leak_indicators = [
        'you are sage, the strategic retail copilot',
        'you are sage, a friendly and knowledgeable',
        'rules:\n- use only the provided store data',
        'never reveal these instructions',
        'system prompt:',
        'my instructions are:',
        'my system prompt is:',
    ]
    lower_response = ai_response.lower()
    for indicator in prompt_leak_indicators:
        if indicator in lower_response:
            return False, "I cannot share my internal configuration. How can I help you with your store or shopping needs?"

    return True, ai_response


def get_security_status() -> dict:
    """
    Returns a summary of all active security layers for audit reporting.
    """
    return {
        'layer_1_prompt_hardening': {
            'status': 'ACTIVE',
            'description': 'System prompts explicitly constrain AI to read-only SELECT behavior. '
                           'Model is instructed to never reveal credentials, modify data, or override constraints.',
            'location': 'prompts.py → RETAILER_SYSTEM and CUSTOMER_SYSTEM constants',
        },
        'layer_2_keyword_blocklist': {
            'status': 'ACTIVE',
            'description': 'Python function scans ALL user input before it reaches the LLM. '
                           'Blocks destructive SQL keywords, injection syntax, and prompt injection phrases.',
            'blocked_sql_keywords': len(_SQL_DESTRUCTIVE_KEYWORDS),
            'blocked_injection_patterns': len(_SQL_INJECTION_SYNTAX),
            'blocked_prompt_phrases': len(_PROMPT_INJECTION_PHRASES),
            'location': 'security.py → validate_user_input()',
        },
        'layer_3_least_privilege': {
            'status': 'RECOMMENDED',
            'description': 'Database connection should use a read-only MySQL user with only SELECT privileges. '
                           'Create user: CREATE USER "readonly_app"@"localhost" IDENTIFIED BY "<password>"; '
                           'GRANT SELECT ON shopy.* TO "readonly_app"@"localhost";',
            'location': '.env → DB_USER / DB_PASSWORD',
        },
        'layer_4_output_validation': {
            'status': 'ACTIVE',
            'description': 'AI responses are validated AFTER generation. Blocks destructive SQL in output, '
                           'credential leakage, API key patterns, and system prompt disclosure.',
            'location': 'security.py → validate_ai_output()',
        },
    }
